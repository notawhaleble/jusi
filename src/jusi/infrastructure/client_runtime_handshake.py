from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ClientRuntimeReadyMessage:
    mode: str

    def to_wire(self) -> str:
        return json.dumps({"kind": "ready", "mode": self.mode})


def parse_ready_line(raw: str) -> ClientRuntimeReadyMessage | None:
    value = str(raw).strip()
    if not value:
        return None
    if value == "ready":
        return ClientRuntimeReadyMessage(mode="unknown")
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get("kind", "")).strip() != "ready":
        return None
    return ClientRuntimeReadyMessage(mode=str(payload.get("mode", "")).strip() or "unknown")
