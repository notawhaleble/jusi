from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


CLIENT_RUNTIME_COMMAND_KINDS = frozenset(
    {
        "bind",
        "activate",
        "execution_status",
        "execution_event",
        "transport",
        "shutdown",
    }
)


@dataclass(frozen=True)
class ClientRuntimeCommand:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized = self.kind.strip()
        if normalized not in CLIENT_RUNTIME_COMMAND_KINDS:
            raise ValueError(f"Unsupported client runtime command kind: {self.kind}")
        object.__setattr__(self, "kind", normalized)
        object.__setattr__(self, "payload", dict(self.payload))

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, **dict(self.payload)}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ClientRuntimeCommand":
        if not isinstance(payload, dict):
            raise ValueError("Client runtime command payload must be a dict")
        kind = str(payload.get("kind", "")).strip()
        data = dict(payload)
        data.pop("kind", None)
        return cls(kind=kind, payload=data)

    @classmethod
    def bind(cls, client_bufnr: int) -> "ClientRuntimeCommand":
        return cls("bind", {"client_bufnr": int(client_bufnr)})

    @classmethod
    def activate(cls, cell_id: int) -> "ClientRuntimeCommand":
        return cls("activate", {"cell_id": int(cell_id)})

    @classmethod
    def execution_status(cls, status: str) -> "ClientRuntimeCommand":
        return cls("execution_status", {"status": str(status)})

    @classmethod
    def execution_event(cls, event: dict[str, Any]) -> "ClientRuntimeCommand":
        return cls("execution_event", {"event": dict(event)})

    @classmethod
    def transport(cls, transport: dict[str, Any]) -> "ClientRuntimeCommand":
        return cls("transport", {"transport": dict(transport)})

    @classmethod
    def shutdown(cls, reason: str) -> "ClientRuntimeCommand":
        return cls("shutdown", {"reason": str(reason)})


@dataclass
class ClientRuntimeSnapshot:
    client_id: str = ""
    notebook_id: str = ""
    session_id: str = ""
    client_bufnr: int = -1
    active_cell_id: int | None = None
    execution_status: str = ""
    view_revision: int = 0
    lifecycle: list[str] = field(default_factory=list)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    view_title: str = ""
    view_lines: list[str] = field(default_factory=list)
    shutdown_reason: str = ""
    transport: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "client_id": self.client_id,
            "notebook_id": self.notebook_id,
            "session_id": self.session_id,
            "client_bufnr": self.client_bufnr,
            "active_cell_id": self.active_cell_id,
            "execution_status": self.execution_status,
            "view_revision": self.view_revision,
            "lifecycle": list(self.lifecycle),
            "transcript": [dict(item) for item in self.transcript],
            "view_title": self.view_title,
            "view_lines": list(self.view_lines),
            "shutdown_reason": self.shutdown_reason,
        }
        if self.transport:
            payload["transport"] = dict(self.transport)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ClientRuntimeSnapshot":
        if not isinstance(payload, dict):
            return cls()
        transcript_raw = payload.get("transcript", [])
        transcript = [dict(item) for item in transcript_raw if isinstance(item, dict)]
        lifecycle_raw = payload.get("lifecycle", [])
        view_lines_raw = payload.get("view_lines", [])
        active_cell_id_raw = payload.get("active_cell_id")
        active_cell_id = None
        if isinstance(active_cell_id_raw, int):
            active_cell_id = active_cell_id_raw
        elif active_cell_id_raw is not None:
            try:
                active_cell_id = int(active_cell_id_raw)
            except (TypeError, ValueError):
                active_cell_id = None
        transport_raw = payload.get("transport", {})
        transport = dict(transport_raw) if isinstance(transport_raw, dict) else {}
        return cls(
            client_id=str(payload.get("client_id", "")),
            notebook_id=str(payload.get("notebook_id", "")),
            session_id=str(payload.get("session_id", "")),
            client_bufnr=int(payload.get("client_bufnr", -1)),
            active_cell_id=active_cell_id,
            execution_status=str(payload.get("execution_status", "")),
            view_revision=int(payload.get("view_revision", 0)),
            lifecycle=[str(item) for item in lifecycle_raw],
            transcript=transcript,
            view_title=str(payload.get("view_title", "")),
            view_lines=[str(item) for item in view_lines_raw],
            shutdown_reason=str(payload.get("shutdown_reason", "")),
            transport=transport,
        )
