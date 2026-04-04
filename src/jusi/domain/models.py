from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SessionState = Literal["idle", "starting", "connected", "disconnected", "stopping", "stopped", "failed"]
PreparedState = Literal["missing", "spawning", "binding", "ready"]
CellState = Literal["pending", "busy", "follow-up", "done", "error", "interrupted", "parked"]
ClientState = Literal["active", "shutting_down", "shutdown"]
ExecutionOwnerKind = Literal["kernel", "handler", "unknown"]


@dataclass
class SessionTarget:
    source: str = ""
    alias: str = ""
    kind: str = ""
    value: str = ""
    config: dict[str, object] = field(default_factory=dict)


@dataclass
class ClientTransport:
    kind: str = ""
    attach_cmd: list[str] = field(default_factory=list)
    attach_env: dict[str, str] = field(default_factory=dict)
    session_id: str = ""
    client_id: str = ""
    handler_id: str = ""


@dataclass
class PreparedClient:
    state: PreparedState = "missing"
    client_id: str = ""
    client_bufnr: int = -1
    client_state: ClientState = "active"
    transport: ClientTransport = field(default_factory=ClientTransport)


@dataclass
class Session:
    notebook_id: str
    session_id: str = ""
    state: SessionState = "idle"
    kernel_name: str = ""
    connection: str = ""
    target: SessionTarget = field(default_factory=SessionTarget)
    expires_at: float | None = None
    frontend_last_ack_at: float | None = None
    frontend_healthcheck_id: str = ""
    frontend_healthcheck_deadline: float | None = None
    last_error: str = ""
    last_action: str = ""
    prepared: PreparedClient = field(default_factory=PreparedClient)


@dataclass
class CellExecution:
    cell_id: int
    status: CellState = "pending"
    owner_kind: ExecutionOwnerKind = "unknown"
    client_id: str = ""
    client_bufnr: int = -1
    client_state: ClientState = "active"
    transport: ClientTransport = field(default_factory=ClientTransport)


@dataclass(frozen=True)
class ExecutableCell:
    cell_id: int
    kind: str
    syntax: str
    main_lines: list[str]
    keep_running: bool = False
