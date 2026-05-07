from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from jusi.infrastructure.client_runtime_host import require_registered_runtime_mode


class LiveClientController(Protocol):
    def on_frontend_message(self, _context: object, message_type: str, payload: dict[str, Any]) -> None:
        ...

    def interrupt(self) -> None:
        ...

    def stop(self) -> None:
        ...


@dataclass
class ActiveClientController:
    client_id: str
    controller: LiveClientController
    runtime_mode: str = ""
    handler_id: str = ""
    context: object | None = None


class LiveClientControllerRegistry:
    def __init__(self) -> None:
        self._active: dict[tuple[str, str], ActiveClientController] = {}

    @staticmethod
    def _finalize_removed_controller(active: ActiveClientController | None) -> None:
        if active is not None:
            active.controller.stop()

    def register(self, session_id: str, client_id: str, active: ActiveClientController) -> None:
        self._active[(session_id, client_id)] = active

    def replace(self, session_id: str, client_id: str, active: ActiveClientController) -> ActiveClientController | None:
        key = (session_id, client_id)
        previous = self._active.get(key)
        self._active[key] = active
        self._finalize_removed_controller(previous)
        return previous

    def get(self, session_id: str, client_id: str) -> ActiveClientController | None:
        return self._active.get((session_id, client_id))

    def remove_client(self, session_id: str, client_id: str) -> None:
        active = self._active.pop((session_id, client_id), None)
        self._finalize_removed_controller(active)

    def interrupt_client(self, session_id: str, client_id: str) -> None:
        active = self._active.get((session_id, client_id))
        if active is None:
            return
        active.controller.interrupt()

    def dispatch_frontend_message(
        self,
        session_id: str,
        client_id: str,
        *,
        handler_id: str,
        message_type: str,
        payload: dict[str, Any],
    ) -> ActiveClientController:
        active = self._active.get((session_id, client_id))
        if active is None:
            raise ValueError("No active handler is registered for the client")
        mode = require_registered_runtime_mode(active.runtime_mode)
        if not mode.accepts_frontend_messages:
            raise ValueError("Active client runtime does not accept handler messages")
        if active.handler_id != str(handler_id).strip():
            raise ValueError("Handler message does not match the active handler")
        active.controller.on_frontend_message(active.context, message_type, payload)
        return active

    def remove_session(self, session_id: str) -> None:
        stale = [key for key in self._active if key[0] == session_id]
        for key in stale:
            active = self._active.pop(key, None)
            self._finalize_removed_controller(active)

    def stop_all(self) -> None:
        stale = list(self._active.values())
        self._active.clear()
        for active in stale:
            self._finalize_removed_controller(active)
