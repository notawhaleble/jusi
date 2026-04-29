import unittest

from jusi.infrastructure.client_runtime_handshake import (
    ClientRuntimeReadyMessage,
    parse_ready_line,
)


class ClientRuntimeHandshakeTest(unittest.TestCase):
    def test_parse_legacy_ready_line(self) -> None:
        ready = parse_ready_line("ready")
        self.assertEqual(ClientRuntimeReadyMessage(mode="unknown"), ready)

    def test_parse_structured_ready_line(self) -> None:
        wire = ClientRuntimeReadyMessage(mode="transcript").to_wire()
        ready = parse_ready_line(wire)
        self.assertEqual(ClientRuntimeReadyMessage(mode="transcript"), ready)

    def test_invalid_ready_line_returns_none(self) -> None:
        self.assertIsNone(parse_ready_line("nope"))


if __name__ == "__main__":
    unittest.main()
