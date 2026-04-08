from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from jusi.domain.models import CellExecution, ClientTransport, ExecutableCell, HandlerHandoff, Session, SessionTarget


@dataclass(frozen=True)
class StartSessionCommand:
    notebook_id: str
    kernel_name: str
    target: SessionTarget


@dataclass(frozen=True)
class AttachSessionCommand:
    notebook_id: str
    target: SessionTarget


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


@dataclass(frozen=True)
class ShutdownClientCommand:
    notebook_id: str
    session_id: str
    cell_id: int
    client_id: str
    reason: str


@dataclass(frozen=True)
class InputReplyCommand:
    notebook_id: str
    session_id: str
    cell_id: int
    client_id: str
    value: str


@dataclass(frozen=True)
class HealthcheckReplyCommand:
    notebook_id: str
    session_id: str
    healthcheck_id: str


@dataclass(frozen=True)
class HandlerMessageCommand:
    notebook_id: str
    session_id: str
    client_id: str
    handler_id: str
    message_type: str
    payload: dict


class SessionEventSink(Protocol):
    def session_updated(self, notebook_id: str, payload: dict) -> None:
        ...

    def cell_updated(self, notebook_id: str, payload: dict) -> None:
        ...


class KernelRuntime(Protocol):
    def start_target(self, target: SessionTarget, kernel_name: str) -> tuple[str, str]:
        """Start a new session for the resolved target and return session_id and connection."""

    def attach_target(self, target: SessionTarget) -> tuple[str, str]:
        """Attach to an existing external target and return session_id and connection."""

    def start_managed(self, kernel_name: str) -> tuple[str, str]:
        """Return session_id and connection reference."""

    def prepare_client(self, notebook_id: str, session_id: str) -> str:
        """Return a backend-owned execution client id."""

    def activate_client(self, session: Session, client_id: str, cell_id: int) -> None:
        """Move an execution client into active cell ownership."""

    def update_client_execution_status(self, session: Session, client_id: str, status: str) -> None:
        """Publish the current execution-facing status for an active client."""

    def append_client_execution_event(self, session: Session, client_id: str, event: dict) -> None:
        """Append a structured execution event to the active client transcript."""

    def set_client_transport(self, session: Session, client_id: str, transport: ClientTransport) -> None:
        """Publish transport metadata for a client."""

    def read_client_view(self, session: Session, client_id: str) -> dict:
        """Read the current backend-owned client view snapshot."""

    def execute_cell(self, session: Session, cell: ExecutableCell, client: CellExecution) -> str:
        """Execute the cell and return the final cell status."""

    def reply_input(self, session: Session, execution: CellExecution, value: str) -> str:
        """Resume a pending input_request for the active execution and return the resulting status."""

    def interrupt_kernel(self, session: Session, execution: CellExecution) -> str:
        """Interrupt a kernel-owned execution and return the resulting cell status."""

    def interrupt_handler(self, session: Session, execution: CellExecution) -> str:
        """Interrupt a handler-owned execution and return the resulting cell status."""

    def disconnect_session(self, session: Session, reason: str) -> None:
        """Release runtime-owned client resources for a recoverable disconnect."""

    def stop_session(self, session: Session) -> None:
        """Stop or detach the runtime resources associated with the session."""

    def shutdown_client(self, session: Session, client_id: str, reason: str) -> None:
        """Tear down a specific client lifecycle if the runtime owns one."""

    def close(self) -> None:
        """Sweep any runtime-owned resources during backend root-process shutdown."""

    def sync_disconnect_deadline(self, session: Session, expires_at: float | None) -> float | None:
        """Persist or fetch any shared disconnect deadline for the session."""

    def expire_session(self, session: Session) -> None:
        """Perform final timeout teardown for the session."""

    def consume_handler_handoff(self, session: Session, client_id: str) -> HandlerHandoff | None:
        """Consume the latest kernel-emitted handler handoff for the active client, if any."""


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

    def list_sessions(self) -> list[Session]:
        ...
