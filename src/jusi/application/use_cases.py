from __future__ import annotations

import time
from typing import Callable

from jusi.application.errors import SessionExpiredError, SessionNotFoundError, SessionStoppedError
from jusi.application.ports import (
    AttachSessionCommand,
    DisconnectSessionCommand,
    ExecuteCellCommand,
    HandlerMessageCommand,
    HealthcheckReplyCommand,
    InputReplyCommand,
    InterruptCellCommand,
    KernelRuntime,
    ReconnectSessionCommand,
    SessionEventSink,
    SessionStore,
    ShutdownClientCommand,
    StopSessionCommand,
    StartSessionCommand,
)
from jusi.domain.models import (
    CellExecution,
    ClientTransport,
    ExecutableCell,
    Session,
    SessionTarget,
    clear_execution_runtime_identity,
    normalize_closed_followup_execution,
    normalize_stopped_active_execution,
)
from jusi.infrastructure.native_terminal_transport import (
    build_transcript_terminal_attach_env,
    native_terminal_attach_command,
)
from jusi.visidata_support import normalize_visidatarc_content
from jusi.infrastructure.client_runtime_controller import LiveClientControllerRegistry
from jusi.infrastructure.client_runtime_host import (
    default_launch_runtime_mode,
    default_transition_target_runtime_mode,
    require_registered_runtime_mode,
)
from jusi.infrastructure.client_runtime_launcher import (
    ClientRuntimeLauncher,
    HandlerClientCallbacks,
    RuntimeClientLaunchOperation,
    RuntimeClientTransitionOperation,
)
from jusi.infrastructure.client_runtime_updates import ClientRuntimeUpdate
from jusi.infrastructure.debug_timing import emit_timing
from jusi.plugins import DisplayHandlerRegistry, HandlerContext, collect_plugin_palette, collect_plugin_presentation_specs, default_frontend_channel


def _target_payload(target: SessionTarget) -> dict:
    return {
        "source": target.source,
        "alias": target.alias,
        "kind": target.kind,
        "value": target.value,
        "config": dict(target.config),
    }


def _session_payload(session: Session) -> dict:
    return {
        "id": session.session_id,
        "state": session.state,
        "kernel_name": session.kernel_name,
        "connection": session.connection,
        "target": _target_payload(session.target),
        "plugin_specs": {key: dict(value) for key, value in session.plugin_specs.items()},
        "palette": {key: {"entries": list(value.get("entries", []))} for key, value in session.palette.items()},
        "expires_at": session.expires_at,
        "last_error": session.last_error,
        "last_action": session.last_action,
    }


def _effective_kernel_name(target: SessionTarget, kernel_name: str) -> str:
    requested = kernel_name.strip() or "python3"
    if target.kind == "venv":
        configured = str(target.config.get("kernel_name", "")).strip()
        return configured or "python3"
    return requested


def _cell_payload(execution: CellExecution) -> dict:
    payload = {
        "id": execution.cell_id,
        "status": execution.status,
        "owner": {"kind": execution.owner_kind},
        "client_state": execution.client_state,
    }
    if execution.client_id:
        payload["client_id"] = execution.client_id
    if execution.runtime_mode:
        payload["runtime_mode"] = execution.runtime_mode
    if execution.client_bufnr >= 0:
        payload["client_bufnr"] = execution.client_bufnr
    if execution.transport.kind:
        payload["transport"] = _transport_payload(execution.transport)
    if execution.presentation:
        payload["presentation"] = dict(execution.presentation)
    return payload


def _transport_payload(transport: ClientTransport) -> dict:
    return {
        "kind": transport.kind,
        "attach_cmd": list(transport.attach_cmd),
        "attach_env": dict(transport.attach_env),
        "session_id": transport.session_id,
        "client_id": transport.client_id,
        "handler_id": transport.handler_id,
    }


SESSION_DISCONNECT_TTL_SECONDS = 300.0


def _require_matching_session(store: SessionStore, notebook_id: str, session_id: str) -> Session:
    session = store.get_by_notebook(notebook_id)
    if session is None or session.session_id != session_id:
        raise SessionNotFoundError("Unknown notebook session")
    return session


