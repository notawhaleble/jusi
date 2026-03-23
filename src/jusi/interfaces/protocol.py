from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional

PROTOCOL_VERSION = 1


class ProtocolError(ValueError):
    """Raised when a message does not match the Jusi protocol."""


@dataclass(frozen=True)
class Envelope:
    version: int
    kind: str
    type: str
    payload: Dict[str, Any]
    request_id: str = ""
    ok: Optional[bool] = None
    error: Optional[Dict[str, str]] = None

    def as_dict(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "version": self.version,
            "kind": self.kind,
            "type": self.type,
            "payload": self.payload,
        }
        if self.request_id:
            body["request_id"] = self.request_id
        if self.ok is not None:
            body["ok"] = self.ok
        if self.error is not None:
            body["error"] = self.error
        return body


@dataclass(frozen=True)
class StartSessionRequest:
    notebook_id: str
    kernel_name: str


@dataclass(frozen=True)
class ExecuteCellRequest:
    notebook_id: str
    session_id: str
    cell_id: int
    kind: str
    syntax: str
    main_lines: List[str]
    keep_running: bool = False


@dataclass(frozen=True)
class InterruptCellRequest:
    notebook_id: str
    session_id: str
    cell_id: int


@dataclass(frozen=True)
class DisconnectSessionRequest:
    notebook_id: str
    session_id: str
    reason: str


@dataclass(frozen=True)
class ReconnectSessionRequest:
    notebook_id: str
    session_id: str


@dataclass(frozen=True)
class StopSessionRequest:
    notebook_id: str
    session_id: str


def parse_envelope(raw: str) -> Envelope:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError("Message is not valid JSON") from exc
    return parse_envelope_dict(data)


def parse_envelope_dict(data: Mapping[str, Any]) -> Envelope:
    version = int(data.get("version", 0))
    if version != PROTOCOL_VERSION:
        raise ProtocolError("Unsupported protocol version")
    kind = str(data.get("kind", ""))
    msg_type = str(data.get("type", ""))
    payload = data.get("payload", {})
    if kind not in {"request", "response", "event"}:
        raise ProtocolError("Envelope kind is invalid")
    if not msg_type:
        raise ProtocolError("Envelope type is required")
    if not isinstance(payload, dict):
        raise ProtocolError("Envelope payload must be an object")
    request_id = str(data.get("request_id", ""))
    if kind in {"request", "response"} and not request_id:
        raise ProtocolError("Request and response envelopes require request_id")
    ok_value = data.get("ok")
    ok = bool(ok_value) if ok_value is not None else None
    error = data.get("error")
    if error is not None and not isinstance(error, dict):
        raise ProtocolError("Envelope error must be an object")
    return Envelope(
        version=version,
        kind=kind,
        type=msg_type,
        payload=dict(payload),
        request_id=request_id,
        ok=ok,
        error=dict(error) if error is not None else None,
    )


def parse_start_session(payload: Mapping[str, Any]) -> StartSessionRequest:
    notebook_id = str(payload.get("notebook_id", "")).strip()
    kernel_name = str(payload.get("kernel_name", "")).strip() or "python3"
    if not notebook_id:
        raise ProtocolError("start_session requires notebook_id")
    return StartSessionRequest(notebook_id=notebook_id, kernel_name=kernel_name)


