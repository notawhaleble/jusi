from __future__ import annotations

from queue import SimpleQueue
from threading import Thread
import time
import uuid
from typing import List, Optional

from jusi.application.errors import SessionError, SessionNotFoundError
from jusi.application.ports import AttachSessionCommand, ExecuteCellCommand, HandlerMessageCommand, HealthcheckReplyCommand, InputReplyCommand, InterruptCellCommand, StartSessionCommand
from jusi.application.ports import DisconnectSessionCommand, ReconnectSessionCommand, StopSessionCommand
from jusi.application.ports import ShutdownClientCommand
from jusi.application.use_cases import AttachSession, DisconnectSession, ExecuteCell, HandlerMessage, HealthcheckReply, InputReply, InterruptCell, ReconnectSession, ShutdownClient, StopSession, StartSession
from jusi.domain.models import ExecutableCell
from jusi.domain.models import ClientTransport
from jusi.infrastructure.debug_timing import emit_timing
from jusi.infrastructure.runtime import InMemoryKernelRuntime, InMemorySessionStore, build_runtime
from jusi.interfaces.protocol import (
    Envelope,
    ProtocolError,
    dump_envelopes,
    error_response,
    parse_attach_session,
    parse_disconnect_session,
    parse_envelope,
    parse_execute_cell,
    parse_handler_message,
    parse_healthcheck_reply,
    parse_input_reply,
    parse_inspect_client,
    parse_interrupt_cell,
    parse_reconnect_session,
    parse_shutdown_client,
    parse_start_session,
    parse_stop_session,
    response_envelope,
)
from jusi.plugins import DisplayHandlerRegistry, DisplayHandlerRuntime, build_display_handler_registry

FRONTEND_HEALTHCHECK_INTERVAL_SECONDS = 5.0
FRONTEND_HEALTHCHECK_REPLY_TTL_SECONDS = 10.0


class ProtocolEventSink:
    def __init__(self) -> None:
        self._events: List[Envelope] = []

    @property
    def events(self) -> List[Envelope]:
        return list(self._events)

    def session_updated(self, notebook_id: str, payload: dict) -> None:
        self._events.append(
            Envelope(
                version=1,
                kind="event",
                type="session_updated",
                payload={"notebook_id": notebook_id, "session": payload},
            )
        )

    def cell_updated(self, notebook_id: str, payload: dict) -> None:
        self._events.append(
            Envelope(
                version=1,
                kind="event",
                type="cell_updated",
                payload={"notebook_id": notebook_id, "cell": payload},
            )
        )

    def handler_message(
        self,
        notebook_id: str,
        session_id: str,
        client_id: str,
        handler_id: str,
        message_type: str,
        payload: dict,
    ) -> None:
        self._events.append(
            Envelope(
                version=1,
                kind="event",
                type="handler_message",
                payload={
                    "notebook_id": notebook_id,
                    "session_id": session_id,
                    "client_id": client_id,
                    "handler_id": handler_id,
                    "message_type": message_type,
                    "payload": payload,
                },
            )
        )

    def client_updated(self, notebook_id: str, session_id: str, client_id: str, revision: int) -> None:
        self._events.append(
            Envelope(
                version=1,
                kind="event",
                type="client_updated",
                payload={
                    "notebook_id": notebook_id,
                    "session_id": session_id,
                    "client_id": client_id,
                    "revision": revision,
                },
            )
        )

    def healthcheck(self, notebook_id: str, session_id: str, healthcheck_id: str) -> None:
        self._events.append(
            Envelope(
                version=1,
                kind="event",
                type="healthcheck",
                payload={
                    "notebook_id": notebook_id,
                    "session_id": session_id,
                    "healthcheck_id": healthcheck_id,
                },
            )
        )


