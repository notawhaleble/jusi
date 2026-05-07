import unittest
from unittest.mock import Mock, patch

from jusi.infrastructure.client_runtime_constants import JUSI_CLIENT_RUNTIME_MODE_ENV
from jusi.infrastructure.client_runtime_entrypoint import (
    run_client_runtime,
)


class ClientRuntimeEntrypointTest(unittest.TestCase):
    def test_transcript_mode_runs_client_process(self) -> None:
        host = Mock()
        host.run.return_value = 11
        with patch.dict("os.environ", {JUSI_CLIENT_RUNTIME_MODE_ENV: "transcript"}, clear=False):
            rc = run_client_runtime(host_factories={"transcript": lambda: host})
        self.assertEqual(11, rc)
        host.run.assert_called_once_with()

    def test_default_mode_is_used_when_env_is_missing(self) -> None:
        host = Mock()
        host.run.return_value = 19
        with patch.dict("os.environ", {}, clear=True):
            rc = run_client_runtime("transcript", host_factories={"transcript": lambda: host})
        self.assertEqual(19, rc)
        host.run.assert_called_once_with()

    def test_invalid_mode_returns_error(self) -> None:
        with patch.dict("os.environ", {JUSI_CLIENT_RUNTIME_MODE_ENV: "nope"}, clear=False):
            rc = run_client_runtime(host_factories={})
        self.assertEqual(2, rc)


if __name__ == "__main__":
    unittest.main()
