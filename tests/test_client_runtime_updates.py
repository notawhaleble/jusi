import unittest

from jusi.domain.models import ClientTransport
from jusi.infrastructure.client_runtime_updates import ClientRuntimeUpdate


class ClientRuntimeUpdatesTest(unittest.TestCase):
    def test_supported_update_kinds(self) -> None:
        updates = (
            ClientRuntimeUpdate.execution_event({"type": "stream", "text": "x"}),
            ClientRuntimeUpdate.execution_status("busy"),
            ClientRuntimeUpdate.transport(
                ClientTransport(
                    kind="native_terminal",
                    attach_cmd=["/bin/sh"],
                    attach_env={"TERM": "xterm-256color"},
                    session_id="sess-1",
                    client_id="client-1",
                    handler_id="vd",
                )
            ),
            ClientRuntimeUpdate.channel_event("sheet_updated", {"rows": 3}),
            ClientRuntimeUpdate.action_request("open_path", {"path": "/tmp/x"}),
            ClientRuntimeUpdate.live_handler_message("complete_result", {"items": []}),
        )

        self.assertEqual(
            [
                "execution_event",
                "execution_status",
                "transport",
                "channel_event",
                "action_request",
                "live_handler_message",
            ],
            [update.kind for update in updates],
        )

    def test_invalid_kind_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ClientRuntimeUpdate("nope")


if __name__ == "__main__":
    unittest.main()
