from __future__ import annotations

import os
from queue import Empty
from dataclasses import dataclass, field
from itertools import count
from types import SimpleNamespace
from typing import Any, Protocol

from jusi.domain.models import CellExecution, ExecutableCell, Session
from jusi.infrastructure.client_view import build_client_view
from jusi.infrastructure.client_runtime import ProcessClientHandle


class RuntimeDependencyError(RuntimeError):
    """Raised when an optional runtime dependency is required but unavailable."""


class RuntimeClientHandle(Protocol):
    def bind(self, client_bufnr: int) -> None:
        ...

    def activate(self, cell_id: int) -> None:
        ...

    def update_execution_status(self, status: str) -> None:
        ...

    def append_execution_event(self, event: dict) -> None:
        ...

    def read_view(self) -> dict:
        ...

    def shutdown(self, reason: str) -> None:
        ...


@dataclass
class PendingInputRequest:
    client_id: str
    cell_id: int
    msg_id: str


@dataclass
class InMemoryClientHandle:
    client_id: str
    notebook_id: str
    session_id: str
    client_bufnr: int = -1
    active_cell_id: int | None = None
    execution_status: str = ""
    shutdown_reason: str = ""
    view_revision: int = 0
    lifecycle: list[str] = field(default_factory=list)
    transcript: list[dict] = field(default_factory=list)

    def bind(self, client_bufnr: int) -> None:
        self.client_bufnr = client_bufnr
        self.view_revision += 1
        self.lifecycle.append(f"bind:{client_bufnr}")

    def activate(self, cell_id: int) -> None:
        self.active_cell_id = cell_id
        self.view_revision += 1
        self.lifecycle.append(f"activate:{cell_id}")

    def update_execution_status(self, status: str) -> None:
        if self.execution_status == status:
            return
        self.execution_status = status
        self.view_revision += 1
        self.lifecycle.append(f"status:{status}")

    def append_execution_event(self, event: dict) -> None:
        self.transcript.append(dict(event))
        self.view_revision += 1
        event_type = str(event.get("type", "")).strip() or "event"
        self.lifecycle.append(f"event:{event_type}")

    def read_view(self) -> dict:
        view = build_client_view(
            client_id=self.client_id,
            session_id=self.session_id,
            client_bufnr=self.client_bufnr,
            active_cell_id=self.active_cell_id,
            execution_status=self.execution_status,
            transcript=self.transcript,
        )
        view["revision"] = self.view_revision
        return view

    def shutdown(self, reason: str) -> None:
        self.shutdown_reason = reason
        self.client_bufnr = -1
        self.view_revision += 1
        self.lifecycle.append(f"shutdown:{reason}")


@dataclass
class ManagedClientHandle(ProcessClientHandle):
    runtime_kind: str = "managed"


@dataclass
class RuntimeClient:
    client_id: str
    notebook_id: str
    session_id: str
    handle: RuntimeClientHandle
    state: str = "prepared"
    client_bufnr: int = -1
    cell_id: int | None = None
    shutdown_reason: str = ""


@dataclass
class RuntimeSessionClients:
    clients: dict[str, RuntimeClient] = field(default_factory=dict)
    prepared_client_id: str = ""


