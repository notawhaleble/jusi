from __future__ import annotations

import signal
import threading

from jusi.infrastructure.runtime_supervisor import monitor_supervisor_liveness, supervisor_is_alive


class RuntimeRunnerBase:
    def __init__(self) -> None:
        self._running = True
        self._stop_event = threading.Event()
        self._supervisor_monitor: threading.Thread | None = None

    def _stop(self, _signum: int, _frame: object) -> None:
        self._running = False
        self._stop_event.set()

    def _install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)

    def _supervisor_is_alive(self, supervisor_pid: int, *, require_same_parent: bool = False) -> bool:
        return supervisor_is_alive(supervisor_pid, require_same_parent=require_same_parent)

    def _start_supervisor_monitor(
        self,
        supervisor_pid: int,
        *,
        poll_interval_seconds: float,
        require_same_parent: bool = False,
    ) -> None:
        if supervisor_pid <= 0:
            return
        self._supervisor_monitor = threading.Thread(
            target=monitor_supervisor_liveness,
            args=(supervisor_pid, self._stop_event),
            kwargs={
                "poll_interval_seconds": poll_interval_seconds,
                "require_same_parent": require_same_parent,
            },
            daemon=True,
        )
        self._supervisor_monitor.start()

    def _stop_supervisor_monitor(self, timeout: float = 0.5) -> None:
        self._stop_event.set()
        if self._supervisor_monitor is not None and self._supervisor_monitor.is_alive():
            self._supervisor_monitor.join(timeout=timeout)
