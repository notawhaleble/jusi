from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

SessionState = Literal["idle", "starting", "connected", "disconnected", "stopping", "stopped", "failed"]
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
    plugin_specs: dict[str, dict[str, object]] = field(default_factory=dict)
    palette: dict[str, dict[str, object]] = field(default_factory=dict)


@dataclass
class CellExecution:
    cell_id: int
    status: CellState = "pending"
    owner_kind: ExecutionOwnerKind = "unknown"
    client_id: str = ""
    client_bufnr: int = -1
    client_state: ClientState = "active"
    transport: ClientTransport = field(default_factory=ClientTransport)
    presentation: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutableCell:
    cell_id: int
    kind: str
    syntax: str
    main_lines: list[str]
    keep_running: bool = False


JUSI_HANDLER_HANDOFF_MIME = "application/vnd.jusi.handoff+json"


@dataclass(frozen=True)
class HandlerHandoff:
    handler_id: str
    magic_name: str
    content: str = ""
    meta: dict[str, object] = field(default_factory=dict)


def parse_handler_handoff_payload(
    data: object,
    *,
    metadata: object = None,
) -> HandlerHandoff | None:
    if not isinstance(data, dict):
        return None
    raw_payload = data.get(JUSI_HANDLER_HANDOFF_MIME)
    if raw_payload is None:
        return None
    payload: dict[str, object]
    if isinstance(raw_payload, str):
        try:
            decoded = json.loads(raw_payload)
        except json.JSONDecodeError:
            return None
        if not isinstance(decoded, dict):
            return None
        payload = decoded
    elif isinstance(raw_payload, dict):
        payload = dict(raw_payload)
    else:
        return None
    handler_id = str(payload.get("handler_id", "")).strip()
    magic_name = str(payload.get("magic_name", "")).strip()
    if not handler_id or not magic_name:
        return None
    handoff_meta: dict[str, object] = {}
    if isinstance(payload.get("meta"), dict):
        handoff_meta.update(dict(payload["meta"]))
    if isinstance(metadata, dict):
        raw_meta = metadata.get(JUSI_HANDLER_HANDOFF_MIME)
        if isinstance(raw_meta, dict):
            handoff_meta.update(dict(raw_meta))
    return HandlerHandoff(
        handler_id=handler_id,
        magic_name=magic_name,
        content=str(payload.get("content", "")),
        meta=handoff_meta,
    )
