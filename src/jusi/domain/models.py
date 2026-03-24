from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SessionState = Literal["idle", "starting", "connected", "disconnected", "stopping", "stopped", "failed"]
PreparedState = Literal["missing", "spawning", "binding", "ready"]
CellState = Literal["pending", "busy", "follow-up", "done", "error", "interrupted", "parked"]
ClientState = Literal["active", "shutting_down", "shutdown"]
ExecutionOwnerKind = Literal["kernel", "handler", "unknown"]


@dataclass
class PreparedClient:
    state: PreparedState = "missing"
    client_id: str = ""
    client_bufnr: int = -1
    client_state: ClientState = "active"


@dataclass
class Session:
    notebook_id: str
    session_id: str = ""
    state: SessionState = "idle"
    kernel_name: str = ""
    connection: str = ""
    attachable: bool = False
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


@dataclass(frozen=True)
class ExecutableCell:
    cell_id: int
    kind: str
    syntax: str
    main_lines: list[str]
    keep_running: bool = False
