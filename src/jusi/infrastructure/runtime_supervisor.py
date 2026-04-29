from __future__ import annotations

import os
import signal
import threading


def parse_supervisor_pid(raw: object) -> int:
    try:
        value = int(str(raw).strip())
    except ValueError:
        return 0
    return value if value > 0 else 0


def supervisor_is_alive(supervisor_pid: int, *, require_same_parent: bool = False) -> bool:
    if supervisor_pid <= 0:
        return True
    if require_same_parent and os.getppid() != supervisor_pid:
        return False
    try:
        os.kill(supervisor_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def request_process_shutdown() -> None:
    os.kill(os.getpid(), signal.SIGTERM)


def monitor_supervisor_liveness(
    supervisor_pid: int,
    stop_event: threading.Event,
    *,
    poll_interval_seconds: float,
    require_same_parent: bool = False,
) -> None:
    while not stop_event.is_set():
        if not supervisor_is_alive(supervisor_pid, require_same_parent=require_same_parent):
            request_process_shutdown()
            return
        stop_event.wait(poll_interval_seconds)
