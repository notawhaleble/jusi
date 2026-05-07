from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field

from jusi.infrastructure.client_runtime_startup import build_transcript_runtime_startup, transcript_startup_env
from jusi.infrastructure.debug_timing import emit_timing
from jusi.infrastructure.client_runtime_handshake import parse_ready_line
from jusi.infrastructure.client_runtime_protocol import ClientRuntimeCommand, ClientRuntimeSnapshot
from jusi.infrastructure.transcript_runtime_session import TranscriptRuntimeSession


CLIENT_STATUS_POLL_INTERVAL_SECONDS = 0.005


def _default_client_command() -> list[str]:
    return [sys.executable, "-m", "jusi", "client-runtime"]


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
        transcript_startup_env(
            build_transcript_runtime_startup(
                client_id=client_id,
                notebook_id=notebook_id,
                session_id=session_id,
                control_dir=control_dir,
                supervisor_pid=os.getpid(),
            )
        )
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


def _cleanup_control_dir(control_dir: str) -> None:
    if not control_dir:
        return
    shutil.rmtree(control_dir, ignore_errors=True)


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
    transport: dict = field(default_factory=dict)
    process: subprocess.Popen[str] = field(init=False)
    control_dir: str = field(init=False)
    commands_path: str = field(init=False)
    status_path: str = field(init=False)
    plugin_runtime_pid_path: str = field(init=False)
    plugin_runtime_socket_path: str = field(init=False)
    plugin_frontend_actions_path: str = field(init=False)
    runtime_kind: str = "process"

    def __post_init__(self) -> None:
        self.control_dir = tempfile.mkdtemp(prefix=f"jusi-client-{self.client_id}-")
        self.commands_path = os.path.join(self.control_dir, "commands.jsonl")
        self.status_path = os.path.join(self.control_dir, "status.json")
        self.plugin_runtime_pid_path = os.path.join(self.control_dir, "plugin-runtime.pid")
        self.plugin_runtime_socket_path = os.path.join(self.control_dir, "plugin-runtime.sock")
        self.plugin_frontend_actions_path = os.path.join(self.control_dir, "plugin-runtime-actions.jsonl")
        self._frontend_actions_offset = 0
        self.process = _spawn_client_process(
            client_id=self.client_id,
            notebook_id=self.notebook_id,
            session_id=self.session_id,
            control_dir=self.control_dir,
        )
        ready_line = ""
        if self.process.stdout is not None:
            ready_line = self.process.stdout.readline().strip()
        ready = parse_ready_line(ready_line)
        if ready is None:
            self.shutdown("spawn_failed")
            raise RuntimeError(f"Managed client process failed to start for {self.client_id}")
        self.lifecycle.append(f"spawn:{self.process.pid}")
        self._wait_for_status(lambda status: status.lifecycle == ["ready"])

    def _send_command(self, command: ClientRuntimeCommand) -> None:
        with open(self.commands_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(command.to_dict()) + "\n")
            handle.flush()

    def _read_status(self) -> ClientRuntimeSnapshot:
        if not os.path.exists(self.status_path):
            return ClientRuntimeSnapshot()
        with open(self.status_path, "r", encoding="utf-8") as handle:
            try:
                return ClientRuntimeSnapshot.from_dict(json.load(handle))
            except json.JSONDecodeError:
                return ClientRuntimeSnapshot()

    def _wait_for_status(self, predicate, timeout: float = 2.0) -> ClientRuntimeSnapshot:  # type: ignore[no-untyped-def]
        deadline = time.time() + timeout
        last_status = ClientRuntimeSnapshot()
        while time.time() < deadline:
            last_status = self._read_status()
            if last_status.client_id and predicate(last_status):
                return last_status
            if self.process.poll() is not None:
                break
            time.sleep(CLIENT_STATUS_POLL_INTERVAL_SECONDS)
        raise RuntimeError(f"Managed client process did not report expected status for {self.client_id}")

    @property
    def pid(self) -> int | None:
        return self.process.pid

    def is_running(self) -> bool:
        return self.process.poll() is None

    def read_status(self) -> dict:
        return self._read_status()

    def bind(self, client_bufnr: int) -> None:
        start = time.monotonic()
        self._send_command(ClientRuntimeCommand.bind(client_bufnr))
        self._wait_for_status(lambda status: status.client_bufnr == client_bufnr)
        emit_timing(
            "client_runtime.bind",
            client_id=self.client_id,
            client_bufnr=client_bufnr,
            duration_ms=round((time.monotonic() - start) * 1000, 3),
        )
        self.client_bufnr = client_bufnr
        self.view_revision += 1
        self.lifecycle.append(f"bind:{client_bufnr}")

    def activate(self, cell_id: int) -> None:
        start = time.monotonic()
        self._send_command(ClientRuntimeCommand.activate(cell_id))
        self._wait_for_status(lambda status: status.active_cell_id == cell_id)
        emit_timing(
            "client_runtime.activate",
            client_id=self.client_id,
            cell_id=cell_id,
            duration_ms=round((time.monotonic() - start) * 1000, 3),
        )
        self.active_cell_id = cell_id
        self.view_revision += 1
        self.lifecycle.append(f"activate:{cell_id}")

    def update_execution_status(self, status: str) -> None:
        if self.execution_status == status:
            return
        start = time.monotonic()
        self._send_command(ClientRuntimeCommand.execution_status(status))
        self._wait_for_status(lambda snapshot: snapshot.execution_status == status)
        emit_timing(
            "client_runtime.execution_status",
            client_id=self.client_id,
            status=status,
            duration_ms=round((time.monotonic() - start) * 1000, 3),
        )
        self.execution_status = status
        self.view_revision += 1
        self.lifecycle.append(f"status:{status}")

    def append_execution_event(self, event: dict) -> None:
        event_copy = dict(event)
        start = time.monotonic()
        self._send_command(ClientRuntimeCommand.execution_event(event_copy))
        self._wait_for_status(lambda snapshot: event_copy in snapshot.transcript)
        emit_timing(
            "client_runtime.execution_event",
            client_id=self.client_id,
            event_type=str(event_copy.get("type", "")).strip() or "event",
            duration_ms=round((time.monotonic() - start) * 1000, 3),
        )
        self.transcript.append(event_copy)
        self.view_revision += 1
        event_type = str(event_copy.get("type", "")).strip() or "event"
        self.lifecycle.append(f"event:{event_type}")

    def set_transport(self, transport: dict) -> None:
        transport_copy = dict(transport)
        start = time.monotonic()
        self._send_command(ClientRuntimeCommand.transport(transport_copy))
        self._wait_for_status(lambda snapshot: snapshot.transport == transport_copy)
        emit_timing(
            "client_runtime.transport",
            client_id=self.client_id,
            kind=str(transport_copy.get("kind", "")),
            duration_ms=round((time.monotonic() - start) * 1000, 3),
        )
        self.transport = transport_copy
        self.view_revision += 1
        self.lifecycle.append(f"transport:{transport_copy.get('kind', '')}")

    def read_view(self) -> dict:
        snapshot = self.read_status()
        view = {
            "title": snapshot.view_title,
            "lines": list(snapshot.view_lines),
            "execution_status": snapshot.execution_status,
            "active_cell_id": snapshot.active_cell_id,
            "revision": snapshot.view_revision,
        }
        if snapshot.transport:
            view["transport"] = dict(snapshot.transport)
        return view

    def plugin_runtime_is_alive(self) -> bool | None:
        try:
            with open(self.plugin_runtime_pid_path, "r", encoding="utf-8") as handle:
                raw = handle.read().strip()
        except FileNotFoundError:
            return None
        try:
            plugin_pid = int(raw)
        except ValueError:
            return False
        if plugin_pid <= 0:
            return False
        try:
            os.kill(plugin_pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def drain_frontend_actions(self) -> list[dict]:
        if not os.path.exists(self.plugin_frontend_actions_path):
            return []
        actions: list[dict] = []
        with open(self.plugin_frontend_actions_path, "r", encoding="utf-8") as handle:
            handle.seek(self._frontend_actions_offset)
            for raw_line in handle:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    payload = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    actions.append(dict(payload))
            self._frontend_actions_offset = handle.tell()
        return actions

    def shutdown(self, reason: str) -> None:
        if self.shutdown_reason:
            return
        self.shutdown_reason = reason
        self.client_bufnr = -1
        self.view_revision += 1
        self.lifecycle.append(f"shutdown:{reason}")
        self._shutdown_plugin_runtime()
        if self.process.poll() is None:
            self._send_command(ClientRuntimeCommand.shutdown(reason))
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
            self._wait_for_status(lambda status: status.shutdown_reason == reason)
        except RuntimeError:
            pass
        _cleanup_control_dir(self.control_dir)

    def _shutdown_plugin_runtime(self) -> None:
        try:
            with open(self.plugin_runtime_pid_path, "r", encoding="utf-8") as handle:
                raw = handle.read().strip()
        except FileNotFoundError:
            return
        try:
            plugin_pid = int(raw)
        except ValueError:
            return
        if plugin_pid <= 0:
            return
        try:
            os.kill(plugin_pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                os.kill(plugin_pid, 0)
            except ProcessLookupError:
                return
            except PermissionError:
                break
            time.sleep(CLIENT_STATUS_POLL_INTERVAL_SECONDS)
        try:
            os.kill(plugin_pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        finally:
            try:
                os.unlink(self.plugin_runtime_socket_path)
            except FileNotFoundError:
                pass
            try:
                os.unlink(self.plugin_runtime_pid_path)
            except FileNotFoundError:
                pass

    def request_plugin_runtime(self, payload: dict, timeout: float = 2.0) -> dict:
        message_type = str(payload.get("message_type", "")).strip()
        emit_timing(
            "client_runtime.plugin_runtime_request.start",
            client_id=self.client_id,
            message_type=message_type,
            socket_path=self.plugin_runtime_socket_path,
        )
        if not os.path.exists(self.plugin_runtime_socket_path):
            raise RuntimeError("plugin runtime control socket is not available")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(self.plugin_runtime_socket_path)
            client.sendall(json.dumps(dict(payload)).encode("utf-8") + b"\n")
            chunks: list[bytes] = []
            while True:
                chunk = client.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
        raw = b"".join(chunks).decode("utf-8").strip()
        if not raw:
            raise RuntimeError("plugin runtime control response was empty")
        response = json.loads(raw)
        if not isinstance(response, dict):
            raise RuntimeError("plugin runtime control returned invalid response")
        if not bool(response.get("ok", False)):
            raise RuntimeError(str(response.get("error", "plugin runtime control failed")))
        payload = response.get("payload", {})
        emit_timing(
            "client_runtime.plugin_runtime_request.done",
            client_id=self.client_id,
            message_type=message_type,
            response_keys=sorted(list(payload.keys())) if isinstance(payload, dict) else [],
        )
        if isinstance(payload, dict):
            return dict(payload)
        return {}


@dataclass
class InProcessTranscriptHandle:
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
    transport: dict = field(default_factory=dict)
    control_dir: str = field(init=False)
    commands_path: str = field(init=False)
    status_path: str = field(init=False)
    plugin_runtime_pid_path: str = field(init=False)
    plugin_runtime_socket_path: str = field(init=False)
    plugin_frontend_actions_path: str = field(init=False)
    runtime_kind: str = "inprocess"

    def __post_init__(self) -> None:
        self.control_dir = tempfile.mkdtemp(prefix=f"jusi-client-{self.client_id}-")
        self.commands_path = os.path.join(self.control_dir, "commands.jsonl")
        self.status_path = os.path.join(self.control_dir, "status.json")
        self.plugin_runtime_pid_path = os.path.join(self.control_dir, "plugin-runtime.pid")
        self.plugin_runtime_socket_path = os.path.join(self.control_dir, "plugin-runtime.sock")
        self.plugin_frontend_actions_path = os.path.join(self.control_dir, "plugin-runtime-actions.jsonl")
        self._frontend_actions_offset = 0
        self._session = TranscriptRuntimeSession(
            client_id=self.client_id,
            notebook_id=self.notebook_id,
            session_id=self.session_id,
            control_dir=self.control_dir,
        )
        self._session.state.lifecycle.append("ready")
        self._session.write_status()
        self._sync_from_snapshot(self._session.snapshot())
        self.lifecycle = [f"spawn:inprocess:{os.getpid()}"]

    @property
    def pid(self) -> int | None:
        return None

    def is_running(self) -> bool:
        return not bool(self.shutdown_reason)

    def _sync_from_snapshot(self, snapshot: ClientRuntimeSnapshot) -> None:
        self.client_bufnr = snapshot.client_bufnr
        self.active_cell_id = snapshot.active_cell_id
        self.execution_status = snapshot.execution_status
        self.shutdown_reason = snapshot.shutdown_reason
        self.view_revision = snapshot.view_revision
        self.transcript = [dict(item) for item in snapshot.transcript]
        self.transport = dict(snapshot.transport)

    def _apply_command(self, command: ClientRuntimeCommand) -> ClientRuntimeSnapshot:
        self._session.apply_command(command)
        snapshot = self._session.snapshot()
        self._sync_from_snapshot(snapshot)
        return snapshot

    def read_status(self) -> dict:
        return self._session.snapshot().to_dict()

    def bind(self, client_bufnr: int) -> None:
        start = time.monotonic()
        self._apply_command(ClientRuntimeCommand.bind(client_bufnr))
        self.lifecycle.append(f"bind:{client_bufnr}")
        emit_timing(
            "client_runtime.bind",
            client_id=self.client_id,
            client_bufnr=client_bufnr,
            duration_ms=round((time.monotonic() - start) * 1000, 3),
        )

    def activate(self, cell_id: int) -> None:
        start = time.monotonic()
        self._apply_command(ClientRuntimeCommand.activate(cell_id))
        self.lifecycle.append(f"activate:{cell_id}")
        emit_timing(
            "client_runtime.activate",
            client_id=self.client_id,
            cell_id=cell_id,
            duration_ms=round((time.monotonic() - start) * 1000, 3),
        )

    def update_execution_status(self, status: str) -> None:
        if self.execution_status == status:
            return
        start = time.monotonic()
        self._apply_command(ClientRuntimeCommand.execution_status(status))
        self.lifecycle.append(f"status:{status}")
        emit_timing(
            "client_runtime.execution_status",
            client_id=self.client_id,
            status=status,
            duration_ms=round((time.monotonic() - start) * 1000, 3),
        )

    def append_execution_event(self, event: dict) -> None:
        event_copy = dict(event)
        start = time.monotonic()
        self._apply_command(ClientRuntimeCommand.execution_event(event_copy))
        event_type = str(event_copy.get("type", "")).strip() or "event"
        self.lifecycle.append(f"event:{event_type}")
        emit_timing(
            "client_runtime.execution_event",
            client_id=self.client_id,
            event_type=event_type,
            duration_ms=round((time.monotonic() - start) * 1000, 3),
        )

    def set_transport(self, transport: dict) -> None:
        transport_copy = dict(transport)
        start = time.monotonic()
        self._apply_command(ClientRuntimeCommand.transport(transport_copy))
        self.lifecycle.append(f"transport:{transport_copy.get('kind', '')}")
        emit_timing(
            "client_runtime.transport",
            client_id=self.client_id,
            kind=str(transport_copy.get("kind", "")),
            duration_ms=round((time.monotonic() - start) * 1000, 3),
        )

    def read_view(self) -> dict:
        snapshot = self._session.snapshot()
        view = {
            "title": snapshot.view_title,
            "lines": list(snapshot.view_lines),
            "execution_status": snapshot.execution_status,
            "active_cell_id": snapshot.active_cell_id,
            "revision": snapshot.view_revision,
        }
        if snapshot.transport:
            view["transport"] = dict(snapshot.transport)
        return view

    def plugin_runtime_is_alive(self) -> bool | None:
        try:
            with open(self.plugin_runtime_pid_path, "r", encoding="utf-8") as handle:
                raw = handle.read().strip()
        except FileNotFoundError:
            return None
        try:
            plugin_pid = int(raw)
        except ValueError:
            return False
        if plugin_pid <= 0:
            return False
        try:
            os.kill(plugin_pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def drain_frontend_actions(self) -> list[dict]:
        if not os.path.exists(self.plugin_frontend_actions_path):
            return []
        actions: list[dict] = []
        with open(self.plugin_frontend_actions_path, "r", encoding="utf-8") as handle:
            handle.seek(self._frontend_actions_offset)
            for raw_line in handle:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    payload = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    actions.append(dict(payload))
            self._frontend_actions_offset = handle.tell()
        return actions

    def shutdown(self, reason: str) -> None:
        if self.shutdown_reason:
            return
        self._shutdown_plugin_runtime()
        self._apply_command(ClientRuntimeCommand.shutdown(reason))
        self.lifecycle.append(f"shutdown:{reason}")
        _cleanup_control_dir(self.control_dir)

    def _shutdown_plugin_runtime(self) -> None:
        try:
            with open(self.plugin_runtime_pid_path, "r", encoding="utf-8") as handle:
                raw = handle.read().strip()
        except FileNotFoundError:
            return
        try:
            plugin_pid = int(raw)
        except ValueError:
            return
        if plugin_pid <= 0:
            return
        try:
            os.kill(plugin_pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                os.kill(plugin_pid, 0)
            except ProcessLookupError:
                return
            except PermissionError:
                break
            time.sleep(CLIENT_STATUS_POLL_INTERVAL_SECONDS)
        try:
            os.kill(plugin_pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        finally:
            try:
                os.unlink(self.plugin_runtime_socket_path)
            except FileNotFoundError:
                pass
            try:
                os.unlink(self.plugin_runtime_pid_path)
            except FileNotFoundError:
                pass

    def request_plugin_runtime(self, payload: dict, timeout: float = 2.0) -> dict:
        message_type = str(payload.get("message_type", "")).strip()
        emit_timing(
            "client_runtime.plugin_runtime_request.start",
            client_id=self.client_id,
            message_type=message_type,
            socket_path=self.plugin_runtime_socket_path,
        )
        if not os.path.exists(self.plugin_runtime_socket_path):
            raise RuntimeError("plugin runtime control socket is not available")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(self.plugin_runtime_socket_path)
            client.sendall(json.dumps(dict(payload)).encode("utf-8") + b"\n")
            chunks: list[bytes] = []
            while True:
                chunk = client.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
        raw = b"".join(chunks).decode("utf-8").strip()
        if not raw:
            raise RuntimeError("plugin runtime control response was empty")
        response = json.loads(raw)
        if not isinstance(response, dict):
            raise RuntimeError("plugin runtime control returned invalid response")
        if not bool(response.get("ok", False)):
            raise RuntimeError(str(response.get("error", "plugin runtime control failed")))
        response_payload = response.get("payload", {})
        emit_timing(
            "client_runtime.plugin_runtime_request.done",
            client_id=self.client_id,
            message_type=message_type,
            response_keys=sorted(list(response_payload.keys())) if isinstance(response_payload, dict) else [],
        )
        if isinstance(response_payload, dict):
            return dict(response_payload)
        return {}
