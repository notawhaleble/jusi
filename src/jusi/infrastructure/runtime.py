from __future__ import annotations

import os
from itertools import count
from types import SimpleNamespace
from typing import Any

from jusi.domain.models import CellExecution, ExecutableCell, Session


class RuntimeDependencyError(RuntimeError):
    """Raised when an optional runtime dependency is required but unavailable."""


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


class InMemoryKernelRuntime:
    def __init__(self) -> None:
        self._session_counter = count(1)
        self._client_counter = count(1)

    def start_managed(self, kernel_name: str) -> tuple[str, str]:
        ident = next(self._session_counter)
        session_id = f"session-{ident}"
        connection = f"inmemory://{kernel_name}/{ident}"
        return session_id, connection

    def prepare_client(self, notebook_id: str, session_id: str) -> str:
        _ = (notebook_id, session_id)
        ident = next(self._client_counter)
        return f"client-{ident}"

    def execute_cell(self, session: Session, cell: ExecutableCell, client: CellExecution) -> str:
        _ = (session, cell, client)
        if cell.keep_running:
            return "busy"
        if cell.kind == "magic":
            return "follow-up"
        return "done"

    def interrupt_kernel(self, session: Session, execution: CellExecution) -> str:
        _ = (session, execution)
        return "interrupted"

    def interrupt_handler(self, session: Session, execution: CellExecution) -> str:
        _ = (session, execution)
        return "interrupted"

    def stop_session(self, session: Session) -> None:
        _ = session

    def shutdown_client(self, session: Session, client_id: str, reason: str) -> None:
        _ = (session, client_id, reason)


class ManagedKernelRuntime:
    def __init__(self) -> None:
        self._client_counter = count(1)
        self._sessions: dict[str, Any] = {}

    def start_managed(self, kernel_name: str) -> tuple[str, str]:
        km, kc = _start_new_kernel(kernel_name=kernel_name)
        session_id = f"managed:{id(km)}"
        self._sessions[session_id] = SimpleNamespace(manager=km, client=kc)
        return session_id, str(getattr(km, "connection_file", ""))

    def prepare_client(self, notebook_id: str, session_id: str) -> str:
        _ = notebook_id
        ident = next(self._client_counter)
        return f"client-{session_id}-{ident}"

    def execute_cell(self, session: Session, cell: ExecutableCell, client: CellExecution) -> str:
        _ = client
        runtime_session = self._require_session(session.session_id)
        code = "\n".join(cell.main_lines)
        if cell.keep_running:
            runtime_session.client.execute(code)
            return "busy"
        if cell.kind == "magic":
            runtime_session.client.execute(code, store_history=False)
            return "follow-up"

        msg_id = runtime_session.client.execute(code)
        status = "done"
        while True:
            message = runtime_session.client.get_iopub_msg(timeout=5)
            if message.get("parent_header", {}).get("msg_id") != msg_id:
                continue
            msg_type = message.get("msg_type", "")
            if msg_type == "error":
                status = "error"
            if msg_type == "status" and message.get("content", {}).get("execution_state") == "idle":
                return status

    def interrupt_kernel(self, session: Session, execution: CellExecution) -> str:
        _ = execution
        runtime_session = self._require_session(session.session_id)
        runtime_session.manager.interrupt_kernel()
        return "interrupted"

    def interrupt_handler(self, session: Session, execution: CellExecution) -> str:
        _ = (session, execution)
        return "interrupted"

    def stop_session(self, session: Session) -> None:
        runtime_session = self._sessions.pop(session.session_id, None)
        if runtime_session is None:
            return
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

    def shutdown_client(self, session: Session, client_id: str, reason: str) -> None:
        _ = (session, client_id, reason)

    def _require_session(self, session_id: str) -> Any:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise RuntimeError(f"Unknown managed runtime session: {session_id}") from exc


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._executions: dict[tuple[str, int], CellExecution] = {}

    def save(self, session: Session) -> None:
        self._sessions[session.notebook_id] = session

    def get_by_notebook(self, notebook_id: str) -> Session | None:
        return self._sessions.get(notebook_id)

    def save_execution(self, notebook_id: str, execution: CellExecution) -> None:
        self._executions[(notebook_id, execution.cell_id)] = execution

    def get_execution(self, notebook_id: str, cell_id: int) -> CellExecution | None:
        return self._executions.get((notebook_id, cell_id))

    def list_executions(self, notebook_id: str) -> list[CellExecution]:
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
