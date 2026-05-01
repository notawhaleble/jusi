from __future__ import annotations

import importlib
import json
import os
import socket
import sys
import threading
from typing import Callable

from jusi.infrastructure.debug_timing import emit_timing
from jusi.infrastructure.runtime_supervisor import (
    monitor_supervisor_liveness,
    parse_supervisor_pid,
)
from jusi.visidata_support import (
    handle_plugin_runtime_control_request,
    install_visidata_runtime_hooks,
    load_visidatarc_from_env,
)


PLUGIN_RUNTIME_SUPERVISOR_POLL_INTERVAL_SECONDS = 0.25
_PLUGIN_CONTROL_HANDLER: Callable[[dict], dict] | None = None


def _write_runtime_pid_file() -> str:
    path = str(os.environ.get("JUSI_PLUGIN_RUNTIME_PID_FILE", "")).strip()
    if not path:
        return ""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    return path


def _remove_runtime_pid_file(path: str) -> None:
    if not path:
        return
    try:
        os.unlink(path)
    except FileNotFoundError:
        return


def set_plugin_control_handler(handler: Callable[[dict], dict]) -> None:
    global _PLUGIN_CONTROL_HANDLER
    _PLUGIN_CONTROL_HANDLER = handler


def _plugin_control_socket_path() -> str:
    return str(os.environ.get("JUSI_PLUGIN_RUNTIME_CONTROL_SOCKET", "")).strip()


def _prepare_common_plugin_runtime() -> None:
    try:
        load_visidatarc_from_env()
        install_visidata_runtime_hooks()
        set_plugin_control_handler(handle_plugin_runtime_control_request)
    except ModuleNotFoundError:
        return


def _run_plugin_control_server(socket_path: str, stop_event: threading.Event) -> None:
    if not socket_path:
        return
    try:
        os.unlink(socket_path)
    except FileNotFoundError:
        pass
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(socket_path)
        server.listen(5)
        server.settimeout(0.25)
        while not stop_event.is_set():
            try:
                conn, _addr = server.accept()
            except socket.timeout:
                continue
            with conn:
                raw = b""
                while not raw.endswith(b"\n"):
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    raw += chunk
                response: dict[str, object]
                try:
                    request = json.loads(raw.decode("utf-8").strip() or "{}")
                    if not isinstance(request, dict):
                        raise RuntimeError("invalid plugin runtime control request")
                    emit_timing(
                        "plugin_runtime.control.request",
                        message_type=str(request.get("message_type", "")).strip(),
                        socket_path=socket_path,
                    )
                    handler = _PLUGIN_CONTROL_HANDLER
                    if handler is None:
                        raise RuntimeError("plugin runtime control handler is not ready")
                    payload = handler(dict(request))
                    emit_timing(
                        "plugin_runtime.control.done",
                        message_type=str(request.get("message_type", "")).strip(),
                        response_keys=sorted(list(payload.keys())) if isinstance(payload, dict) else [],
                        socket_path=socket_path,
                    )
                    response = {"ok": True, "payload": dict(payload)}
                except Exception as exc:
                    emit_timing(
                        "plugin_runtime.control.error",
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                        socket_path=socket_path,
                    )
                    response = {"ok": False, "error": str(exc), "payload": {}}
                conn.sendall(json.dumps(response).encode("utf-8") + b"\n")
    try:
        os.unlink(socket_path)
    except FileNotFoundError:
        pass


def run_plugin_runtime() -> int:
    raw = os.environ.get("JUSI_PLUGIN_RUNTIME_CALLABLE", "").strip()
    if not raw or ":" not in raw:
        sys.stderr.write("missing JUSI_PLUGIN_RUNTIME_CALLABLE\n")
        sys.stderr.flush()
        return 2
    module_name, func_name = raw.split(":", 1)
    module_name = module_name.strip()
    func_name = func_name.strip()
    if not module_name or not func_name:
        sys.stderr.write("invalid JUSI_PLUGIN_RUNTIME_CALLABLE\n")
        sys.stderr.flush()
        return 2
    module = importlib.import_module(module_name)
    target = getattr(module, func_name, None)
    if not callable(target):
        sys.stderr.write("invalid JUSI_PLUGIN_RUNTIME_CALLABLE\n")
        sys.stderr.flush()
        return 2
    supervisor_pid = parse_supervisor_pid(os.environ.get("JUSI_SUPERVISOR_PID", ""))
    stop_event = threading.Event()
    monitor: threading.Thread | None = None
    control_server: threading.Thread | None = None
    if supervisor_pid > 0:
        monitor = threading.Thread(
            target=monitor_supervisor_liveness,
            args=(supervisor_pid, stop_event),
            kwargs={"poll_interval_seconds": PLUGIN_RUNTIME_SUPERVISOR_POLL_INTERVAL_SECONDS},
            daemon=True,
        )
        monitor.start()
    socket_path = _plugin_control_socket_path()
    if socket_path:
        control_server = threading.Thread(
            target=_run_plugin_control_server,
            args=(socket_path, stop_event),
            daemon=True,
        )
        control_server.start()
    pid_file = _write_runtime_pid_file()
    try:
        _prepare_common_plugin_runtime()
        return int(target())
    finally:
        stop_event.set()
        if control_server is not None and control_server.is_alive():
            control_server.join(timeout=0.5)
        if monitor is not None and monitor.is_alive():
            monitor.join(timeout=0.5)