class ClientRegistryRuntime:
    def __init__(self) -> None:
        self._client_counter = count(1)
        self._session_clients: dict[str, RuntimeSessionClients] = {}

    def prepare_client(self, notebook_id: str, session_id: str) -> str:
        session_clients = self._ensure_session_clients(session_id)
        client_id = self._next_client_id(session_id)
        session_clients.clients[client_id] = RuntimeClient(
            client_id=client_id,
            notebook_id=notebook_id,
            session_id=session_id,
            handle=self._build_client_handle(client_id, notebook_id, session_id),
        )
        session_clients.prepared_client_id = client_id
        return client_id

    def bind_prepared_client(self, session: Session, client_id: str, client_bufnr: int) -> None:
        runtime_client = self._require_client(session.session_id, client_id)
        if runtime_client.state != "prepared":
            raise ValueError("Prepared client is no longer awaiting binding")
        runtime_client.handle.bind(client_bufnr)
        runtime_client.client_bufnr = client_bufnr

    def activate_client(self, session: Session, client_id: str, cell_id: int) -> None:
        session_clients = self._ensure_session_clients(session.session_id)
        runtime_client = self._require_client(session.session_id, client_id)
        if session_clients.prepared_client_id != client_id:
            raise ValueError("Prepared client id does not match runtime state")
        if runtime_client.client_bufnr < 0:
            raise ValueError("Prepared client is not bound")
        runtime_client.handle.activate(cell_id)
        runtime_client.state = "active"
        runtime_client.cell_id = cell_id
        session_clients.prepared_client_id = ""

    def update_client_execution_status(self, session: Session, client_id: str, status: str) -> None:
        runtime_client = self._require_client(session.session_id, client_id)
        runtime_client.handle.update_execution_status(status)

    def append_client_execution_event(self, session: Session, client_id: str, event: dict) -> None:
        runtime_client = self._require_client(session.session_id, client_id)
        runtime_client.handle.append_execution_event(event)

    def read_client_view(self, session: Session, client_id: str) -> dict:
        runtime_client = self._require_client(session.session_id, client_id)
        return runtime_client.handle.read_view()

    def shutdown_client(self, session: Session, client_id: str, reason: str) -> None:
        session_clients = self._session_clients.get(session.session_id)
        if session_clients is None:
            return
        runtime_client = session_clients.clients.get(client_id)
        if runtime_client is None:
            return
        runtime_client.handle.shutdown(reason)
        runtime_client.state = "shutdown"
        runtime_client.shutdown_reason = reason
        runtime_client.client_bufnr = -1
        if session_clients.prepared_client_id == client_id:
            session_clients.prepared_client_id = ""
        session_clients.clients.pop(client_id, None)

    def release_session_clients(self, session_id: str, reason: str) -> None:
        session_clients = self._session_clients.pop(session_id, None)
        if session_clients is None:
            return
        for runtime_client in session_clients.clients.values():
            runtime_client.handle.shutdown(reason)
            runtime_client.state = "shutdown"
            runtime_client.shutdown_reason = reason
            runtime_client.client_bufnr = -1

    def get_client(self, session_id: str, client_id: str) -> RuntimeClient | None:
        session_clients = self._session_clients.get(session_id)
        if session_clients is None:
            return None
        return session_clients.clients.get(client_id)

    def list_clients(self, session_id: str) -> list[RuntimeClient]:
        session_clients = self._session_clients.get(session_id)
        if session_clients is None:
            return []
        return list(session_clients.clients.values())

    def _next_client_id(self, session_id: str) -> str:
        _ = session_id
        ident = next(self._client_counter)
        return f"client-{ident}"

    def _build_client_handle(self, client_id: str, notebook_id: str, session_id: str) -> RuntimeClientHandle:
        return InMemoryClientHandle(
            client_id=client_id,
            notebook_id=notebook_id,
            session_id=session_id,
        )

    def _ensure_session_clients(self, session_id: str) -> RuntimeSessionClients:
        session_clients = self._session_clients.get(session_id)
        if session_clients is None:
            session_clients = RuntimeSessionClients()
            self._session_clients[session_id] = session_clients
        return session_clients

    def _require_client(self, session_id: str, client_id: str) -> RuntimeClient:
        session_clients = self._session_clients.get(session_id)
        if session_clients is None or client_id not in session_clients.clients:
            raise ValueError("Runtime client id does not match current session state")
        return session_clients.clients[client_id]


def _start_new_kernel(kernel_name: str) -> tuple[Any, Any]:
    try:
        from jupyter_client.manager import start_new_kernel
        from jupyter_client.kernelspec import NoSuchKernel
    except ModuleNotFoundError as exc:
        raise RuntimeDependencyError(
            "Managed runtime requires jupyter_client to be installed"
        ) from exc

    try:
        return start_new_kernel(kernel_name=kernel_name)
    except NoSuchKernel as exc:
        raise RuntimeDependencyError(
            "Managed runtime requires an installed kernelspec for "
            f"'{kernel_name}'. Install ipykernel in the active environment."
        ) from exc


