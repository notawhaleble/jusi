from __future__ import annotations

import time
from typing import Callable

from jusi.application.errors import SessionExpiredError, SessionNotFoundError, SessionStoppedError
from jusi.application.ports import (
    AttachSessionCommand,
    BindPreparedClientCommand,
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
from jusi.domain.models import CellExecution, ClientTransport, ExecutableCell, PreparedClient, Session, SessionTarget
from jusi.infrastructure.handler_worker import HandlerWorkerProcess, HandlerWorkerStartup
from jusi.plugins import ActiveDisplayHandler, DisplayHandlerRegistry, DisplayHandlerRuntime, HandlerContext, default_frontend_channel


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
        "expires_at": session.expires_at,
        "last_error": session.last_error,
        "last_action": session.last_action,
    }


def _prepared_payload(prepared: PreparedClient) -> dict:
    payload = {
        "id": prepared.client_id,
        "state": prepared.state,
        "bufnr": prepared.client_bufnr,
        "client_state": prepared.client_state,
    }
    if prepared.transport.kind:
        payload["transport"] = _transport_payload(prepared.transport)
    return payload


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
        "client_id": execution.client_id,
        "client_bufnr": execution.client_bufnr,
        "client_state": execution.client_state,
    }
    if execution.transport.kind:
        payload["transport"] = _transport_payload(execution.transport)
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