def _require_execution_runtime_mode(execution: CellExecution):  # type: ignore[no-untyped-def]
    if execution.owner_kind == "unknown":
        raise ValueError("Cannot operate on execution without an active runtime owner")
    mode = require_registered_runtime_mode(execution.runtime_mode)
    if mode.owner_kind != execution.owner_kind:
        raise ValueError("Execution owner does not match runtime mode")
    return mode


def _validate_execution_runtime_consistency(execution: CellExecution) -> None:
    if not execution.runtime_mode:
        return
    mode = require_registered_runtime_mode(execution.runtime_mode)
    if execution.owner_kind == "unknown":
        return
    if mode.owner_kind != execution.owner_kind:
        raise ValueError("Execution owner does not match runtime mode")


class StartSession:
    def __init__(
        self,
        runtime: KernelRuntime,
        store: SessionStore,
        events: SessionEventSink,
        display_handlers: DisplayHandlerRegistry | None = None,
    ) -> None:
        self._runtime = runtime
        self._store = store
        self._events = events
        self._display_handlers = display_handlers or DisplayHandlerRegistry()

    def execute(self, command: StartSessionCommand) -> Session:
        kernel_name = _effective_kernel_name(command.target, command.kernel_name)
        session = Session(
            notebook_id=command.notebook_id,
            state="starting",
            kernel_name=kernel_name,
            target=command.target,
            last_action="start",
            visidatarc_content=normalize_visidatarc_content(command.visidatarc),
            plugin_specs=collect_plugin_presentation_specs(self._display_handlers),
            palette=collect_plugin_palette(self._display_handlers, command.target.config),
        )
        self._store.save(session)
        self._events.session_updated(command.notebook_id, _session_payload(session))

        session_id, connection = self._runtime.start_target(command.target, kernel_name)
        session.session_id = session_id
        session.connection = connection
        session.state = "connected"
        session.expires_at = None
        session.frontend_last_ack_at = time.time()
        session.frontend_healthcheck_id = ""
        session.frontend_healthcheck_deadline = None
        self._store.save(session)

        self._events.session_updated(command.notebook_id, _session_payload(session))
        return session


class AttachSession:
    def __init__(
        self,
        runtime: KernelRuntime,
        store: SessionStore,
        events: SessionEventSink,
        display_handlers: DisplayHandlerRegistry | None = None,
    ) -> None:
        self._runtime = runtime
        self._store = store
        self._events = events
        self._display_handlers = display_handlers or DisplayHandlerRegistry()

    def execute(self, command: AttachSessionCommand) -> Session:
        if command.target.kind != "connection_file":
            raise ValueError("attach_session currently supports only target.kind=connection_file")
        session = Session(
            notebook_id=command.notebook_id,
            state="starting",
            target=command.target,
            last_action="attach",
            visidatarc_content=normalize_visidatarc_content(command.visidatarc),
            plugin_specs=collect_plugin_presentation_specs(self._display_handlers),
            palette=collect_plugin_palette(self._display_handlers, command.target.config),
        )
        self._store.save(session)
        self._events.session_updated(command.notebook_id, _session_payload(session))

        session_id, connection = self._runtime.attach_target(command.target)
        session.session_id = session_id
        session.connection = connection
        session.state = "connected"
        session.expires_at = None
        session.frontend_last_ack_at = time.time()
        session.frontend_healthcheck_id = ""
        session.frontend_healthcheck_deadline = None
        self._store.save(session)

        self._events.session_updated(command.notebook_id, _session_payload(session))
        return session