class InMemoryKernelRuntime(ClientRegistryRuntime):
    def __init__(self) -> None:
        super().__init__()
        self._session_counter = count(1)

    def start_managed(self, kernel_name: str) -> tuple[str, str]:
        ident = next(self._session_counter)
        session_id = f"session-{ident}"
        connection = f"inmemory://{kernel_name}/{ident}"
        return session_id, connection

    def execute_cell(self, session: Session, cell: ExecutableCell, client: CellExecution) -> str:
        _ = (session, cell, client)
        self.append_client_execution_event(
            session,
            client.client_id,
            {"type": "execution_started", "cell_id": client.cell_id, "kind": cell.kind, "syntax": cell.syntax},
        )
        if cell.keep_running:
            self.update_client_execution_status(session, client.client_id, "busy")
            self.append_client_execution_event(
                session,
                client.client_id,
                {"type": "execution_state", "status": "busy"},
            )
            return "busy"
        if cell.kind == "magic":
            self.update_client_execution_status(session, client.client_id, "follow-up")
            self.append_client_execution_event(
                session,
                client.client_id,
                {"type": "execution_finished", "status": "follow-up"},
            )
            return "follow-up"
        self.update_client_execution_status(session, client.client_id, "done")
        self.append_client_execution_event(
            session,
            client.client_id,
            {"type": "execution_finished", "status": "done"},
        )
        return "done"

    def interrupt_kernel(self, session: Session, execution: CellExecution) -> str:
        _ = (session, execution)
        self.update_client_execution_status(session, execution.client_id, "interrupted")
        self.append_client_execution_event(
            session,
            execution.client_id,
            {"type": "execution_interrupted", "status": "interrupted"},
        )
        return "interrupted"

    def interrupt_handler(self, session: Session, execution: CellExecution) -> str:
        _ = (session, execution)
        self.update_client_execution_status(session, execution.client_id, "interrupted")
        self.append_client_execution_event(
            session,
            execution.client_id,
            {"type": "execution_interrupted", "status": "interrupted"},
        )
        return "interrupted"

    def disconnect_session(self, session: Session, reason: str) -> None:
        self.release_session_clients(session.session_id, reason=reason)

    def stop_session(self, session: Session) -> None:
        self.release_session_clients(session.session_id, reason="session_stop")


