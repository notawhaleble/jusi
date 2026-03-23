from __future__ import annotations

from itertools import count

from jusi.domain.models import CellExecution, ExecutableCell, Session


class InMemoryKernelRuntime:
    def __init__(self) -> None:
        self._session_counter = count(1)
        self._client_counter = count(1)

    def start_managed(self, kernel_name: str) -> tuple[str, str]:
        ident = next(self._session_counter)
        session_id = f"session-{ident}"
        connection = f"inmemory://{kernel_name}/{ident}"
        return session_id, connection

    def prepare_client(self, notebook_id: str, session_id: str) -> tuple[str, int]:
        ident = next(self._client_counter)
        return f"client-{ident}", 90 + ident

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
