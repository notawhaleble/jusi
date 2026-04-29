import threading
import unittest
from unittest.mock import patch

from jusi.infrastructure.runtime_supervisor import (
    monitor_supervisor_liveness,
    parse_supervisor_pid,
    supervisor_is_alive,
)


class RuntimeSupervisorTest(unittest.TestCase):
    def test_parse_supervisor_pid_normalizes_invalid_values(self) -> None:
        self.assertEqual(123, parse_supervisor_pid("123"))
        self.assertEqual(0, parse_supervisor_pid("x"))
        self.assertEqual(0, parse_supervisor_pid("-1"))

    def test_supervisor_is_alive_requires_same_parent_when_requested(self) -> None:
        with patch("os.getppid", return_value=1):
            self.assertFalse(supervisor_is_alive(42, require_same_parent=True))

    def test_monitor_supervisor_liveness_requests_shutdown_when_supervisor_is_lost(self) -> None:
        stop_event = threading.Event()
        with patch("jusi.infrastructure.runtime_supervisor.supervisor_is_alive", return_value=False):
            with patch("jusi.infrastructure.runtime_supervisor.request_process_shutdown") as shutdown:
                monitor_supervisor_liveness(42, stop_event, poll_interval_seconds=0.01)
        shutdown.assert_called_once_with()