class ExecuteCell:
    def __init__(
        self,
        runtime: KernelRuntime,
        store: SessionStore,
        events: SessionEventSink,
        display_handlers: DisplayHandlerRegistry | None = None,
        active_client_controllers: LiveClientControllerRegistry | None = None,
        client_runtime_launcher: ClientRuntimeLauncher | None = None,
        handler_message_sink: Callable[[str, str, str, str, str, dict], None] | None = None,
        live_handler_message_sink: Callable[[str, str, str, str, str, dict], None] | None = None,
    ) -> None:
        self._runtime = runtime
        self._store = store
        self._events = events
        self._display_handlers = display_handlers or DisplayHandlerRegistry()
        self._active_client_controllers = active_client_controllers or LiveClientControllerRegistry()
        self._client_runtime_launcher = client_runtime_launcher or ClientRuntimeLauncher(self._active_client_controllers)
        self._handler_message_sink = handler_message_sink or (lambda *_args: None)
        self._live_handler_message_sink = live_handler_message_sink or (lambda *_args: None)

    def begin_execute(self, command: ExecuteCellCommand) -> tuple[Session, CellExecution]:
        launch_mode = default_launch_runtime_mode()
        emit_timing(
            "use_case.execute.begin",
            notebook_id=command.notebook_id,
            session_id=command.session_id,
            cell_id=command.cell.cell_id,
            kind=command.cell.kind,
            syntax=command.cell.syntax,
        )
        session = _require_matching_session(self._store, command.notebook_id, command.session_id)
        if session.state != "connected":
            raise ValueError("Cannot execute cell without a connected session")
        launch = self._client_runtime_launcher.launch_runtime_client(
            RuntimeClientLaunchOperation(
                runtime_mode=launch_mode.name,
                notebook_id=command.notebook_id,
                session_id=session.session_id,
                cell_id=command.cell.cell_id,
                initial_status="busy",
                start_client=lambda notebook_id, cell_id, initial_status: self._runtime.start_client(
                    session,
                    notebook_id,
                    cell_id,
                    initial_status,
                ),
            )
        )
        client_id = launch.client_id

        current_client = CellExecution(
            cell_id=command.cell.cell_id,
            status="busy",
            owner_kind=launch_mode.owner_kind,
            client_id=client_id,
            runtime_mode=launch_mode.name,
            client_bufnr=-1,
            client_state="active",
        )
        if command.cell.kind == "code":
            self._set_kernel_client_transport(command.notebook_id, session, current_client)
        session.last_action = "execute"
        self._store.save(session)
        emit_timing(
            "use_case.execute.session_saved_spawning",
            notebook_id=command.notebook_id,
            session_id=session.session_id,
            cell_id=current_client.cell_id,
            client_id=current_client.client_id,
        )

        self._events.session_updated(command.notebook_id, _session_payload(session))
        self._events.cell_updated(
            command.notebook_id, _cell_payload(current_client)
        )
        self._store.save_execution(command.notebook_id, current_client)
        emit_timing(
            "use_case.execute.events_and_execution_saved",
            notebook_id=command.notebook_id,
            session_id=session.session_id,
            cell_id=current_client.cell_id,
            client_id=current_client.client_id,
        )
        emit_timing(
            "use_case.execute.client_activated",
            notebook_id=command.notebook_id,
            session_id=session.session_id,
            cell_id=current_client.cell_id,
            client_id=current_client.client_id,
            owner_kind=current_client.owner_kind,
            status=current_client.status,
        )
        return session, current_client

    def _set_kernel_client_transport(
        self,
        notebook_id: str,
        session: Session,
        execution: CellExecution,
    ) -> None:
        transport = ClientTransport(
            kind="native_terminal",
            attach_cmd=native_terminal_attach_command(),
            attach_env=build_transcript_terminal_attach_env(
                session_id=session.session_id,
                client_id=execution.client_id,
            ),
            session_id=session.session_id,
            client_id=execution.client_id,
        )
        execution.transport = transport
        self._runtime.set_client_transport(session, execution.client_id, transport)
        self._store.save_execution(notebook_id, execution)

    def finish_execute(self, command: ExecuteCellCommand, session: Session, current_client: CellExecution) -> CellExecution:
        emit_timing(
            "use_case.execute.runtime_start",
            notebook_id=command.notebook_id,
            session_id=session.session_id,
            cell_id=current_client.cell_id,
            client_id=current_client.client_id,
        )
        try:
            current_client.status = self._runtime.execute_cell(session, command.cell, current_client)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            emit_timing(
                "use_case.execute.runtime_error",
                notebook_id=command.notebook_id,
                session_id=session.session_id,
                cell_id=current_client.cell_id,
                client_id=current_client.client_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            current_client.status = "error"
            self._runtime.update_client_execution_status(session, current_client.client_id, current_client.status)
            self._runtime.append_client_execution_event(
                session,
                current_client.client_id,
                {"type": "error", "message": message},
            )
            self._maybe_release_managed_controller(session.session_id, current_client)
            self._store.save_execution(command.notebook_id, current_client)
            self._events.cell_updated(command.notebook_id, _cell_payload(current_client))
            raise
        emit_timing(
            "use_case.execute.runtime_done",
            notebook_id=command.notebook_id,
            session_id=session.session_id,
            cell_id=current_client.cell_id,
            client_id=current_client.client_id,
            runtime_status=current_client.status,
        )
        handoff = self._runtime.consume_handler_handoff(session, current_client.client_id)
        if handoff is not None:
            emit_timing(
                "use_case.execute.handoff_consumed",
                notebook_id=command.notebook_id,
                session_id=session.session_id,
                cell_id=current_client.cell_id,
                client_id=current_client.client_id,
                magic_name=handoff.magic_name,
                handler_id=handoff.handler_id,
            )
            matched_handler = self._display_handlers.validate_handoff(handoff)
            if matched_handler is not None:
                handler_mode = default_transition_target_runtime_mode(current_client.runtime_mode)
                current_client.presentation = self._presentation_for_handoff(handoff, matched_handler)
                current_client.status = self._take_over_with_handler_runtime(
                    command,
                    session,
                    current_client,
                    matched_handler.handler_id,
                    handoff.magic_name,
                    self._cell_from_handoff(command.cell, handoff.magic_name, handoff.content),
                    handoff.content,
                    handoff.meta,
                )
                current_client.owner_kind = handler_mode.owner_kind
                current_client.runtime_mode = handler_mode.name
        self._maybe_release_managed_controller(session.session_id, current_client)
        self._store.save_execution(command.notebook_id, current_client)
        self._events.cell_updated(command.notebook_id, _cell_payload(current_client))
        return current_client

    @staticmethod
    def _presentation_for_handoff(handoff, matched_handler) -> dict[str, object]:  # type: ignore[no-untyped-def]
        presentation = dict(matched_handler.presentation)
        raw_override = handoff.meta.get("presentation")
        if isinstance(raw_override, dict):
            presentation.update(dict(raw_override))
        return presentation

    def _take_over_with_handler_runtime(
        self,
        command: ExecuteCellCommand,
        session: Session,
        current_client: CellExecution,
        handler_id: str,
        magic_name: str,
        worker_cell: ExecutableCell,
        content: str,
        meta: dict[str, object],
    ) -> str:
        emit_timing(
            "use_case.handler_takeover.start",
            notebook_id=command.notebook_id,
            session_id=session.session_id,
            cell_id=current_client.cell_id,
            client_id=current_client.client_id,
            handler_id=handler_id,
            magic_name=magic_name,
        )
        takeover = self._client_runtime_launcher.transition_runtime_client(
            RuntimeClientTransitionOperation(
                source_runtime_mode=current_client.runtime_mode,
                notebook_id=command.notebook_id,
                session_id=session.session_id,
                client_id=current_client.client_id,
                cell_id=current_client.cell_id,
                handler_id=handler_id,
                magic_name=magic_name,
                cell=worker_cell,
                content=content,
                meta=dict(meta),
                callbacks=self._build_handler_client_callbacks(
                    command.notebook_id,
                    session,
                    current_client,
                    handler_id,
                ),
            )
        )
        emit_timing(
            "use_case.handler_takeover.done",
            notebook_id=command.notebook_id,
            session_id=session.session_id,
            cell_id=current_client.cell_id,
            client_id=current_client.client_id,
            handler_id=handler_id,
            status=takeover.status,
        )
        return takeover.status

    def _build_handler_client_callbacks(
        self,
        notebook_id: str,
        session: Session,
        execution: CellExecution,
        handler_id: str,
    ) -> HandlerClientCallbacks:
        return HandlerClientCallbacks(
            handle_runtime_update=lambda update: self._handle_handler_runtime_update(
                notebook_id,
                session,
                execution,
                handler_id,
                update,
            ),
            invoke_backend_action=lambda action_name, payload: self._invoke_handler_backend_action(
                action_name,
                session,
                execution.client_id,
                payload,
            ),
            on_exit=lambda exit_status: self._handle_worker_exit(
                notebook_id,
                session.session_id,
                execution.cell_id,
                execution.client_id,
                exit_status,
            ),
        )

    def _handle_handler_runtime_update(
        self,
        notebook_id: str,
        session: Session,
        execution: CellExecution,
        handler_id: str,
        update: ClientRuntimeUpdate,
    ) -> None:
        if update.kind == "execution_event":
            event = update.payload.get("event", {})
            if isinstance(event, dict):
                self._runtime.append_client_execution_event(session, execution.client_id, dict(event))
            return
        if update.kind == "execution_status":
            self._runtime.update_client_execution_status(
                session,
                execution.client_id,
                str(update.payload.get("status", "")).strip(),
            )
            return
        if update.kind == "transport":
            transport = update.payload.get("transport")
            if isinstance(transport, ClientTransport):
                self._set_handler_client_transport(notebook_id, session, execution, transport)
            return
        if update.kind == "channel_event":
            event_type = str(update.payload.get("event_type", "")).strip()
            payload = dict(update.payload.get("payload", {}))
            self._runtime.append_client_execution_event(
                session,
                execution.client_id,
                {
                    "type": "handler_channel_event",
                    "event_type": event_type,
                    "payload": payload,
                },
            )
            self._live_handler_message_sink(
                notebook_id,
                session.session_id,
                execution.client_id,
                handler_id,
                event_type,
                payload,
            )
            return
        if update.kind == "action_request":
            action_type = str(update.payload.get("action_type", "")).strip()
            payload = dict(update.payload.get("payload", {}))
            self._runtime.append_client_execution_event(
                session,
                execution.client_id,
                {
                    "type": "frontend_action_request",
                    "action_type": action_type,
                    "payload": payload,
                },
            )
            self._live_handler_message_sink(
                notebook_id,
                session.session_id,
                execution.client_id,
                handler_id,
                "action_request",
                {
                    "action_type": action_type,
                    "payload": payload,
                },
            )
            return
        if update.kind == "live_handler_message":
            self._live_handler_message_sink(
                notebook_id,
                session.session_id,
                execution.client_id,
                handler_id,
                str(update.payload.get("message_type", "")).strip(),
                dict(update.payload.get("payload", {})),
            )
            return
        raise ValueError(f"Unsupported handler runtime update kind: {update.kind}")

    def _maybe_release_managed_controller(self, session_id: str, execution: CellExecution) -> None:
        if execution.owner_kind != "kernel":
            return
        if execution.status not in {"done", "error", "interrupted"}:
            return
        self._active_client_controllers.remove_client(session_id, execution.client_id)

    @staticmethod
    def _cell_from_handoff(cell: ExecutableCell, magic_name: str, content: str) -> ExecutableCell:
        main_lines = [f"%%{magic_name}"]
        if content:
            main_lines.extend(content.splitlines())
        return ExecutableCell(
            cell_id=cell.cell_id,
            kind=cell.kind,
            syntax=cell.syntax,
            main_lines=main_lines,
            keep_running=cell.keep_running,
        )

    def _invoke_handler_backend_action(
        self,
        action_name: str,
        session: Session,
        client_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if action_name == "plugin_runtime_request":
            message_type = str(payload.get("message_type", "")).strip()
            emit_timing(
                "use_case.plugin_runtime_request.start",
                session_id=session.session_id,
                client_id=client_id,
                message_type=message_type,
            )
            request = getattr(self._runtime, "request_plugin_runtime", None)
            if not callable(request):
                raise ValueError("Runtime does not support plugin runtime requests")
            try:
                response = request(session, client_id, payload)
            except Exception as exc:
                emit_timing(
                    "use_case.plugin_runtime_request.error",
                    session_id=session.session_id,
                    client_id=client_id,
                    message_type=message_type,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                raise
            emit_timing(
                "use_case.plugin_runtime_request.done",
                session_id=session.session_id,
                client_id=client_id,
                message_type=message_type,
                response_keys=sorted(list(response.keys())) if isinstance(response, dict) else [],
            )
            if isinstance(response, dict):
                return dict(response)
            return {}
        raise ValueError(f"Unsupported handler backend action: {action_name}")

    def _set_handler_client_transport(
        self,
        notebook_id: str,
        session: Session,
        execution: CellExecution,
        transport: ClientTransport,
    ) -> None:
        execution.transport = transport
        self._runtime.set_client_transport(session, execution.client_id, transport)
        self._store.save_execution(notebook_id, execution)

    def _handle_worker_exit(
        self,
        notebook_id: str,
        session_id: str,
        cell_id: int,
        client_id: str,
        exit_status: str,
    ) -> None:
        execution = self._store.get_execution(notebook_id, cell_id)
        if execution is None:
            self._active_client_controllers.remove_client(session_id, client_id)
            return
        if execution.status not in {"done", "error", "interrupted"}:
            execution.status = exit_status if exit_status in {"done", "error"} else "error"
            self._store.save_execution(notebook_id, execution)
            self._events.cell_updated(notebook_id, _cell_payload(execution))
        self._active_client_controllers.remove_client(session_id, client_id)

    def execute(self, command: ExecuteCellCommand) -> CellExecution:
        session, current_client = self.begin_execute(command)
        return self.finish_execute(command, session, current_client)


class InterruptCell:
    def __init__(
        self,
        runtime: KernelRuntime,
        store: SessionStore,
        events: SessionEventSink,
        active_client_controllers: LiveClientControllerRegistry | None = None,
    ) -> None:
        self._runtime = runtime
        self._store = store
        self._events = events
        self._active_client_controllers = active_client_controllers or LiveClientControllerRegistry()

    def execute(self, command: InterruptCellCommand) -> CellExecution:
        session = _require_matching_session(self._store, command.notebook_id, command.session_id)
        execution = self._store.get_execution(command.notebook_id, command.cell_id)
        if execution is None:
            raise ValueError("No tracked execution for cell")
        if execution.status not in {"busy", "follow-up"}:
            raise ValueError("Cell is not in an interruptible state")

        session.last_action = "interrupt"
        self._store.save(session)
        self._events.session_updated(command.notebook_id, _session_payload(session))

        mode = _require_execution_runtime_mode(execution)
        if mode.name == "transcript":
            execution.status = self._runtime.interrupt_kernel(session, execution)
        elif mode.name == "handler":
            self._active_client_controllers.interrupt_client(session.session_id, execution.client_id)
            execution.status = self._runtime.interrupt_handler(session, execution)
        else:
            raise ValueError(f"Unsupported runtime mode for interrupt: {mode.name}")

        self._store.save_execution(command.notebook_id, execution)
        self._events.cell_updated(command.notebook_id, _cell_payload(execution))
        return execution


class InputReply:
    def __init__(self, runtime: KernelRuntime, store: SessionStore, events: SessionEventSink) -> None:
        self._runtime = runtime
        self._store = store
        self._events = events

    def begin_reply(self, command: InputReplyCommand) -> tuple[Session, CellExecution]:
        session = _require_matching_session(self._store, command.notebook_id, command.session_id)
        if session.state != "connected":
            raise ValueError("Cannot reply to input without a connected session")

        execution = self._store.get_execution(command.notebook_id, command.cell_id)
        if execution is None:
            raise ValueError("No tracked execution for cell")
        if execution.client_id != command.client_id:
            raise ValueError("Input reply client does not match tracked cell execution")
        if execution.status != "busy":
            raise ValueError("Cell is not waiting in a busy execution")

        session.last_action = "input_reply"
        self._store.save(session)
        self._events.session_updated(command.notebook_id, _session_payload(session))
        return session, execution

    def finish_reply(self, command: InputReplyCommand, session: Session, execution: CellExecution) -> CellExecution:
        execution.status = self._runtime.reply_input(session, execution, command.value)
        self._store.save_execution(command.notebook_id, execution)
        self._events.cell_updated(command.notebook_id, _cell_payload(execution))
        return execution

    def execute(self, command: InputReplyCommand) -> CellExecution:
        session, execution = self.begin_reply(command)
        return self.finish_reply(command, session, execution)


class DisconnectSession:
    def __init__(self, runtime: KernelRuntime, store: SessionStore, events: SessionEventSink) -> None:
        self._runtime = runtime
        self._store = store
        self._events = events

    def execute(self, command: DisconnectSessionCommand) -> Session:
        session = _require_matching_session(self._store, command.notebook_id, command.session_id)

        session.last_action = "disconnect"
        session.last_error = command.reason
        session.expires_at = time.time() + SESSION_DISCONNECT_TTL_SECONDS
        session.expires_at = self._runtime.sync_disconnect_deadline(session, session.expires_at)
        session.frontend_healthcheck_id = ""
        session.frontend_healthcheck_deadline = None

        self._runtime.disconnect_session(session, command.reason)
        session.state = "disconnected"
        self._store.save(session)
        self._events.session_updated(command.notebook_id, _session_payload(session))

        for execution in self._store.list_executions(command.notebook_id):
            if execution.status in {"busy", "follow-up"}:
                execution.owner_kind = "unknown"
                execution.client_state = "active"
                self._store.save_execution(command.notebook_id, execution)
                self._events.cell_updated(command.notebook_id, _cell_payload(execution))
        return session


class ReconnectSession:
    def __init__(self, runtime: KernelRuntime, store: SessionStore, events: SessionEventSink) -> None:
        self._runtime = runtime
        self._store = store
        self._events = events

    def execute(self, command: ReconnectSessionCommand) -> Session:
        session = _require_matching_session(self._store, command.notebook_id, command.session_id)
        if session.state == "stopped":
            raise SessionStoppedError("Session is already stopped")
        if session.expires_at is not None and session.expires_at <= time.time():
            raise SessionExpiredError("Session has expired")
        if session.state != "disconnected":
            raise ValueError("Only disconnected sessions can reconnect")

        session.state = "starting"
        session.last_action = "reconnect"
        session.last_error = ""
        session.expires_at = None
        session.expires_at = self._runtime.sync_disconnect_deadline(session, None)
        session.frontend_last_ack_at = time.time()
        session.frontend_healthcheck_id = ""
        session.frontend_healthcheck_deadline = None
        self._store.save(session)
        self._events.session_updated(command.notebook_id, _session_payload(session))
        session.state = "connected"
        self._store.save(session)
        self._events.session_updated(command.notebook_id, _session_payload(session))
        return session


class StopSession:
    def __init__(self, runtime: KernelRuntime, store: SessionStore, events: SessionEventSink) -> None:
        self._runtime = runtime
        self._store = store
        self._events = events

    def begin_stop(self, command: StopSessionCommand) -> Session:
        session = _require_matching_session(self._store, command.notebook_id, command.session_id)
        if session.state == "stopped":
            raise SessionStoppedError("Session is already stopped")
        if session.state not in {"starting", "connected", "disconnected", "stopping"}:
            raise ValueError("Cannot stop a session that is not active")

        session.state = "stopping"
        session.last_action = "stop"
        session.expires_at = None
        session.frontend_healthcheck_id = ""
        session.frontend_healthcheck_deadline = None
        self._store.save(session)
        self._events.session_updated(command.notebook_id, _session_payload(session))

        for execution in self._store.list_executions(command.notebook_id):
            if execution.status in {"busy", "follow-up"}:
                normalize_stopped_active_execution(execution)
                self._store.save_execution(command.notebook_id, execution)
                self._events.cell_updated(command.notebook_id, _cell_payload(execution))

        return session

    def finish_stop(self, command: StopSessionCommand, session: Session) -> Session:
        self._runtime.stop_session(session)
        session.state = "stopped"
        self._store.save(session)
        self._events.session_updated(command.notebook_id, _session_payload(session))
        return session

    def execute(self, command: StopSessionCommand) -> Session:
        session = self.begin_stop(command)
        return self.finish_stop(command, session)


class ShutdownClient:
    def __init__(self, runtime: KernelRuntime, store: SessionStore, events: SessionEventSink) -> None:
        self._runtime = runtime
        self._store = store
        self._events = events

    def execute(self, command: ShutdownClientCommand) -> None:
        session = _require_matching_session(self._store, command.notebook_id, command.session_id)

        execution = self._store.get_execution(command.notebook_id, command.cell_id)
        if execution is None or execution.client_id != command.client_id:
            raise ValueError("No tracked client ownership for shutdown request")
        _validate_execution_runtime_consistency(execution)

        self._interrupt_busy_execution_before_shutdown(session, execution)
        self._normalize_closed_handler_followup(execution)
        execution.client_state = "shutting_down"
        self._store.save_execution(command.notebook_id, execution)
        self._events.cell_updated(command.notebook_id, _cell_payload(execution))
        self._runtime.shutdown_client(session, command.client_id, command.reason)
        execution.client_state = "shutdown"
        execution.client_bufnr = -1
        self._store.save_execution(command.notebook_id, execution)
        self._events.cell_updated(command.notebook_id, _cell_payload(execution))

    def _normalize_closed_handler_followup(self, execution: CellExecution) -> None:
        normalize_closed_followup_execution(execution)

    def _interrupt_busy_execution_before_shutdown(self, session: Session, execution: CellExecution) -> None:
        if execution.status != "busy":
            return
        mode = _require_execution_runtime_mode(execution)
        if mode.name == "transcript":
            execution.status = self._runtime.interrupt_kernel(session, execution)
        elif mode.name == "handler":
            execution.status = self._runtime.interrupt_handler(session, execution)
        else:
            raise ValueError(f"Unsupported runtime mode for shutdown interrupt: {mode.name}")
        execution.owner_kind = "unknown"
        clear_execution_runtime_identity(execution)


class HealthcheckReply:
    def __init__(self, store: SessionStore) -> None:
        self._store = store

    def execute(self, command: HealthcheckReplyCommand) -> Session:
        session = _require_matching_session(self._store, command.notebook_id, command.session_id)
        if session.state != "connected":
            raise ValueError("Cannot accept healthcheck reply without a connected session")
        if session.frontend_healthcheck_id != command.healthcheck_id:
            raise ValueError("Healthcheck reply does not match current outstanding check")
        session.frontend_last_ack_at = time.time()
        session.frontend_healthcheck_id = ""
        session.frontend_healthcheck_deadline = None
        self._store.save(session)
        return session


class HandlerMessage:
    def __init__(self, store: SessionStore, active_client_controllers: LiveClientControllerRegistry) -> None:
        self._store = store
        self._active_client_controllers = active_client_controllers

    def execute(self, command: HandlerMessageCommand) -> Session:
        emit_timing(
            "use_case.handler_message.start",
            notebook_id=command.notebook_id,
            session_id=command.session_id,
            client_id=command.client_id,
            handler_id=command.handler_id,
            message_type=command.message_type,
        )
        session = _require_matching_session(self._store, command.notebook_id, command.session_id)
        self._active_client_controllers.dispatch_frontend_message(
            command.session_id,
            command.client_id,
            handler_id=command.handler_id,
            message_type=command.message_type,
            payload=command.payload,
        )
        emit_timing(
            "use_case.handler_message.done",
            notebook_id=command.notebook_id,
            session_id=command.session_id,
            client_id=command.client_id,
            handler_id=command.handler_id,
            message_type=command.message_type,
        )
        return session