class ProtocolServer:
    def __init__(
        self,
        runtime: Optional[InMemoryKernelRuntime] = None,
        display_handlers: Optional[DisplayHandlerRegistry] = None,
    ) -> None:
        self._runtime = runtime or build_runtime()
        self._store = InMemorySessionStore()
        self._pending_events: SimpleQueue[Envelope] = SimpleQueue()
        self._display_handlers = display_handlers or build_display_handler_registry()
        self._active_handlers = DisplayHandlerRuntime()
        self._client_revisions: dict[tuple[str, str], int] = {}
        self._closed = False

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _queue_handler_message(
        self,
        notebook_id: str,
        session_id: str,
        client_id: str,
        handler_id: str,
        message_type: str,
        payload: dict,
    ) -> None:
        self._pending_events.put(
            Envelope(
                version=1,
                kind="event",
                type="handler_message",
                payload={
                    "notebook_id": notebook_id,
                    "session_id": session_id,
                    "client_id": client_id,
                    "handler_id": handler_id,
                    "message_type": message_type,
                    "payload": payload,
                },
            )
        )

    def drain_pending_messages(self) -> List[str]:
        envelopes: List[Envelope] = []
        while True:
            try:
                envelopes.append(self._pending_events.get_nowait())
            except Exception:
                break
        if not envelopes:
            return []
        return dump_envelopes(envelopes)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._active_handlers.stop_all()
        close_runtime = getattr(self._runtime, "close", None)
        if callable(close_runtime):
            close_runtime()

    def poll_session_timeouts(self) -> bool:
        now = time.time()
        for session in self._store.list_sessions():
            shared_deadline = self._runtime.sync_disconnect_deadline(session, session.expires_at)
            if shared_deadline != session.expires_at:
                session.expires_at = shared_deadline
                self._store.save(session)
            deadline = session.expires_at
            if deadline is None or deadline > now:
                continue
            self._runtime.expire_session(session)
            self._active_handlers.remove_session(session.session_id)
            session.state = "stopped"
            session.last_action = "timeout"
            session.last_error = "session_expired"
            session.expires_at = None
            self._store.save(session)
            return True
        return False

    def poll_frontend_health(self) -> None:
        now = time.time()
        for session in self._store.list_sessions():
            if session.state != "connected":
                continue
            if session.frontend_healthcheck_id:
                deadline = session.frontend_healthcheck_deadline
                if deadline is None or deadline > now:
                    continue
                events = ProtocolEventSink()
                use_case = DisconnectSession(runtime=self._runtime, store=self._store, events=events)
                use_case.execute(
                    DisconnectSessionCommand(
                        notebook_id=session.notebook_id,
                        session_id=session.session_id,
                        reason="frontend_unreachable",
                    )
                )
                self._active_handlers.remove_session(session.session_id)
                for event in events.events:
                    self._pending_events.put(event)
                continue
            last_ack_at = session.frontend_last_ack_at
            if last_ack_at is None or (now - last_ack_at) < FRONTEND_HEALTHCHECK_INTERVAL_SECONDS:
                continue
            healthcheck_id = f"hc-{uuid.uuid4().hex[:12]}"
            session.frontend_healthcheck_id = healthcheck_id
            session.frontend_healthcheck_deadline = now + FRONTEND_HEALTHCHECK_REPLY_TTL_SECONDS
            self._store.save(session)
            self._pending_events.put(
                Envelope(
                    version=1,
                    kind="event",
                    type="healthcheck",
                    payload={
                        "notebook_id": session.notebook_id,
                        "session_id": session.session_id,
                        "healthcheck_id": healthcheck_id,
                    },
                )
            )

    def poll_client_updates(self) -> None:
        list_clients = getattr(self._runtime, "list_clients", None)
        if not callable(list_clients):
            return
        live_keys: set[tuple[str, str]] = set()
        for session in self._store.list_sessions():
            for runtime_client in list_clients(session.session_id):
                key = (session.session_id, runtime_client.client_id)
                live_keys.add(key)
                try:
                    view = self._runtime.read_client_view(session, runtime_client.client_id)
                except Exception:
                    continue
                revision = int(view.get("revision", 0))
                previous_revision = self._client_revisions.get(key)
                if previous_revision == revision:
                    continue
                self._client_revisions[key] = revision
                if previous_revision is None:
                    continue
                self._pending_events.put(
                    Envelope(
                        version=1,
                        kind="event",
                        type="client_updated",
                        payload={
                            "notebook_id": session.notebook_id,
                            "session_id": session.session_id,
                            "client_id": runtime_client.client_id,
                            "revision": revision,
                        },
                    )
                )
        stale_keys = [key for key in self._client_revisions if key not in live_keys]
        for key in stale_keys:
            self._client_revisions.pop(key, None)

    def handle_message(self, raw: str) -> List[str]:
        request = parse_envelope(raw)
        if request.kind != "request":
            raise ProtocolError("Server expects request envelopes")
        try:
            if request.type == "start_session":
                return self._handle_start_session(request)
            if request.type == "execute_cell":
                return self._handle_execute_cell(request)
            if request.type == "attach_session":
                return self._handle_attach_session(request)
            if request.type == "interrupt_cell":
                return self._handle_interrupt_cell(request)
            if request.type == "disconnect_session":
                return self._handle_disconnect_session(request)
            if request.type == "reconnect_session":
                return self._handle_reconnect_session(request)
            if request.type == "stop_session":
                return self._handle_stop_session(request)
            if request.type == "shutdown_client":
                return self._handle_shutdown_client(request)
            if request.type == "inspect_client":
                return self._handle_inspect_client(request)
            if request.type == "input_reply":
                return self._handle_input_reply(request)
            if request.type == "healthcheck_reply":
                return self._handle_healthcheck_reply(request)
            if request.type == "handler_message":
                return self._handle_handler_message(request)
            return dump_envelopes([error_response(request, "unknown_request", "Unknown request type")])
        except ProtocolError as exc:
            return dump_envelopes([error_response(request, "invalid_request", str(exc))])
        except SessionError as exc:
            return dump_envelopes([error_response(request, exc.code, str(exc))])
        except Exception as exc:
            return dump_envelopes([error_response(request, "internal_error", str(exc))])

    def _handle_start_session(self, request: Envelope) -> List[str]:
        start_request = parse_start_session(request.payload)
        events = ProtocolEventSink()
        use_case = StartSession(runtime=self._runtime, store=self._store, events=events)
        use_case.execute(
            StartSessionCommand(
                notebook_id=start_request.notebook_id,
                kernel_name=start_request.kernel_name,
                target=start_request.target,
            )
        )
        envelopes = [response_envelope(request, ok=True)]
        envelopes.extend(events.events)
        return dump_envelopes(envelopes)

    def _handle_attach_session(self, request: Envelope) -> List[str]:
        attach_request = parse_attach_session(request.payload)
        events = ProtocolEventSink()
        use_case = AttachSession(runtime=self._runtime, store=self._store, events=events)
        try:
            use_case.execute(
                AttachSessionCommand(
                    notebook_id=attach_request.notebook_id,
                    target=attach_request.target,
                )
            )
        except SessionError as exc:
            return dump_envelopes([error_response(request, exc.code, str(exc))])
        except ValueError as exc:
            return dump_envelopes([error_response(request, "invalid_state", str(exc))])
        envelopes = [response_envelope(request, ok=True)]
        envelopes.extend(events.events)
        return dump_envelopes(envelopes)

    def _handle_interrupt_cell(self, request: Envelope) -> List[str]:
        interrupt_request = parse_interrupt_cell(request.payload)
        events = ProtocolEventSink()
        use_case = InterruptCell(
            runtime=self._runtime,
            store=self._store,
            events=events,
            active_handlers=self._active_handlers,
        )
        try:
            use_case.execute(
                InterruptCellCommand(
                    notebook_id=interrupt_request.notebook_id,
                    session_id=interrupt_request.session_id,
                    cell_id=interrupt_request.cell_id,
                )
            )
        except SessionError as exc:
            return dump_envelopes([error_response(request, exc.code, str(exc))])
        except ValueError as exc:
            return dump_envelopes([error_response(request, "invalid_state", str(exc))])
        envelopes = [response_envelope(request, ok=True)]
        envelopes.extend(events.events)
        return dump_envelopes(envelopes)

    def _handle_disconnect_session(self, request: Envelope) -> List[str]:
        disconnect_request = parse_disconnect_session(request.payload)
        events = ProtocolEventSink()
        use_case = DisconnectSession(runtime=self._runtime, store=self._store, events=events)
        try:
            use_case.execute(
                DisconnectSessionCommand(
                    notebook_id=disconnect_request.notebook_id,
                    session_id=disconnect_request.session_id,
                    reason=disconnect_request.reason,
                )
            )
        except SessionError as exc:
            return dump_envelopes([error_response(request, exc.code, str(exc))])
        except ValueError as exc:
            return dump_envelopes([error_response(request, "invalid_state", str(exc))])
        self._active_handlers.remove_session(disconnect_request.session_id)
        envelopes = [response_envelope(request, ok=True)]
        envelopes.extend(events.events)
        return dump_envelopes(envelopes)

    def _handle_reconnect_session(self, request: Envelope) -> List[str]:
        reconnect_request = parse_reconnect_session(request.payload)
        events = ProtocolEventSink()
        use_case = ReconnectSession(runtime=self._runtime, store=self._store, events=events)
        try:
            use_case.execute(
                ReconnectSessionCommand(
                    notebook_id=reconnect_request.notebook_id,
                    session_id=reconnect_request.session_id,
                )
            )
        except SessionError as exc:
            return dump_envelopes([error_response(request, exc.code, str(exc))])
        except ValueError as exc:
            return dump_envelopes([error_response(request, "invalid_state", str(exc))])
        envelopes = [response_envelope(request, ok=True)]
        envelopes.extend(events.events)
        return dump_envelopes(envelopes)

    def _handle_execute_cell(self, request: Envelope) -> List[str]:
        execute_request = parse_execute_cell(request.payload)
        emit_timing(
            "server.execute_cell.request",
            notebook_id=execute_request.notebook_id,
            session_id=execute_request.session_id,
            cell_id=execute_request.cell_id,
            kind=execute_request.kind,
            syntax=execute_request.syntax,
        )
        events = ProtocolEventSink()
        use_case = ExecuteCell(
            runtime=self._runtime,
            store=self._store,
            events=events,
            display_handlers=self._display_handlers,
            active_handlers=self._active_handlers,
            handler_message_sink=events.handler_message,
            live_handler_message_sink=self._queue_handler_message,
        )
        try:
            command = ExecuteCellCommand(
                notebook_id=execute_request.notebook_id,
                session_id=execute_request.session_id,
                cell=ExecutableCell(
                    cell_id=execute_request.cell_id,
                    kind=execute_request.kind,
                    syntax=execute_request.syntax,
                    main_lines=execute_request.main_lines,
                    keep_running=execute_request.keep_running,
                ),
            )
            if self._supports_background_execute():
                session, current_client = use_case.begin_execute(command)
                emit_timing(
                    "server.execute_cell.begin_done",
                    notebook_id=execute_request.notebook_id,
                    session_id=execute_request.session_id,
                    cell_id=execute_request.cell_id,
                    client_id=current_client.client_id,
                    owner_kind=current_client.owner_kind,
                    status=current_client.status,
                )
                self._spawn_execute_completion(command, session, current_client)
            else:
                use_case.execute(command)
        except SessionError as exc:
            return dump_envelopes([error_response(request, exc.code, str(exc))])
        except ValueError as exc:
            return dump_envelopes([error_response(request, "invalid_state", str(exc))])
        envelopes = [response_envelope(request, ok=True)]
        envelopes.extend(events.events)
        return dump_envelopes(envelopes)

    def _supports_background_execute(self) -> bool:
        method = getattr(self._runtime, "supports_background_execute", None)
        if not callable(method):
            return False
        try:
            return bool(method())
        except Exception:
            return False

    def _spawn_execute_completion(self, command: ExecuteCellCommand, session, current_client) -> None:  # type: ignore[no-untyped-def]
        def _run() -> None:
            emit_timing(
                "server.execute_cell.finish_start",
                notebook_id=command.notebook_id,
                session_id=session.session_id,
                cell_id=current_client.cell_id,
                client_id=current_client.client_id,
            )
            events = ProtocolEventSink()
            use_case = ExecuteCell(
                runtime=self._runtime,
                store=self._store,
                events=events,
                display_handlers=self._display_handlers,
                active_handlers=self._active_handlers,
                handler_message_sink=events.handler_message,
                live_handler_message_sink=self._queue_handler_message,
            )
            try:
                use_case.finish_execute(command, session, current_client)
            except Exception as exc:
                emit_timing(
                    "server.execute_cell.finish_error",
                    notebook_id=command.notebook_id,
                    session_id=session.session_id,
                    cell_id=current_client.cell_id,
                    client_id=current_client.client_id,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                for event in events.events:
                    self._pending_events.put(event)
                return
            emit_timing(
                "server.execute_cell.finish_done",
                notebook_id=command.notebook_id,
                session_id=session.session_id,
                cell_id=current_client.cell_id,
                client_id=current_client.client_id,
                final_status=current_client.status,
                owner_kind=current_client.owner_kind,
            )
            for event in events.events:
                self._pending_events.put(event)

        Thread(target=_run, daemon=True).start()

    def _handle_stop_session(self, request: Envelope) -> List[str]:
        stop_request = parse_stop_session(request.payload)
        events = ProtocolEventSink()
        use_case = StopSession(runtime=self._runtime, store=self._store, events=events)
        try:
            command = StopSessionCommand(
                notebook_id=stop_request.notebook_id,
                session_id=stop_request.session_id,
            )
            session = use_case.begin_stop(command)
            self._spawn_stop_completion(command, session)
        except SessionError as exc:
            return dump_envelopes([error_response(request, exc.code, str(exc))])
        except ValueError as exc:
            return dump_envelopes([error_response(request, "invalid_state", str(exc))])
        envelopes = [response_envelope(request, ok=True)]
        envelopes.extend(events.events)
        return dump_envelopes(envelopes)

    def _spawn_stop_completion(self, command: StopSessionCommand, session) -> None:  # type: ignore[no-untyped-def]
        def _run() -> None:
            events = ProtocolEventSink()
            use_case = StopSession(runtime=self._runtime, store=self._store, events=events)
            try:
                use_case.finish_stop(command, session)
            except Exception:
                return
            self._active_handlers.remove_session(command.session_id)
            for event in events.events:
                self._pending_events.put(event)

        Thread(target=_run, daemon=True).start()

    def _handle_shutdown_client(self, request: Envelope) -> List[str]:
        shutdown_request = parse_shutdown_client(request.payload)
        events = ProtocolEventSink()
        use_case = ShutdownClient(runtime=self._runtime, store=self._store, events=events)
        try:
            use_case.execute(
                ShutdownClientCommand(
                    notebook_id=shutdown_request.notebook_id,
                    session_id=shutdown_request.session_id,
                    cell_id=shutdown_request.cell_id,
                    client_id=shutdown_request.client_id,
                    reason=shutdown_request.reason,
                )
            )
        except SessionError as exc:
            return dump_envelopes([error_response(request, exc.code, str(exc))])
        except ValueError as exc:
            return dump_envelopes([error_response(request, "invalid_state", str(exc))])
        self._active_handlers.remove_client(shutdown_request.session_id, shutdown_request.client_id)
        envelopes = [response_envelope(request, ok=True)]
        envelopes.extend(events.events)
        return dump_envelopes(envelopes)

    def _handle_inspect_client(self, request: Envelope) -> List[str]:
        inspect_request = parse_inspect_client(request.payload)
        emit_timing(
            "server.inspect_client.request",
            notebook_id=inspect_request.notebook_id,
            session_id=inspect_request.session_id,
            client_id=inspect_request.client_id,
        )
        session = self._store.get_by_notebook(inspect_request.notebook_id)
        if session is None or session.session_id != inspect_request.session_id:
            return dump_envelopes([error_response(request, SessionNotFoundError.code, "Unknown notebook session")])
        try:
            client_view = self._runtime.read_client_view(session, inspect_request.client_id)
        except ValueError as exc:
            return dump_envelopes([error_response(request, "invalid_state", str(exc))])
        self._normalize_dead_handler_client(
            inspect_request.notebook_id,
            inspect_request.session_id,
            inspect_request.client_id,
        )
        emit_timing(
            "server.inspect_client.response",
            notebook_id=inspect_request.notebook_id,
            session_id=inspect_request.session_id,
            client_id=inspect_request.client_id,
            revision=int(client_view.get("revision", 0)),
            line_count=len(list(client_view.get("lines", []))),
        )
        return dump_envelopes([response_envelope(request, ok=True, payload={"client": client_view})])

    def _normalize_dead_handler_client(self, notebook_id: str, session_id: str, client_id: str) -> None:
        runtime_client = self._runtime.get_client(session_id, client_id)
        if runtime_client is None:
            return
        handle = getattr(runtime_client, "handle", None)
        is_alive = getattr(handle, "plugin_runtime_is_alive", None)
        if not callable(is_alive):
            return
        if is_alive() is not False:
            return
        session = self._store.get_by_notebook(notebook_id)
        if session is None or session.session_id != session_id:
            return
        self._runtime.shutdown_client(session, client_id, "plugin_runtime_exit")
        self._active_handlers.remove_client(session_id, client_id)
        for execution in self._store.list_executions(notebook_id):
            if execution.client_id != client_id:
                continue
            if execution.status == "follow-up":
                execution.status = "done"
                execution.owner_kind = "unknown"
            execution.client_state = "shutdown"
            execution.client_bufnr = -1
            execution.client_id = ""
            execution.transport = ClientTransport()
            self._store.save_execution(notebook_id, execution)
            self._pending_events.put(
                Envelope(
                    version=1,
                    kind="event",
                    type="cell_updated",
                    payload={"notebook_id": notebook_id, "cell": {
                        "id": execution.cell_id,
                        "status": execution.status,
                        "owner": {"kind": execution.owner_kind},
                        "client_state": execution.client_state,
                    }},
                )
            )

    def _handle_input_reply(self, request: Envelope) -> List[str]:
        input_request = parse_input_reply(request.payload)
        events = ProtocolEventSink()
        use_case = InputReply(runtime=self._runtime, store=self._store, events=events)
        try:
            command = InputReplyCommand(
                notebook_id=input_request.notebook_id,
                session_id=input_request.session_id,
                cell_id=input_request.cell_id,
                client_id=input_request.client_id,
                value=input_request.value,
            )
            if self._supports_background_execute():
                session, execution = use_case.begin_reply(command)
                self._spawn_input_reply_completion(command, session, execution)
            else:
                use_case.execute(command)
        except SessionError as exc:
            return dump_envelopes([error_response(request, exc.code, str(exc))])
        except ValueError as exc:
            return dump_envelopes([error_response(request, "invalid_state", str(exc))])
        envelopes = [response_envelope(request, ok=True)]
        envelopes.extend(events.events)
        return dump_envelopes(envelopes)

    def _spawn_input_reply_completion(self, command: InputReplyCommand, session, execution) -> None:  # type: ignore[no-untyped-def]
        def _run() -> None:
            events = ProtocolEventSink()
            use_case = InputReply(runtime=self._runtime, store=self._store, events=events)
            try:
                use_case.finish_reply(command, session, execution)
            except Exception:
                return
            for event in events.events:
                self._pending_events.put(event)

        Thread(target=_run, daemon=True).start()

    def _handle_healthcheck_reply(self, request: Envelope) -> List[str]:
        healthcheck_request = parse_healthcheck_reply(request.payload)
        use_case = HealthcheckReply(store=self._store)
        try:
            use_case.execute(
                HealthcheckReplyCommand(
                    notebook_id=healthcheck_request.notebook_id,
                    session_id=healthcheck_request.session_id,
                    healthcheck_id=healthcheck_request.healthcheck_id,
                )
            )
        except SessionError as exc:
            return dump_envelopes([error_response(request, exc.code, str(exc))])
        except ValueError as exc:
            return dump_envelopes([error_response(request, "invalid_state", str(exc))])
        return dump_envelopes([response_envelope(request, ok=True)])

    def _handle_handler_message(self, request: Envelope) -> List[str]:
        handler_request = parse_handler_message(request.payload)
        emit_timing(
            "server.handler_message.request",
            notebook_id=handler_request.notebook_id,
            session_id=handler_request.session_id,
            client_id=handler_request.client_id,
            handler_id=handler_request.handler_id,
            message_type=handler_request.message_type,
        )
        use_case = HandlerMessage(store=self._store, active_handlers=self._active_handlers)
        try:
            use_case.execute(
                HandlerMessageCommand(
                    notebook_id=handler_request.notebook_id,
                    session_id=handler_request.session_id,
                    client_id=handler_request.client_id,
                    handler_id=handler_request.handler_id,
                    message_type=handler_request.message_type,
                    payload=handler_request.payload,
                )
            )
        except SessionError as exc:
            emit_timing(
                "server.handler_message.error",
                notebook_id=handler_request.notebook_id,
                session_id=handler_request.session_id,
                client_id=handler_request.client_id,
                handler_id=handler_request.handler_id,
                message_type=handler_request.message_type,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return dump_envelopes([error_response(request, exc.code, str(exc))])
        except ValueError as exc:
            emit_timing(
                "server.handler_message.error",
                notebook_id=handler_request.notebook_id,
                session_id=handler_request.session_id,
                client_id=handler_request.client_id,
                handler_id=handler_request.handler_id,
                message_type=handler_request.message_type,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return dump_envelopes([error_response(request, "invalid_state", str(exc))])
        emit_timing(
            "server.handler_message.done",
            notebook_id=handler_request.notebook_id,
            session_id=handler_request.session_id,
            client_id=handler_request.client_id,
            handler_id=handler_request.handler_id,
            message_type=handler_request.message_type,
        )
        return dump_envelopes([response_envelope(request, ok=True)])
