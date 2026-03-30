from __future__ import annotations

import json
import os
import signal
import sys
import time
from dataclasses import dataclass, field

from jusi.infrastructure.client_view import build_client_view


@dataclass
class ClientProcessState:
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


class ClientProcessRunner:
    def __init__(self) -> None:
        self._running = True
        self._state = ClientProcessState(
            client_id=os.environ.get("JUSI_CLIENT_ID", ""),
            notebook_id=os.environ.get("JUSI_NOTEBOOK_ID", ""),
            session_id=os.environ.get("JUSI_SESSION_ID", ""),
        )
        self._control_dir = os.environ.get("JUSI_CLIENT_CONTROL_DIR", "").strip()
        self._commands_path = os.path.join(self._control_dir, "commands.jsonl") if self._control_dir else ""
        self._status_path = os.path.join(self._control_dir, "status.json") if self._control_dir else ""
        self._command_offset = 0
        self._supervisor_pid = self._parse_supervisor_pid(os.environ.get("JUSI_SUPERVISOR_PID", ""))

    @staticmethod
    def _parse_supervisor_pid(raw: str) -> int:
        try:
            value = int(str(raw).strip())
        except ValueError:
            return 0
        return value if value > 0 else 0

    def _stop(self, _signum: int, _frame: object) -> None:
        self._running = False

    def _supervisor_is_alive(self) -> bool:
        if self._supervisor_pid <= 0:
            return True
        if os.getppid() != self._supervisor_pid:
            return False
        try:
            os.kill(self._supervisor_pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _mark_shutdown(self, reason: str) -> None:
        self._state.shutdown_reason = reason
        self._state.client_bufnr = -1
        self._state.view_revision += 1
        self._state.lifecycle.append(f"shutdown:{reason}")
        self._write_status()
        self._running = False

    def _write_status(self) -> None:
        if not self._status_path:
            return
        os.makedirs(self._control_dir, exist_ok=True)
        self._rebuild_view()
        with open(self._status_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "client_id": self._state.client_id,
                    "notebook_id": self._state.notebook_id,
                    "session_id": self._state.session_id,
                    "client_bufnr": self._state.client_bufnr,
                    "active_cell_id": self._state.active_cell_id,
                    "execution_status": self._state.execution_status,
                    "view_revision": self._state.view_revision,
                    "lifecycle": list(self._state.lifecycle),
                    "transcript": list(self._state.transcript),
                    "view_title": self._state.view_title,
                    "view_lines": list(self._state.view_lines),
                    "shutdown_reason": self._state.shutdown_reason,
                },
                handle,
            )

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

    def _apply_command(self, command: dict) -> None:
        kind = str(command.get("kind", "")).strip()
        if kind == "bind":
            self._state.client_bufnr = int(command.get("client_bufnr", -1))
            self._state.view_revision += 1
            self._state.lifecycle.append(f"bind:{self._state.client_bufnr}")
            self._write_status()
            return
        if kind == "activate":
            self._state.active_cell_id = int(command.get("cell_id", -1))
            self._state.view_revision += 1
            self._state.lifecycle.append(f"activate:{self._state.active_cell_id}")
            self._write_status()
            return
        if kind == "execution_status":
            self._state.execution_status = str(command.get("status", "")).strip()
            self._state.view_revision += 1
            self._state.lifecycle.append(f"status:{self._state.execution_status}")
            self._write_status()
            return
        if kind == "execution_event":
            event = command.get("event", {})
            if isinstance(event, dict):
                self._state.transcript.append(dict(event))
                self._state.view_revision += 1
                event_type = str(event.get("type", "")).strip() or "event"
                self._state.lifecycle.append(f"event:{event_type}")
                self._write_status()
            return
        if kind == "shutdown":
            self._mark_shutdown(str(command.get("reason", "")).strip())

    def _poll_commands(self) -> None:
        if not self._commands_path or not os.path.exists(self._commands_path):
            return
        with open(self._commands_path, "r", encoding="utf-8") as handle:
            handle.seek(self._command_offset)
            for line in handle:
                raw = line.strip()
                if not raw:
                    continue
                self._apply_command(json.loads(raw))
            self._command_offset = handle.tell()

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)

        sys.stdout.write("ready\n")
        sys.stdout.flush()
        self._state.lifecycle.append("ready")
        self._write_status()

        while self._running:
            if not self._supervisor_is_alive():
                self._mark_shutdown("supervisor_lost")
                break
            self._poll_commands()
            time.sleep(0.1)
        return 0


def run_client_process() -> int:
    return ClientProcessRunner().run()