class ManagedKernelRuntime(ClientRegistryRuntime):
    def __init__(self) -> None:
        super().__init__()
        self._sessions: dict[str, Any] = {}

    def supports_background_execute(self) -> bool:
        return True

    def supports_background_stop(self) -> bool:
        return True

    def start_managed(self, kernel_name: str) -> tuple[str, str]:
        km, kc = _start_new_kernel(kernel_name=kernel_name)
        session_id = f"managed:{id(km)}"
        self._sessions[session_id] = SimpleNamespace(
            manager=km,
            client=kc,
            interrupted_client_ids=set(),
            pending_inputs={},
        )
        return session_id, str(getattr(km, "connection_file", ""))

    def _build_client_handle(self, client_id: str, notebook_id: str, session_id: str) -> RuntimeClientHandle:
        return ManagedClientHandle(
            client_id=client_id,
            notebook_id=notebook_id,
            session_id=session_id,
        )

    def execute_cell(self, session: Session, cell: ExecutableCell, client: CellExecution) -> str:
        runtime_session = self._require_session(session.session_id)
        code = "\n".join(cell.main_lines)
        self.append_client_execution_event(
            session,
            client.client_id,
            {"type": "execution_started", "cell_id": client.cell_id, "kind": cell.kind, "syntax": cell.syntax},
        )
        if cell.keep_running:
            runtime_session.client.execute(code)
            self.update_client_execution_status(session, client.client_id, "busy")
            self.append_client_execution_event(
                session,
                client.client_id,
                {"type": "execution_state", "status": "busy"},
            )
            return "busy"
        if cell.kind == "magic":
            runtime_session.client.execute(code, store_history=False)
            self.update_client_execution_status(session, client.client_id, "follow-up")
            self.append_client_execution_event(
                session,
                client.client_id,
                {"type": "execution_finished", "status": "follow-up"},
            )
            return "follow-up"

        msg_id = runtime_session.client.execute(code)
        return self._drive_execution(runtime_session, session, client, msg_id)

    def reply_input(self, session: Session, execution: CellExecution, value: str) -> str:
        runtime_session = self._require_session(session.session_id)
        pending_input = runtime_session.pending_inputs.get(execution.client_id)
        if pending_input is None or pending_input.cell_id != execution.cell_id:
            raise ValueError("No pending input_request for the tracked cell client")
        reply_input = getattr(runtime_session.client, "input", None)
        if not callable(reply_input):
            raise RuntimeError("Managed runtime client does not support stdin replies")
        reply_input(value)
        runtime_session.pending_inputs.pop(execution.client_id, None)
        return self._drive_execution(runtime_session, session, execution, pending_input.msg_id)

    def _drive_execution(self, runtime_session: Any, session: Session, client: CellExecution, msg_id: str) -> str:
        status = client.status if client.status in {"error", "follow-up", "interrupted"} else "done"
        while True:
            stdin_message = self._try_get_stdin_request(runtime_session.client, msg_id)
            if stdin_message is not None:
                runtime_session.pending_inputs[client.client_id] = PendingInputRequest(
                    client_id=client.client_id,
                    cell_id=client.cell_id,
                    msg_id=msg_id,
                )
                self.append_client_execution_event(
                    session,
                    client.client_id,
                    {
                        "type": "input_request",
                        "prompt": str(stdin_message.get("content", {}).get("prompt", "")),
                        "password": bool(stdin_message.get("content", {}).get("password", False)),
                    },
                )
                return "busy"

            message = self._try_get_iopub_message(runtime_session.client)
            if message is None:
                continue
            if message.get("parent_header", {}).get("msg_id") != msg_id:
                continue
            result = self._handle_iopub_message(runtime_session, session, client.client_id, message, status)
            if result is None:
                continue
            status = result
            if message.get("msg_type", "") == "status" and message.get("content", {}).get("execution_state") == "idle":
                runtime_session.pending_inputs.pop(client.client_id, None)
                return status

    def _handle_iopub_message(self, runtime_session: Any, session: Session, client_id: str, message: dict, status: str) -> str | None:
        msg_type = message.get("msg_type", "")
        if msg_type == "execute_input":
            self.append_client_execution_event(
                session,
                client_id,
                {
                    "type": "execute_input",
                    "execution_count": message.get("content", {}).get("execution_count"),
                    "code": str(message.get("content", {}).get("code", "")),
                },
            )
            return None
        if msg_type == "stream":
            self.append_client_execution_event(
                session,
                client_id,
                {
                    "type": "stream",
                    "name": message.get("content", {}).get("name", ""),
                    "text": message.get("content", {}).get("text", ""),
                },
            )
            return None
        if msg_type == "error":
            self.append_client_execution_event(
                session,
                client_id,
                {
                    "type": "error",
                    "ename": message.get("content", {}).get("ename", ""),
                    "evalue": message.get("content", {}).get("evalue", ""),
                    "traceback": list(message.get("content", {}).get("traceback", [])),
                },
            )
            return "error"
        if msg_type == "display_data":
            self.append_client_execution_event(
                session,
                client_id,
                {
                    "type": "display_data",
                    "data": dict(message.get("content", {}).get("data", {})),
                    "display_id": str(message.get("content", {}).get("transient", {}).get("display_id", "")),
                },
            )
            return None
        if msg_type == "update_display_data":
            self.append_client_execution_event(
                session,
                client_id,
                {
                    "type": "update_display_data",
                    "data": dict(message.get("content", {}).get("data", {})),
                    "display_id": str(message.get("content", {}).get("transient", {}).get("display_id", "")),
                },
            )
            return None
        if msg_type == "execute_result":
            self.append_client_execution_event(
                session,
                client_id,
                {
                    "type": "execute_result",
                    "data": dict(message.get("content", {}).get("data", {})),
                },
            )
            return None
        if msg_type == "clear_output":
            self.append_client_execution_event(
                session,
                client_id,
                {
                    "type": "clear_output",
                    "wait": bool(message.get("content", {}).get("wait", False)),
                },
            )
            return None
        if msg_type == "comm_open":
            self.append_client_execution_event(
                session,
                client_id,
                {
                    "type": "comm_open",
                    "comm_id": str(message.get("content", {}).get("comm_id", "")),
                    "target_name": str(message.get("content", {}).get("target_name", "")),
                    "data": dict(message.get("content", {}).get("data", {})),
                },
            )
            return None
        if msg_type == "comm_msg":
            self.append_client_execution_event(
                session,
                client_id,
                {
                    "type": "comm_msg",
                    "comm_id": str(message.get("content", {}).get("comm_id", "")),
                    "data": dict(message.get("content", {}).get("data", {})),
                },
            )
            return None
        if msg_type == "comm_close":
            self.append_client_execution_event(
                session,
                client_id,
                {
                    "type": "comm_close",
                    "comm_id": str(message.get("content", {}).get("comm_id", "")),
                    "data": dict(message.get("content", {}).get("data", {})),
                },
            )
            return None
        if msg_type == "status" and message.get("content", {}).get("execution_state") == "idle":
            if client_id in getattr(runtime_session, "interrupted_client_ids", set()):
                status = "interrupted"
                runtime_session.interrupted_client_ids.discard(client_id)
            self.update_client_execution_status(session, client_id, status)
            self.append_client_execution_event(
                session,
                client_id,
                {"type": "execution_finished", "status": status},
            )
            return status
        return None

    def _try_get_iopub_message(self, kernel_client: Any) -> dict | None:
        try:
            return kernel_client.get_iopub_msg(timeout=0.1)
        except Empty:
            return None

    def _try_get_stdin_request(self, kernel_client: Any, msg_id: str) -> dict | None:
        get_stdin_msg = getattr(kernel_client, "get_stdin_msg", None)
        if get_stdin_msg is None:
            return None
        try:
            message = get_stdin_msg(timeout=0.0)
        except Empty:
            return None
        if message.get("parent_header", {}).get("msg_id") != msg_id:
            return None
        if message.get("msg_type", "") != "input_request":
            return None
        return message

    def interrupt_kernel(self, session: Session, execution: CellExecution) -> str:
        runtime_session = self._require_session(session.session_id)
        runtime_session.manager.interrupt_kernel()
        runtime_session.interrupted_client_ids.add(execution.client_id)
        runtime_session.pending_inputs.pop(execution.client_id, None)
        self.update_client_execution_status(session, execution.client_id, "interrupted")
        self.append_client_execution_event(
            session,
            execution.client_id,
            {"type": "execution_interrupted", "status": "interrupted"},
        )
        return "interrupted"

    def interrupt_handler(self, session: Session, execution: CellExecution) -> str:
        runtime_session = self._require_session(session.session_id)
        runtime_session.interrupted_client_ids.add(execution.client_id)
        runtime_session.pending_inputs.pop(execution.client_id, None)
        self.update_client_execution_status(session, execution.client_id, "interrupted")
        self.append_client_execution_event(
            session,
            execution.client_id,
            {"type": "execution_interrupted", "status": "interrupted"},
        )
        return "interrupted"

    def disconnect_session(self, session: Session, reason: str) -> None:
        self.release_session_clients(session.session_id, reason=reason)

    def shutdown_client(self, session: Session, client_id: str, reason: str) -> None:
        runtime_session = self._sessions.get(session.session_id)
        if runtime_session is not None:
            runtime_session.pending_inputs.pop(client_id, None)
            runtime_session.interrupted_client_ids.discard(client_id)
        super().shutdown_client(session, client_id, reason)

    def stop_session(self, session: Session) -> None:
        self.release_session_clients(session.session_id, reason="session_stop")
        runtime_session = self._sessions.pop(session.session_id, None)
        if runtime_session is None:
            return
        runtime_session.pending_inputs.clear()
        try:
            runtime_session.client.stop_channels()
        except Exception:
            pass
        try:
            runtime_session.manager.shutdown_kernel(now=True)
        except Exception:
            pass
        try:
            runtime_session.manager.cleanup_resources()
        except Exception:
            pass

    def _require_session(self, session_id: str) -> Any:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise RuntimeError(f"Unknown managed runtime session: {session_id}") from exc


class InMemorySessionStore:
    def __init__(self) -> None:
        from threading import RLock

        self._lock = RLock()
        self._sessions: dict[str, Session] = {}
        self._executions: dict[tuple[str, int], CellExecution] = {}

    def save(self, session: Session) -> None:
        with self._lock:
            self._sessions[session.notebook_id] = session

    def get_by_notebook(self, notebook_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(notebook_id)

    def save_execution(self, notebook_id: str, execution: CellExecution) -> None:
        with self._lock:
            self._executions[(notebook_id, execution.cell_id)] = execution

    def get_execution(self, notebook_id: str, cell_id: int) -> CellExecution | None:
        with self._lock:
            return self._executions.get((notebook_id, cell_id))

    def list_executions(self, notebook_id: str) -> list[CellExecution]:
        with self._lock:
            return [
                execution
                for (stored_notebook_id, _cell_id), execution in self._executions.items()
                if stored_notebook_id == notebook_id
            ]


def build_runtime() -> InMemoryKernelRuntime | ManagedKernelRuntime:
    mode = os.environ.get("JUSI_RUNTIME", "inmemory").strip().lower()
    if mode == "managed":
        return ManagedKernelRuntime()
    return InMemoryKernelRuntime()
