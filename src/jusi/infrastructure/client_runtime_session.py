from __future__ import annotations

from typing import Protocol


class ClientRuntimeSession(Protocol):
    def run(self) -> int:
        ...
