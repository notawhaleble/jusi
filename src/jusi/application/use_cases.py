from __future__ import annotations

from jusi.application.ports import (
    BindPreparedClientCommand,
    DisconnectSessionCommand,
    ExecuteCellCommand,
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
from jusi.domain.models import CellExecution, PreparedClient, Session


def _session_payload(session: Session) -> dict:
    return {
        "id": session.session_id,
        "state": session.state,
        "kernel_name": session.kernel_name,
        "connection": session.connection,
        "attachable": session.attachable,
        "last_error": session.last_error,
        "last_action": session.last_action,
    }


def _prepared_payload(prepared: PreparedClient) -> dict:
    return {
        "id": prepared.client_id,
        "state": prepared.state,
        "bufnr": prepared.client_bufnr,
        "client_state": prepared.client_state,
    }


def _cell_payload(execution: CellExecution) -> dict:
    return {
        "id": execution.cell_id,
        "status": execution.status,
        "owner": {"kind": execution.owner_kind},
        "client_id": execution.client_id,
        "client_bufnr": execution.client_bufnr,
        "client_state": execution.client_state,
    }


class StartSession:
    def __init__(self, runtime: KernelRuntime, store: SessionStore, events: SessionEventSink) -> None:
        self._runtime = runtime
        self._store = store
        self._events = events

    def execute(self, command: StartSessionCommand) -> Session:
        session = Session(
            notebook_id=command.notebook_id,
            state="starting",
            kernel_name=command.kernel_name,
            last_action="start",
        )
        self._store.save(session)
        self._events.session_updated(command.notebook_id, _session_payload(session))

        session_id, connection = self._runtime.start_managed(command.kernel_name)
        session.session_id = session_id
        session.connection = connection
        session.state = "connected"
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
    def __init__(self, runtime: KernelRuntime, store: SessionStore, events: SessionEventSink) -> None:
        self._runtime = runtime
        self._store = store
        self._events = events

    def begin_execute(self, command: ExecuteCellCommand) -> tuple[Session, CellExecution]:
        session = self._store.get_by_notebook(command.notebook_id)
        if session is None or session.session_id != command.session_id:
            raise ValueError("Unknown notebook session")
        if session.state != "connected":
            raise ValueError("Cannot execute cell without a connected session")
        if session.prepared.state != "ready":
            raise ValueError("Cannot execute cell without a prepared client")

        current_client = CellExecution(
            cell_id=command.cell.cell_id,
            status="busy",
            owner_kind="handler" if command.cell.kind == "magic" else "kernel",
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
        self._store.save_execution(command.notebook_id, current_client)
        self._events.cell_updated(command.notebook_id, _cell_payload(current_client))
        return current_client

    def execute(self, command: ExecuteCellCommand) -> CellExecution:
        session, current_client = self.begin_execute(command)
        return self.finish_execute(command, session, current_client)


class InterruptCell:
    def __init__(self, runtime: KernelRuntime, store: SessionStore, events: SessionEventSink) -> None:
        self._runtime = runtime
        self._store = store
        self._events = events

    def execute(self, command: InterruptCellCommand) -> CellExecution:
        session = self._store.get_by_notebook(command.notebook_id)
        if session is None or session.session_id != command.session_id:
            raise ValueError("Unknown notebook session")
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
        session = self._store.get_by_notebook(command.notebook_id)
        if session is None or session.session_id != command.session_id:
            raise ValueError("Unknown notebook session")
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
        session = self._store.get_by_notebook(command.notebook_id)
        if session is None or session.session_id != command.session_id:
            raise ValueError("Unknown notebook session")

        session.last_action = "disconnect"
        session.last_error = command.reason
        session.prepared = PreparedClient(state="missing", client_state="shutdown")

        if not session.attachable:
            self._store.save(session)
            self._events.session_updated(command.notebook_id, _session_payload(session))
            self._events.prepared_updated(command.notebook_id, _prepared_payload(session.prepared))

            for execution in self._store.list_executions(command.notebook_id):
                if execution.status in {"busy", "follow-up"}:
                    execution.client_state = "shutdown"
                    execution.client_bufnr = -1
                    self._store.save_execution(command.notebook_id, execution)
                    self._events.cell_updated(command.notebook_id, _cell_payload(execution))

            self._runtime.stop_session(session)
            session.state = "stopped"
            self._store.save(session)
            self._events.session_updated(command.notebook_id, _session_payload(session))
            return session

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
        session = self._store.get_by_notebook(command.notebook_id)
        if session is None or session.session_id != command.session_id:
            raise ValueError("Unknown notebook session")
        if session.state != "disconnected":
            raise ValueError("Only disconnected sessions can reconnect")

        session.state = "starting"
        session.last_action = "reconnect"
        session.last_error = ""
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
        session = self._store.get_by_notebook(command.notebook_id)
        if session is None or session.session_id != command.session_id:
            raise ValueError("Unknown notebook session")
        if session.state not in {"starting", "connected", "disconnected", "stopping"}:
            raise ValueError("Cannot stop a session that is not active")

        session.state = "stopping"
        session.last_action = "stop"
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
        session = self._store.get_by_notebook(command.notebook_id)
        if session is None or session.session_id != command.session_id:
            raise ValueError("Unknown notebook session")
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
        session = self._store.get_by_notebook(command.notebook_id)
        if session is None or session.session_id != command.session_id:
            raise ValueError("Unknown notebook session")

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
