from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jusi.domain.models import ClientTransport


CLIENT_RUNTIME_UPDATE_KINDS = frozenset(
    {
        "execution_event",
        "execution_status",
        "transport",
        "channel_event",
        "action_request",
        "live_handler_message",
    }
)


@dataclass(frozen=True)
class ClientRuntimeUpdate:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized = self.kind.strip()
        if normalized not in CLIENT_RUNTIME_UPDATE_KINDS:
            raise ValueError(f"Unsupported client runtime update kind: {self.kind}")
        object.__setattr__(self, "kind", normalized)
        object.__setattr__(self, "payload", dict(self.payload))

    @classmethod
    def execution_event(cls, event: dict[str, Any]) -> "ClientRuntimeUpdate":
        return cls("execution_event", {"event": dict(event)})

    @classmethod
    def execution_status(cls, status: str) -> "ClientRuntimeUpdate":
        return cls("execution_status", {"status": str(status)})

    @classmethod
    def transport(cls, transport: ClientTransport) -> "ClientRuntimeUpdate":
        return cls("transport", {"transport": transport})

    @classmethod
    def channel_event(cls, event_type: str, payload: dict[str, Any]) -> "ClientRuntimeUpdate":
        return cls("channel_event", {"event_type": str(event_type), "payload": dict(payload)})

    @classmethod
    def action_request(cls, action_type: str, payload: dict[str, Any]) -> "ClientRuntimeUpdate":
        return cls("action_request", {"action_type": str(action_type), "payload": dict(payload)})

    @classmethod
    def live_handler_message(cls, message_type: str, payload: dict[str, Any]) -> "ClientRuntimeUpdate":
        return cls("live_handler_message", {"message_type": str(message_type), "payload": dict(payload)})
