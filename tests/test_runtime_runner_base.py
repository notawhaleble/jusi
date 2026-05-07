import unittest
from unittest.mock import patch

from jusi.infrastructure.runtime_runner_base import RuntimeRunnerBase


class RuntimeRunnerBaseTest(unittest.TestCase):
    def test_stop_sets_running_false_and_stop_event(self) -> None:
        runner = RuntimeRunnerBase()
        runner._stop(15, None)
        self.assertFalse(runner._running)
        self.assertTrue(runner._stop_event.is_set())

    def test_supervisor_is_alive_delegates(self) -> None:
        runner = RuntimeRunnerBase()
        with patch("jusi.infrastructure.runtime_runner_base.supervisor_is_alive", return_value=True) as alive:
            self.assertTrue(runner._supervisor_is_alive(12, require_same_parent=True))
        alive.assert_called_once_with(12, require_same_parent=True)


if __name__ == "__main__":
    unittest.main()
