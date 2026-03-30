from __future__ import annotations

from queue import SimpleQueue
from threading import Thread
import time
import uuid
from typing import List, Optional

from jusi.application.errors import SessionError, SessionNotFoundError
from jusi.application.ports import AttachSessionCommand, BindPreparedClientCommand, ExecuteCellCommand, HealthcheckReplyCommand, InputReplyCommand, InterruptCellCommand, StartSessionCommand
from jusi.application.ports import DisconnectSessionCommand, ReconnectSessionCommand, StopSessionCommand
from jusi.application.ports import ShutdownClientCommand
from jusi.application.use_cases import AttachSession, BindPreparedClient, DisconnectSession, ExecuteCell, HealthcheckReply, InputReply, InterruptCell, ReconnectSession, ShutdownClient, StopSession, StartSession
from jusi.domain.models import ExecutableCell
from jusi.infrastructure.runtime import InMemoryKernelRuntime, InMemorySessionStore, build_runtime
from jusi.interfaces.protocol import (
    Envelope,
    ProtocolError,
    dump_envelopes,
    error_response,
    parse_attach_session,
    parse_bind_prepared_client,
    parse_disconnect_session,
    parse_envelope,
    parse_execute_cell,
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

    def prepared_updated(self, notebook_id: str, payload: dict) -> None:
        self._events.append(
            Envelope(
                version=1,
                kind="event",
                type="prepared_updated",
                payload={"notebook_id": notebook_id, "prepared": payload},
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
    def __init__(self, runtime: Optional[InMemoryKernelRuntime] = None) -> None:
        self._runtime = runtime or build_runtime()
        self._store = InMemorySessionStore()
        self._pending_events: SimpleQueue[Envelope] = SimpleQueue()

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
            if request.type == "bind_prepared_client":
                return self._handle_bind_prepared_client(request)
            if request.type == "shutdown_client":
                return self._handle_shutdown_client(request)
            if request.type == "inspect_client":
                return self._handle_inspect_client(request)
            if request.type == "input_reply":
                return self._handle_input_reply(request)
            if request.type == "healthcheck_reply":
                return self._handle_healthcheck_reply(request)
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
        use_case = InterruptCell(runtime=self._runtime, store=self._store, events=events)
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
        events = ProtocolEventSink()
        use_case = ExecuteCell(runtime=self._runtime, store=self._store, events=events)
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
            events = ProtocolEventSink()
            use_case = ExecuteCell(runtime=self._runtime, store=self._store, events=events)
            try:
                use_case.finish_execute(command, session, current_client)
            except Exception:
                return
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
            for event in events.events:
                self._pending_events.put(event)

        Thread(target=_run, daemon=True).start()

    def _handle_bind_prepared_client(self, request: Envelope) -> List[str]:
        bind_request = parse_bind_prepared_client(request.payload)
        events = ProtocolEventSink()
        use_case = BindPreparedClient(runtime=self._runtime, store=self._store, events=events)
        try:
            use_case.execute(
                BindPreparedClientCommand(
                    notebook_id=bind_request.notebook_id,
                    session_id=bind_request.session_id,
                    client_id=bind_request.client_id,
                    client_bufnr=bind_request.client_bufnr,
                )
            )
        except SessionError as exc:
            return dump_envelopes([error_response(request, exc.code, str(exc))])
        except ValueError as exc:
            return dump_envelopes([error_response(request, "invalid_state", str(exc))])
        envelopes = [response_envelope(request, ok=True)]
        envelopes.extend(events.events)
        return dump_envelopes(envelopes)

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
        envelopes = [response_envelope(request, ok=True)]
        envelopes.extend(events.events)
        return dump_envelopes(envelopes)

    def _handle_inspect_client(self, request: Envelope) -> List[str]:
        inspect_request = parse_inspect_client(request.payload)
        session = self._store.get_by_notebook(inspect_request.notebook_id)
        if session is None or session.session_id != inspect_request.session_id:
            return dump_envelopes([error_response(request, SessionNotFoundError.code, "Unknown notebook session")])
        try:
            client_view = self._runtime.read_client_view(session, inspect_request.client_id)
        except ValueError as exc:
            return dump_envelopes([error_response(request, "invalid_state", str(exc))])
        return dump_envelopes([response_envelope(request, ok=True, payload={"client": client_view})])

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
