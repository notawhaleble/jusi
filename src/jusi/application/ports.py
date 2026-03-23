from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from jusi.domain.models import CellExecution, ExecutableCell, Session


@dataclass(frozen=True)
class StartSessionCommand:
    notebook_id: str
    kernel_name: str


@dataclass(frozen=True)
class ExecuteCellCommand:
    notebook_id: str
    session_id: str
    cell: ExecutableCell


@dataclass(frozen=True)
class InterruptCellCommand:
    notebook_id: str
    session_id: str
    cell_id: int


@dataclass(frozen=True)
class DisconnectSessionCommand:
    notebook_id: str
    session_id: str
    reason: str


@dataclass(frozen=True)
class ReconnectSessionCommand:
    notebook_id: str
    session_id: str


@dataclass(frozen=True)
class StopSessionCommand:
    notebook_id: str
    session_id: str


class SessionEventSink(Protocol):
    def session_updated(self, notebook_id: str, payload: dict) -> None:
        ...

    def prepared_updated(self, notebook_id: str, payload: dict) -> None:
        ...

    def cell_updated(self, notebook_id: str, payload: dict) -> None:
        ...


class KernelRuntime(Protocol):
    def start_managed(self, kernel_name: str) -> tuple[str, str]:
        """Return session_id and connection reference."""

    def prepare_client(self, notebook_id: str, session_id: str) -> tuple[str, int]:
        """Return client_id and editor-facing client buffer reference."""

    def execute_cell(self, session: Session, cell: ExecutableCell, client: CellExecution) -> str:
        """Execute the cell and return the final cell status."""

    def interrupt_kernel(self, session: Session, execution: CellExecution) -> str:
        """Interrupt a kernel-owned execution and return the resulting cell status."""

    def interrupt_handler(self, session: Session, execution: CellExecution) -> str:
        """Interrupt a handler-owned execution and return the resulting cell status."""


class SessionStore(Protocol):
    def save(self, session: Session) -> None:
        ...

    def get_by_notebook(self, notebook_id: str) -> Optional[Session]:
        ...

    def save_execution(self, notebook_id: str, execution: CellExecution) -> None:
        ...

    def get_execution(self, notebook_id: str, cell_id: int) -> Optional[CellExecution]:
        ...

    def list_executions(self, notebook_id: str) -> list[CellExecution]:
        ...
