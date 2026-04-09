from __future__ import annotations

import importlib
import os
import signal
import sys
import threading
from typing import Callable


PLUGIN_RUNTIME_SUPERVISOR_POLL_INTERVAL_SECONDS = 0.25


def _parse_supervisor_pid(raw: str) -> int:
    try:
        value = int(str(raw).strip())
    except ValueError:
        return 0
    return value if value > 0 else 0


def _supervisor_is_alive(supervisor_pid: int) -> bool:
    if supervisor_pid <= 0:
        return True
    try:
        os.kill(supervisor_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _request_process_shutdown() -> None:
    os.kill(os.getpid(), signal.SIGTERM)


def _monitor_supervisor_liveness(supervisor_pid: int, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        if not _supervisor_is_alive(supervisor_pid):
            _request_process_shutdown()
            return
        stop_event.wait(PLUGIN_RUNTIME_SUPERVISOR_POLL_INTERVAL_SECONDS)


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
    supervisor_pid = _parse_supervisor_pid(os.environ.get("JUSI_SUPERVISOR_PID", ""))
    stop_event = threading.Event()
    monitor: threading.Thread | None = None
    if supervisor_pid > 0:
        monitor = threading.Thread(
            target=_monitor_supervisor_liveness,
            args=(supervisor_pid, stop_event),
            daemon=True,
        )
        monitor.start()
    pid_file = _write_runtime_pid_file()
    try:
        return int(target())
    finally:
        stop_event.set()
        if monitor is not None and monitor.is_alive():
            monitor.join(timeout=0.5)
        _remove_runtime_pid_file(pid_file)