def parse_execute_cell(payload: Mapping[str, Any]) -> ExecuteCellRequest:
    notebook_id = str(payload.get("notebook_id", "")).strip()
    session_id = str(payload.get("session_id", "")).strip()
    cell = payload.get("cell")
    if not notebook_id:
        raise ProtocolError("execute_cell requires notebook_id")
    if not session_id:
        raise ProtocolError("execute_cell requires session_id")
    if not isinstance(cell, dict):
        raise ProtocolError("execute_cell requires cell payload")
    cell_id = int(cell.get("id", 0))
    if cell_id <= 0:
        raise ProtocolError("execute_cell requires positive cell id")
    kind = str(cell.get("kind", "")).strip() or "code"
    syntax = str(cell.get("syntax", "")).strip() or "python"
    main_lines = cell.get("main_lines", [])
    if not isinstance(main_lines, list) or any(not isinstance(line, str) for line in main_lines):
        raise ProtocolError("execute_cell main_lines must be a list of strings")
    return ExecuteCellRequest(
        notebook_id=notebook_id,
        session_id=session_id,
        cell_id=cell_id,
        kind=kind,
        syntax=syntax,
        main_lines=list(main_lines),
        keep_running=bool(cell.get("keep_running", False)),
    )


def parse_interrupt_cell(payload: Mapping[str, Any]) -> InterruptCellRequest:
    notebook_id = str(payload.get("notebook_id", "")).strip()
    session_id = str(payload.get("session_id", "")).strip()
    cell_id = int(payload.get("cell_id", 0))
    if not notebook_id:
        raise ProtocolError("interrupt_cell requires notebook_id")
    if not session_id:
        raise ProtocolError("interrupt_cell requires session_id")
    if cell_id <= 0:
        raise ProtocolError("interrupt_cell requires positive cell id")
    return InterruptCellRequest(notebook_id=notebook_id, session_id=session_id, cell_id=cell_id)


def parse_disconnect_session(payload: Mapping[str, Any]) -> DisconnectSessionRequest:
    notebook_id = str(payload.get("notebook_id", "")).strip()
    session_id = str(payload.get("session_id", "")).strip()
    reason = str(payload.get("reason", "")).strip() or "disconnected"
    if not notebook_id:
        raise ProtocolError("disconnect_session requires notebook_id")
    if not session_id:
        raise ProtocolError("disconnect_session requires session_id")
    return DisconnectSessionRequest(notebook_id=notebook_id, session_id=session_id, reason=reason)


def parse_reconnect_session(payload: Mapping[str, Any]) -> ReconnectSessionRequest:
    notebook_id = str(payload.get("notebook_id", "")).strip()
    session_id = str(payload.get("session_id", "")).strip()
    if not notebook_id:
        raise ProtocolError("reconnect_session requires notebook_id")
    if not session_id:
        raise ProtocolError("reconnect_session requires session_id")
    return ReconnectSessionRequest(notebook_id=notebook_id, session_id=session_id)


def parse_stop_session(payload: Mapping[str, Any]) -> StopSessionRequest:
    notebook_id = str(payload.get("notebook_id", "")).strip()
    session_id = str(payload.get("session_id", "")).strip()
    if not notebook_id:
        raise ProtocolError("stop_session requires notebook_id")
    if not session_id:
        raise ProtocolError("stop_session requires session_id")
    return StopSessionRequest(notebook_id=notebook_id, session_id=session_id)


def response_envelope(request: Envelope, ok: bool, payload: Optional[Dict[str, Any]] = None) -> Envelope:
    return Envelope(
        version=PROTOCOL_VERSION,
        kind="response",
        type=request.type,
        request_id=request.request_id,
        ok=ok,
        payload=payload or {},
    )


def error_response(request: Envelope, code: str, message: str) -> Envelope:
    return Envelope(
        version=PROTOCOL_VERSION,
        kind="response",
        type=request.type,
        request_id=request.request_id,
        ok=False,
        payload={},
        error={"code": code, "message": message},
    )


def event_envelope(event_type: str, payload: Dict[str, Any]) -> Envelope:
    return Envelope(
        version=PROTOCOL_VERSION,
        kind="event",
        type=event_type,
        payload=payload,
    )


def dump_envelope(envelope: Envelope) -> str:
    return json.dumps(envelope.as_dict(), sort_keys=True)


def dump_envelopes(envelopes: Iterable[Envelope]) -> List[str]:
    return [dump_envelope(envelope) for envelope in envelopes]
