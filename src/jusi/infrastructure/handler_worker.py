from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from queue import Queue
from typing import Any, Callable

from jusi.domain.models import ClientTransport, ExecutableCell
from jusi.plugins import DisplayHandlerRegistry, HandlerContext, build_display_handler_registry


@dataclass(frozen=True)
class HandlerWorkerStartup:
    notebook_id: str
    session_id: str
    client_id: str
    cell_id: int
    handler_id: str
    magic_name: str
    cell: ExecutableCell
    content: str = ""
    meta: dict[str, object] | None = None


def _worker_command() -> list[str]:
    return [sys.executable, "-m", "jusi", "handler-worker"]


def _worker_env(startup: HandlerWorkerStartup) -> dict[str, str]:
    env = os.environ.copy()
    package_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    pythonpath_entries = [package_root]
    inherited_pythonpath = str(env.get("PYTHONPATH", "")).strip()
    if inherited_pythonpath:
        pythonpath_entries.append(inherited_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
    env["JUSI_HANDLER_STARTUP_JSON"] = json.dumps(
        {
            "notebook_id": startup.notebook_id,
            "session_id": startup.session_id,
            "client_id": startup.client_id,
            "cell_id": startup.cell_id,
            "handler_id": startup.handler_id,
            "magic_name": startup.magic_name,
            "content": startup.content,
            "meta": dict(startup.meta or {}),
            "cell": {
                "cell_id": startup.cell.cell_id,
                "kind": startup.cell.kind,
                "syntax": startup.cell.syntax,
                "main_lines": list(startup.cell.main_lines),
                "keep_running": bool(startup.cell.keep_running),
            },
        }
    )
    env["JUSI_SUPERVISOR_PID"] = str(os.getpid())
    return env


class HandlerWorkerProcess:
    def __init__(
        self,
        *,
        startup: HandlerWorkerStartup,
        append_execution_event: Callable[[dict[str, Any]], None],
        update_execution_status: Callable[[str], None],
        set_client_transport: Callable[[ClientTransport], None],
        emit_handler_message: Callable[[str, dict[str, Any]], None],
        invoke_backend_action: Callable[[str, dict[str, Any]], dict[str, Any]],
        on_exit: Callable[[str], None],
    ) -> None:
        self._startup = startup
        self._append_execution_event = append_execution_event
        self._update_execution_status = update_execution_status
        self._set_client_transport = set_client_transport
        self._emit_handler_message = emit_handler_message
        self._invoke_backend_action = invoke_backend_action
        self._on_exit = on_exit
        self._process = subprocess.Popen(
            _worker_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env=_worker_env(startup),
        )
        self._startup_queue: Queue[dict[str, Any]] = Queue()
        self._stop_requested = False
        self._closed = False
        self._reader = threading.Thread(target=self._read_stdout_loop, daemon=True)
        self._reader.start()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def wait_started(self, timeout: float = 5.0) -> str:
        deadline = time.time() + timeout
        saw_ready = False
        while time.time() < deadline:
            remaining = max(0.05, deadline - time.time())
            try:
                message = self._startup_queue.get(timeout=min(0.1, remaining))
            except Exception:
                if self._process.poll() is not None:
                    break
                continue
            kind = str(message.get("kind", "")).strip()
            if kind == "ready":
                saw_ready = True
                continue
            if kind == "execute_result":
                return str(message.get("status", "")).strip() or "follow-up"
        if not saw_ready and self._process.poll() is not None:
            raise RuntimeError(f"Handler worker exited before ready for {self._startup.handler_id}")
        raise RuntimeError(f"Handler worker did not report execute_result for {self._startup.handler_id}")

    def on_frontend_message(self, _context: object, message_type: str, payload: dict[str, Any]) -> None:
        self._send(
            {
                "kind": "frontend_message",
                "message_type": message_type,
                "payload": dict(payload),
            }
        )

    def interrupt(self) -> None:
        self._send({"kind": "interrupt"})

    def stop(self) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._process.poll() is None:
            self._stop_requested = True
            self._send({"kind": "stop"})
            self._terminate_with_timeout()
        self._close_pipes()
        if threading.current_thread() is not self._reader and self._reader.is_alive():
            self._reader.join(timeout=1.0)

    def _terminate_with_timeout(self) -> None:
        try:
            self._process.wait(timeout=2.0)
            self._close_pipes()
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(self._process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            self._process.wait(timeout=2.0)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(self._process.pid, signal.SIGKILL)
        except ProcessLookupError:
            self._close_pipes()
            return
        self._process.wait(timeout=2.0)
        self._close_pipes()

    def _send(self, message: dict[str, Any]) -> None:
        if self._process.stdin is None or self._process.poll() is not None:
            return
        self._process.stdin.write(json.dumps(message) + "\n")
        self._process.stdin.flush()

    def _read_stdout_loop(self) -> None:
        assert self._process.stdout is not None
        for raw_line in self._process.stdout:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                message = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            kind = str(message.get("kind", "")).strip()
            if kind in {"ready", "execute_result"}:
                self._startup_queue.put(message)
                continue
            if kind == "execution_event":
                event = message.get("event", {})
                if isinstance(event, dict):
                    self._append_execution_event(dict(event))
                continue
            if kind == "status":
                self._update_execution_status(str(message.get("status", "")).strip())
                continue
            if kind == "transport":
                payload = message.get("transport", {})
                if isinstance(payload, dict):
                    self._set_client_transport(
                        ClientTransport(
                            kind=str(payload.get("kind", "")).strip(),
                            attach_cmd=[str(item) for item in payload.get("attach_cmd", []) if isinstance(item, str)],
                            attach_env={
                                str(key): str(value)
                                for key, value in dict(payload.get("attach_env", {})).items()
                                if isinstance(key, str)
                            },
                            session_id=str(payload.get("session_id", "")).strip(),
                            client_id=str(payload.get("client_id", "")).strip(),
                            handler_id=str(payload.get("handler_id", "")).strip(),
                        )
                    )
                continue
            if kind == "channel_event":
                event_type = str(message.get("event_type", "")).strip()
                payload = dict(message.get("payload", {}))
                self._append_execution_event(
                    {
                        "type": "handler_channel_event",
                        "event_type": event_type,
                        "payload": payload,
                    }
                )
                self._emit_handler_message(event_type, payload)
                continue
            if kind == "action_request":
                action_type = str(message.get("action_type", "")).strip()
                payload = dict(message.get("payload", {}))
                self._append_execution_event(
                    {
                        "type": "frontend_action_request",
                        "action_type": action_type,
                        "payload": payload,
                    }
                )
                self._emit_handler_message(
                    "action_request",
                    {
                        "action_type": action_type,
                        "payload": payload,
                    },
                )
                continue
            if kind == "live_handler_message":
                self._emit_handler_message(
                    str(message.get("message_type", "")).strip(),
                    dict(message.get("payload", {})),
                )
                continue
            if kind == "backend_action_request":
                request_id = str(message.get("request_id", "")).strip()
                action_name = str(message.get("action_name", "")).strip()
                payload = dict(message.get("payload", {}))
                try:
                    response_payload = self._invoke_backend_action(action_name, payload)
                    response = {
                        "kind": "backend_action_response",
                        "request_id": request_id,
                        "ok": True,
                        "payload": response_payload,
                    }
                except Exception as exc:
                    response = {
                        "kind": "backend_action_response",
                        "request_id": request_id,
                        "ok": False,
                        "error": str(exc),
                        "payload": {},
                    }
                self._send(response)
        self._process.wait()
        self._close_pipes()
        if self._stop_requested:
            return
        exit_status = "error"
        if self._process.returncode == 0:
            exit_status = "done"
        self._on_exit(exit_status)

    def _close_pipes(self) -> None:
        if self._process.stdin is not None and not self._process.stdin.closed:
            self._process.stdin.close()
        if self._process.stdout is not None and not self._process.stdout.closed:
            self._process.stdout.close()
        if self._process.stderr is not None and not self._process.stderr.closed:
            self._process.stderr.close()


def run_handler_worker() -> int:
    raw_startup = os.environ.get("JUSI_HANDLER_STARTUP_JSON", "").strip()
    if not raw_startup:
        sys.stderr.write("missing JUSI_HANDLER_STARTUP_JSON\n")
        sys.stderr.flush()
        return 2
    startup_payload = json.loads(raw_startup)
    cell_payload = dict(startup_payload.get("cell", {}))
    startup = HandlerWorkerStartup(
        notebook_id=str(startup_payload.get("notebook_id", "")).strip(),
        session_id=str(startup_payload.get("session_id", "")).strip(),
        client_id=str(startup_payload.get("client_id", "")).strip(),
        cell_id=int(startup_payload.get("cell_id", 0)),
        handler_id=str(startup_payload.get("handler_id", "")).strip(),
        magic_name=str(startup_payload.get("magic_name", "")).strip(),
        content=str(startup_payload.get("content", "")),
        meta=dict(startup_payload.get("meta", {})) if isinstance(startup_payload.get("meta"), dict) else {},
        cell=ExecutableCell(
            cell_id=int(cell_payload.get("cell_id", startup_payload.get("cell_id", 0))),
            kind=str(cell_payload.get("kind", "")).strip(),
            syntax=str(cell_payload.get("syntax", "")).strip(),
            main_lines=[str(line) for line in cell_payload.get("main_lines", [])],
            keep_running=bool(cell_payload.get("keep_running", False)),
        ),
    )
    registry = build_display_handler_registry()
    spec = registry.get(startup.handler_id)
    if spec is None:
        sys.stderr.write(f"unknown handler id: {startup.handler_id}\n")
        sys.stderr.flush()
        return 2
    handler = spec.factory()
    stdin = sys.stdin
    stdout = sys.stdout

    def send(message: dict[str, Any]) -> None:
        stdout.write(json.dumps(message) + "\n")
        stdout.flush()

    def request_backend_action(action_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = f"act-{uuid.uuid4().hex[:12]}"
        send(
            {
                "kind": "backend_action_request",
                "request_id": request_id,
                "action_name": action_name,
                "payload": dict(payload),
            }
        )
        while True:
            raw_line = stdin.readline()
            if not raw_line:
                raise RuntimeError("backend action response channel closed")
            message = json.loads(raw_line)
            if message.get("kind") != "backend_action_response":
                continue
            if str(message.get("request_id", "")).strip() != request_id:
                continue
            if not bool(message.get("ok", False)):
                raise RuntimeError(str(message.get("error", "backend action failed")))
            return dict(message.get("payload", {}))

    class WorkerFrontendChannel:
        def emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
            send({"kind": "channel_event", "event_type": event_type, "payload": dict(payload)})

        def request_action(self, action_type: str, payload: dict[str, Any]) -> None:
            send({"kind": "action_request", "action_type": action_type, "payload": dict(payload)})

    context = HandlerContext(
        notebook_id=startup.notebook_id,
        session_id=startup.session_id,
        cell_id=startup.cell_id,
        client_id=startup.client_id,
        channel=WorkerFrontendChannel(),
        push_frontend_message=lambda message_type, payload: send(
            {"kind": "live_handler_message", "message_type": message_type, "payload": dict(payload)}
        ),
        invoke_backend_action=request_backend_action,
        append_execution_event=lambda event: send({"kind": "execution_event", "event": dict(event)}),
        update_execution_status=lambda status: send({"kind": "status", "status": status}),
        set_client_transport=lambda transport: send(
            {
                "kind": "transport",
                "transport": {
                    "kind": transport.kind,
                    "attach_cmd": list(transport.attach_cmd),
                    "attach_env": dict(transport.attach_env),
                    "session_id": transport.session_id,
                    "client_id": transport.client_id,
                    "handler_id": transport.handler_id,
                },
            }
        ),
        magic_name=startup.magic_name,
        content=startup.content,
        meta=dict(startup.meta or {}),
    )
    send({"kind": "ready"})
    try:
        status = handler.execute(context, startup.cell)
    except Exception as exc:
        send(
            {
                "kind": "execution_event",
                "event": {
                    "type": "error",
                    "ename": exc.__class__.__name__,
                    "evalue": str(exc),
                    "traceback": [],
                },
            }
        )
        send({"kind": "status", "status": "error"})
        send({"kind": "execute_result", "status": "error"})
        return 1
    send({"kind": "execute_result", "status": status})
    for raw_line in stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        message = json.loads(raw_line)
        kind = str(message.get("kind", "")).strip()
        if kind == "frontend_message":
            handler.on_frontend_message(
                context,
                str(message.get("message_type", "")).strip(),
                dict(message.get("payload", {})),
            )
            continue
        if kind == "interrupt":
            interrupt = getattr(handler, "interrupt", None)
            if callable(interrupt):
                interrupt()
            continue
        if kind == "stop":
            handler.stop()
            break
    return 0
