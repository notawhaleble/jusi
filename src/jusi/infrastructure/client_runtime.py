from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field

def _default_client_command() -> list[str]:
    return [sys.executable, "-m", "jusi", "client-process"]


def build_client_command() -> list[str]:
    raw = os.environ.get("JUSI_CLIENT_CMD", "").strip()
    if not raw:
        return _default_client_command()
    command = shlex.split(raw)
    if not command:
        raise RuntimeError("JUSI_CLIENT_CMD did not produce an executable command")
    return command


def _build_client_env(client_id: str, notebook_id: str, session_id: str, control_dir: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "JUSI_CLIENT_ID": client_id,
            "JUSI_NOTEBOOK_ID": notebook_id,
            "JUSI_SESSION_ID": session_id,
            "JUSI_CLIENT_CONTROL_DIR": control_dir,
            "JUSI_SUPERVISOR_PID": str(os.getpid()),
        }
    )
    return env


def _spawn_client_process(client_id: str, notebook_id: str, session_id: str, control_dir: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        build_client_command(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env=_build_client_env(client_id, notebook_id, session_id, control_dir),
    )


@dataclass
class ProcessClientHandle:
    client_id: str
    notebook_id: str
    session_id: str
    client_bufnr: int = -1
    active_cell_id: int | None = None
    execution_status: str = ""
    shutdown_reason: str = ""
    view_revision: int = 0
    lifecycle: list[str] = field(default_factory=list)
    transcript: list[dict] = field(default_factory=list)
    process: subprocess.Popen[str] = field(init=False)
    control_dir: str = field(init=False)
    commands_path: str = field(init=False)
    status_path: str = field(init=False)
    runtime_kind: str = "process"

    def __post_init__(self) -> None:
        self.control_dir = tempfile.mkdtemp(prefix=f"jusi-client-{self.client_id}-")
        self.commands_path = os.path.join(self.control_dir, "commands.jsonl")
        self.status_path = os.path.join(self.control_dir, "status.json")
        self.process = _spawn_client_process(
            client_id=self.client_id,
            notebook_id=self.notebook_id,
            session_id=self.session_id,
            control_dir=self.control_dir,
        )
        ready_line = ""
        if self.process.stdout is not None:
            ready_line = self.process.stdout.readline().strip()
        if ready_line != "ready":
            self.shutdown("spawn_failed")
            raise RuntimeError(f"Managed client process failed to start for {self.client_id}")
        self.lifecycle.append(f"spawn:{self.process.pid}")
        self._wait_for_status(lambda status: status.get("lifecycle") == ["ready"])

    def _send_command(self, payload: dict) -> None:
        with open(self.commands_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")
            handle.flush()

    def _read_status(self) -> dict:
        if not os.path.exists(self.status_path):
            return {}
        with open(self.status_path, "r", encoding="utf-8") as handle:
            try:
                return json.load(handle)
            except json.JSONDecodeError:
                return {}

    def _wait_for_status(self, predicate, timeout: float = 2.0) -> dict:  # type: ignore[no-untyped-def]
        deadline = time.time() + timeout
        last_status: dict = {}
        while time.time() < deadline:
            last_status = self._read_status()
            if last_status and predicate(last_status):
                return last_status
            if self.process.poll() is not None:
                break
            time.sleep(0.02)
        raise RuntimeError(f"Managed client process did not report expected status for {self.client_id}")

    @property
    def pid(self) -> int | None:
        return self.process.pid

    def is_running(self) -> bool:
        return self.process.poll() is None

    def read_status(self) -> dict:
        return self._read_status()

    def bind(self, client_bufnr: int) -> None:
        self._send_command({"kind": "bind", "client_bufnr": client_bufnr})
        self._wait_for_status(lambda status: status.get("client_bufnr") == client_bufnr)
        self.client_bufnr = client_bufnr
        self.view_revision += 1
        self.lifecycle.append(f"bind:{client_bufnr}")

    def activate(self, cell_id: int) -> None:
        self._send_command({"kind": "activate", "cell_id": cell_id})
        self._wait_for_status(lambda status: status.get("active_cell_id") == cell_id)
        self.active_cell_id = cell_id
        self.view_revision += 1
        self.lifecycle.append(f"activate:{cell_id}")

    def update_execution_status(self, status: str) -> None:
        if self.execution_status == status:
            return
        self._send_command({"kind": "execution_status", "status": status})
        self._wait_for_status(lambda snapshot: snapshot.get("execution_status") == status)
        self.execution_status = status
        self.view_revision += 1
        self.lifecycle.append(f"status:{status}")

    def append_execution_event(self, event: dict) -> None:
        event_copy = dict(event)
        self._send_command({"kind": "execution_event", "event": event_copy})
        self._wait_for_status(lambda snapshot: event_copy in snapshot.get("transcript", []))
        self.transcript.append(event_copy)
        self.view_revision += 1
        event_type = str(event_copy.get("type", "")).strip() or "event"
        self.lifecycle.append(f"event:{event_type}")

    def read_view(self) -> dict:
        snapshot = self.read_status()
        return {
            "title": snapshot.get("view_title", ""),
            "lines": list(snapshot.get("view_lines", [])),
            "execution_status": snapshot.get("execution_status", ""),
            "active_cell_id": snapshot.get("active_cell_id"),
            "revision": int(snapshot.get("view_revision", 0)),
        }

    def shutdown(self, reason: str) -> None:
        if self.shutdown_reason:
            return
        self.shutdown_reason = reason
        self.client_bufnr = -1
        self.view_revision += 1
        self.lifecycle.append(f"shutdown:{reason}")
        if self.process.poll() is None:
            self._send_command({"kind": "shutdown", "reason": reason})
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    self.process.wait(timeout=2)
        if self.process.stdout is not None:
            self.process.stdout.close()
        if self.process.stderr is not None:
            self.process.stderr.close()
        try:
            self._wait_for_status(lambda status: status.get("shutdown_reason") == reason)
        except RuntimeError:
            pass
