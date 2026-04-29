from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable

from jusi.infrastructure.client_runtime_protocol import ClientRuntimeCommand, ClientRuntimeSnapshot
from jusi.infrastructure.client_view import build_client_view


@dataclass
class TranscriptRuntimeState:
    client_id: str
    notebook_id: str
    session_id: str
    client_bufnr: int = -1
    active_cell_id: int | None = None
    execution_status: str = ""
    view_revision: int = 0
    lifecycle: list[str] = field(default_factory=list)
    transcript: list[dict] = field(default_factory=list)
    view_title: str = "idle"
    view_lines: list[str] = field(default_factory=list)
    shutdown_reason: str = ""
    transport: dict = field(default_factory=dict)


class TranscriptRuntimeSession:
    def __init__(
        self,
        *,
        client_id: str,
        notebook_id: str,
        session_id: str,
        control_dir: str,
        keep_running: Callable[[], bool] | None = None,
        supervisor_is_alive: Callable[[], bool] | None = None,
        request_shutdown: Callable[[str], None] | None = None,
        poll_interval_seconds: float = 0.01,
    ) -> None:
        self._state = TranscriptRuntimeState(
            client_id=client_id,
            notebook_id=notebook_id,
            session_id=session_id,
        )
        self._control_dir = control_dir
        self._commands_path = os.path.join(self._control_dir, "commands.jsonl") if self._control_dir else ""
        self._status_path = os.path.join(self._control_dir, "status.json") if self._control_dir else ""
        self._command_offset = 0
        self._keep_running = keep_running or (lambda: True)
        self._supervisor_is_alive = supervisor_is_alive or (lambda: True)
        self._request_shutdown = request_shutdown or (lambda _reason: None)
        self._poll_interval_seconds = poll_interval_seconds

    @property
    def state(self) -> TranscriptRuntimeState:
        return self._state

    def mark_shutdown(self, reason: str) -> None:
        self._state.shutdown_reason = reason
        self._state.client_bufnr = -1
        self._state.view_revision += 1
        self._state.lifecycle.append(f"shutdown:{reason}")
        self.write_status()

    def write_status(self) -> None:
        if not self._status_path:
            return
        os.makedirs(self._control_dir, exist_ok=True)
        self._rebuild_view()
        with open(self._status_path, "w", encoding="utf-8") as handle:
            json.dump(self.snapshot().to_dict(), handle)

    def snapshot(self) -> ClientRuntimeSnapshot:
        return ClientRuntimeSnapshot(
            client_id=self._state.client_id,
            notebook_id=self._state.notebook_id,
            session_id=self._state.session_id,
            client_bufnr=self._state.client_bufnr,
            active_cell_id=self._state.active_cell_id,
            execution_status=self._state.execution_status,
            view_revision=self._state.view_revision,
            lifecycle=list(self._state.lifecycle),
            transcript=[dict(item) for item in self._state.transcript],
            view_title=self._state.view_title,
            view_lines=list(self._state.view_lines),
            shutdown_reason=self._state.shutdown_reason,
            transport=dict(self._state.transport),
        )

    def apply_command(self, command: ClientRuntimeCommand) -> None:
        kind = command.kind
        payload = command.payload
        if kind == "bind":
            self._state.client_bufnr = int(payload.get("client_bufnr", -1))
            self._state.view_revision += 1
            self._state.lifecycle.append(f"bind:{self._state.client_bufnr}")
            self.write_status()
            return
        if kind == "activate":
            self._state.active_cell_id = int(payload.get("cell_id", -1))
            self._state.view_revision += 1
            self._state.lifecycle.append(f"activate:{self._state.active_cell_id}")
            self.write_status()
            return
        if kind == "execution_status":
            self._state.execution_status = str(payload.get("status", "")).strip()
            self._state.view_revision += 1
            self._state.lifecycle.append(f"status:{self._state.execution_status}")
            self.write_status()
            return
        if kind == "execution_event":
            event = payload.get("event", {})
            if isinstance(event, dict):
                self._state.transcript.append(dict(event))
                self._state.view_revision += 1
                event_type = str(event.get("type", "")).strip() or "event"
                self._state.lifecycle.append(f"event:{event_type}")
                self.write_status()
            return
        if kind == "transport":
            transport = payload.get("transport", {})
            if isinstance(transport, dict):
                self._state.transport = dict(transport)
                self._state.view_revision += 1
                self._state.lifecycle.append(f"transport:{self._state.transport.get('kind', '')}")
                self.write_status()
            return
        if kind == "shutdown":
            self.mark_shutdown(str(payload.get("reason", "")).strip())

    def poll_commands(self) -> None:
        if not self._commands_path or not os.path.exists(self._commands_path):
            return
        with open(self._commands_path, "r", encoding="utf-8") as handle:
            handle.seek(self._command_offset)
            for line in handle:
                raw = line.strip()
                if not raw:
                    continue
                self.apply_command(ClientRuntimeCommand.from_dict(json.loads(raw)))
            self._command_offset = handle.tell()

    def run(self) -> int:
        self._state.lifecycle.append("ready")
        self.write_status()
        while self._keep_running():
            if not self._supervisor_is_alive():
                self._request_shutdown("supervisor_lost")
                break
            self.poll_commands()
            if self._state.shutdown_reason:
                break
            time.sleep(self._poll_interval_seconds)
        return 0

    def _rebuild_view(self) -> None:
        view = build_client_view(
            client_id=self._state.client_id,
            session_id=self._state.session_id,
            client_bufnr=self._state.client_bufnr,
            active_cell_id=self._state.active_cell_id,
            execution_status=self._state.execution_status,
            transcript=self._state.transcript,
        )
        self._state.view_title = str(view["title"])
        self._state.view_lines = list(view["lines"])