class StartSession:
    def __init__(self, runtime: KernelRuntime, store: SessionStore, events: SessionEventSink) -> None:
        self._runtime = runtime
        self._store = store
        self._events = events

    def execute(self, command: StartSessionCommand) -> Session:
        kernel_name = _effective_kernel_name(command.target, command.kernel_name)
        session = Session(
            notebook_id=command.notebook_id,
            state="starting",
            kernel_name=kernel_name,
            target=command.target,
            last_action="start",
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
        session.prepared = PreparedClient(state="spawning", client_state="active")
        self._store.save(session)

        self._events.session_updated(command.notebook_id, _session_payload(session))
        self._events.prepared_updated(command.notebook_id, _prepared_payload(session.prepared))

        client_id = self._runtime.prepare_client(command.notebook_id, session.session_id)
        session.prepared = PreparedClient(state="binding", client_id=client_id, client_bufnr=-1, client_state="active")
        self._store.save(session)
        self._events.prepared_updated(command.notebook_id, _prepared_payload(session.prepared))
        return session


class AttachSession:
    def __init__(self, runtime: KernelRuntime, store: SessionStore, events: SessionEventSink) -> None:
        self._runtime = runtime
        self._store = store
        self._events = events

    def execute(self, command: AttachSessionCommand) -> Session:
        if command.target.kind != "connection_file":
            raise ValueError("attach_session currently supports only target.kind=connection_file")
        session = Session(
            notebook_id=command.notebook_id,
            state="starting",
            target=command.target,
            last_action="attach",
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
        session.prepared = PreparedClient(state="spawning", client_state="active")
        self._store.save(session)

        self._events.session_updated(command.notebook_id, _session_payload(session))
        self._events.prepared_updated(command.notebook_id, _prepared_payload(session.prepared))

        client_id = self._runtime.prepare_client(command.notebook_id, session.session_id)
        session.prepared = PreparedClient(state="binding", client_id=client_id, client_bufnr=-1, client_state="active")
        self._store.save(session)
        self._events.prepared_updated(command.notebook_id, _prepared_payload(session.prepared))
        return session


class ExecuteCell:
    def __init__(
        self,
        runtime: KernelRuntime,
        store: SessionStore,
        events: SessionEventSink,
        display_handlers: DisplayHandlerRegistry | None = None,
        active_handlers: DisplayHandlerRuntime | None = None,
        handler_message_sink: Callable[[str, str, str, str, str, dict], None] | None = None,
        live_handler_message_sink: Callable[[str, str, str, str, str, dict], None] | None = None,
    ) -> None:
        self._runtime = runtime
        self._store = store
        self._events = events
        self._display_handlers = display_handlers or DisplayHandlerRegistry()
        self._active_handlers = active_handlers or DisplayHandlerRuntime()
        self._handler_message_sink = handler_message_sink or (lambda *_args: None)
        self._live_handler_message_sink = live_handler_message_sink or (lambda *_args: None)

    def _resolve_owner_kind(self, command: ExecuteCellCommand) -> str:
        _ = command
        return "kernel"

    def begin_execute(self, command: ExecuteCellCommand) -> tuple[Session, CellExecution]:
        session = _require_matching_session(self._store, command.notebook_id, command.session_id)
        if session.state != "connected":
            raise ValueError("Cannot execute cell without a connected session")
        if session.prepared.state != "ready":
            raise ValueError("Cannot execute cell without a prepared client")

        current_client = CellExecution(
            cell_id=command.cell.cell_id,
            status="busy",
            owner_kind=self._resolve_owner_kind(command),
            client_id=session.prepared.client_id,
            client_bufnr=session.prepared.client_bufnr,
            client_state="active",
        )
        session.last_action = "execute"
        session.prepared = PreparedClient(state="spawning", client_state="active")
        self._store.save(session)

        self._events.session_updated(command.notebook_id, _session_payload(session))
        self._events.prepared_updated(command.notebook_id, _prepared_payload(session.prepared))
        self._events.cell_updated(
            command.notebook_id, _cell_payload(current_client)
        )
        self._store.save_execution(command.notebook_id, current_client)
        self._runtime.activate_client(session, current_client.client_id, current_client.cell_id)
        self._runtime.update_client_execution_status(session, current_client.client_id, current_client.status)

        client_id = self._runtime.prepare_client(command.notebook_id, session.session_id)
        session.prepared = PreparedClient(state="binding", client_id=client_id, client_bufnr=-1, client_state="active")
        self._store.save(session)
        self._events.prepared_updated(command.notebook_id, _prepared_payload(session.prepared))
        return session, current_client

    def finish_execute(self, command: ExecuteCellCommand, session: Session, current_client: CellExecution) -> CellExecution:
        current_client.status = self._runtime.execute_cell(session, command.cell, current_client)
        handoff = self._runtime.consume_handler_handoff(session, current_client.client_id)
        if handoff is not None:
            matched_handler = self._display_handlers.validate_handoff(handoff)
            if matched_handler is not None:
                current_client.owner_kind = "handler"
                current_client.status = self._start_handler_worker(
                    command,
                    session,
                    current_client,
                    matched_handler.handler_id,
                    handoff.magic_name,
                    self._cell_from_handoff(command.cell, handoff.magic_name, handoff.content),
                    handoff.content,
                    handoff.meta,
                )
        self._store.save_execution(command.notebook_id, current_client)
        self._events.cell_updated(command.notebook_id, _cell_payload(current_client))
        return current_client

    def _start_handler_worker(
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
        append_event = lambda event: self._runtime.append_client_execution_event(session, current_client.client_id, event)
        worker = HandlerWorkerProcess(
            startup=HandlerWorkerStartup(
                notebook_id=command.notebook_id,
                session_id=session.session_id,
                client_id=current_client.client_id,
                cell_id=current_client.cell_id,
                handler_id=handler_id,
                magic_name=magic_name,
                cell=worker_cell,
                content=content,
                meta=dict(meta),
            ),
            append_execution_event=append_event,
            update_execution_status=lambda status: self._runtime.update_client_execution_status(
                session, current_client.client_id, status
            ),
            set_client_transport=lambda transport: self._set_handler_client_transport(
                command.notebook_id,
                session,
                current_client,
                transport,
            ),
            emit_handler_message=lambda message_type, payload: self._live_handler_message_sink(
                command.notebook_id,
                session.session_id,
                current_client.client_id,
                handler_id,
                message_type,
                payload,
            ),
            invoke_backend_action=lambda action_name, payload: self._invoke_handler_backend_action(
                action_name,
                session,
                payload,
            ),
            on_exit=lambda exit_status: self._handle_worker_exit(
                command.notebook_id,
                session.session_id,
                current_client.cell_id,
                current_client.client_id,
                exit_status,
            ),
        )
        self._active_handlers.register(
            session.session_id,
            current_client.client_id,
            ActiveDisplayHandler(
                handler_id=handler_id,
                handler=worker,  # type: ignore[arg-type]
                context=None,
            ),
        )
        return worker.wait_started()

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

    def _invoke_handler_backend_action(self, action_name: str, session: Session, payload: dict[str, object]) -> dict[str, object]:
        if action_name == "materialize_vd_source":
            expression = str(payload.get("expression", "")).strip()
            return dict(self._runtime.materialize_vd_source(session, expression))
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
            self._active_handlers.remove_client(session_id, client_id)
            return
        if execution.status not in {"done", "error", "interrupted"}:
            execution.status = exit_status if exit_status in {"done", "error"} else "error"
            self._store.save_execution(notebook_id, execution)
            self._events.cell_updated(notebook_id, _cell_payload(execution))
        self._active_handlers.remove_client(session_id, client_id)

    def execute(self, command: ExecuteCellCommand) -> CellExecution:
        session, current_client = self.begin_execute(command)
        return self.finish_execute(command, session, current_client)


class InterruptCell:
    def __init__(
        self,
        runtime: KernelRuntime,
        store: SessionStore,
        events: SessionEventSink,
        active_handlers: DisplayHandlerRuntime | None = None,
    ) -> None:
        self._runtime = runtime
        self._store = store
        self._events = events
        self._active_handlers = active_handlers or DisplayHandlerRuntime()

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

        if execution.owner_kind == "kernel":
            execution.status = self._runtime.interrupt_kernel(session, execution)
        elif execution.owner_kind == "handler":
            self._active_handlers.interrupt_client(session.session_id, execution.client_id)
            execution.status = self._runtime.interrupt_handler(session, execution)
        else:
            raise ValueError("Cannot interrupt execution with unknown owner")

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
        session.prepared = PreparedClient(state="missing", client_state="shutdown")

        self._runtime.disconnect_session(session, command.reason)
        session.state = "disconnected"
        self._store.save(session)
        self._events.session_updated(command.notebook_id, _session_payload(session))
        self._events.prepared_updated(command.notebook_id, _prepared_payload(session.prepared))

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
        session.prepared = PreparedClient(state="spawning", client_state="active")
        self._store.save(session)
        self._events.session_updated(command.notebook_id, _session_payload(session))
        self._events.prepared_updated(command.notebook_id, _prepared_payload(session.prepared))

        client_id = self._runtime.prepare_client(command.notebook_id, session.session_id)
        session.state = "connected"
        session.prepared = PreparedClient(state="binding", client_id=client_id, client_bufnr=-1, client_state="active")
        self._store.save(session)
        self._events.session_updated(command.notebook_id, _session_payload(session))
        self._events.prepared_updated(command.notebook_id, _prepared_payload(session.prepared))
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
        session.prepared = PreparedClient(state="missing", client_state="shutdown")
        self._store.save(session)
        self._events.session_updated(command.notebook_id, _session_payload(session))
        self._events.prepared_updated(command.notebook_id, _prepared_payload(session.prepared))

        for execution in self._store.list_executions(command.notebook_id):
            if execution.status in {"busy", "follow-up"}:
                execution.status = "interrupted"
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


class BindPreparedClient:
    def __init__(self, runtime: KernelRuntime, store: SessionStore, events: SessionEventSink) -> None:
        self._runtime = runtime
        self._store = store
        self._events = events

    def execute(self, command: BindPreparedClientCommand) -> PreparedClient:
        session = _require_matching_session(self._store, command.notebook_id, command.session_id)
        if session.prepared.client_id != command.client_id:
            raise ValueError("Prepared client id does not match current session state")
        if session.prepared.state not in {"binding", "ready"}:
            raise ValueError("Prepared client is not awaiting binding")

        session.prepared = PreparedClient(
            state="ready",
            client_id=command.client_id,
            client_bufnr=command.client_bufnr,
            client_state="active",
        )
        self._runtime.bind_prepared_client(session, command.client_id, command.client_bufnr)
        self._store.save(session)
        self._events.prepared_updated(command.notebook_id, _prepared_payload(session.prepared))
        return session.prepared


class ShutdownClient:
    def __init__(self, runtime: KernelRuntime, store: SessionStore, events: SessionEventSink) -> None:
        self._runtime = runtime
        self._store = store
        self._events = events

    def execute(self, command: ShutdownClientCommand) -> None:
        session = _require_matching_session(self._store, command.notebook_id, command.session_id)

        if session.prepared.client_id == command.client_id:
            session.prepared = PreparedClient(
                state=session.prepared.state,
                client_id=session.prepared.client_id,
                client_bufnr=session.prepared.client_bufnr,
                client_state="shutting_down",
            )
            self._store.save(session)
            self._events.prepared_updated(command.notebook_id, _prepared_payload(session.prepared))
            self._runtime.shutdown_client(session, command.client_id, command.reason)
            session.prepared = PreparedClient(state="missing", client_state="shutdown")
            self._store.save(session)
            self._events.prepared_updated(command.notebook_id, _prepared_payload(session.prepared))
            return

        execution = self._store.get_execution(command.notebook_id, command.cell_id)
        if execution is None or execution.client_id != command.client_id:
            raise ValueError("No tracked client ownership for shutdown request")

        execution.client_state = "shutting_down"
        self._store.save_execution(command.notebook_id, execution)
        self._events.cell_updated(command.notebook_id, _cell_payload(execution))
        self._runtime.shutdown_client(session, command.client_id, command.reason)
        execution.client_state = "shutdown"
        execution.client_bufnr = -1
        self._store.save_execution(command.notebook_id, execution)
        self._events.cell_updated(command.notebook_id, _cell_payload(execution))


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
    def __init__(self, store: SessionStore, active_handlers: DisplayHandlerRuntime) -> None:
        self._store = store
        self._active_handlers = active_handlers

    def execute(self, command: HandlerMessageCommand) -> Session:
        session = _require_matching_session(self._store, command.notebook_id, command.session_id)
        active_handler = self._active_handlers.get(command.session_id, command.client_id)
        if active_handler is None:
            raise ValueError("No active handler is registered for the client")
        if active_handler.handler_id != command.handler_id:
            raise ValueError("Handler message does not match the active handler")
        active_handler.handler.on_frontend_message(active_handler.context, command.message_type, command.payload)
        return session
