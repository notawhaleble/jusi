import unittest

from jusi.application.ports import StartSessionCommand
from jusi.application.use_cases import StartSession
from jusi.infrastructure.runtime import InMemoryKernelRuntime, InMemorySessionStore
from jusi.interfaces.events import CollectingEventSink
from jusi.interfaces.protocol import parse_envelope
from jusi.interfaces.server import ProtocolServer


class StartSessionTest(unittest.TestCase):
    def test_start_session_emits_starting_then_connected_updates(self) -> None:
        events = CollectingEventSink()
        use_case = StartSession(runtime=InMemoryKernelRuntime(), store=InMemorySessionStore(), events=events)

        session = use_case.execute(StartSessionCommand(notebook_id="nb-1", kernel_name="python3"))

        self.assertEqual("connected", session.state)
        self.assertEqual("session-1", session.session_id)
        self.assertEqual("inmemory://python3/1", session.connection)
        self.assertEqual(
            ["session_updated", "session_updated", "prepared_updated", "prepared_updated"],
            [event["type"] for event in events.events],
        )
        self.assertEqual("starting", events.events[0]["payload"]["state"])
        self.assertEqual("connected", events.events[1]["payload"]["state"])
        self.assertEqual("spawning", events.events[2]["payload"]["state"])
        self.assertEqual("ready", events.events[3]["payload"]["state"])

    def test_stdio_server_handles_start_session_request(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())

        messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "start_session", '
                '"request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}'
            )
        )

        envelopes = [parse_envelope(message) for message in messages]

        self.assertEqual("response", envelopes[0].kind)
        self.assertTrue(envelopes[0].ok)
        self.assertEqual("start_session", envelopes[0].type)
        self.assertEqual("session_updated", envelopes[1].type)
        self.assertEqual("starting", envelopes[1].payload["session"]["state"])
        self.assertEqual("session_updated", envelopes[2].type)
        self.assertEqual("connected", envelopes[2].payload["session"]["state"])
        self.assertEqual("prepared_updated", envelopes[3].type)
        self.assertEqual("spawning", envelopes[3].payload["prepared"]["state"])
        self.assertEqual("prepared_updated", envelopes[4].type)
        self.assertEqual("ready", envelopes[4].payload["prepared"]["state"])

    def test_stdio_server_handles_execute_cell_request(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())

        start_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "start_session", '
                '"request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}'
            )
        )
        start_envelopes = [parse_envelope(message) for message in start_messages]
        session_id = start_envelopes[2].payload["session"]["id"]

        execute_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "main_lines": ["print(1)"]}}}'
            )
        )
        execute_envelopes = [parse_envelope(message) for message in execute_messages]

        self.assertEqual("response", execute_envelopes[0].kind)
        self.assertTrue(execute_envelopes[0].ok)
        self.assertEqual("session_updated", execute_envelopes[1].type)
        self.assertEqual("execute", execute_envelopes[1].payload["session"]["last_action"])
        self.assertEqual("prepared_updated", execute_envelopes[2].type)
        self.assertEqual("spawning", execute_envelopes[2].payload["prepared"]["state"])
        self.assertEqual("cell_updated", execute_envelopes[3].type)
        self.assertEqual("busy", execute_envelopes[3].payload["cell"]["status"])
        self.assertEqual("kernel", execute_envelopes[3].payload["cell"]["owner"]["kind"])
        self.assertEqual("prepared_updated", execute_envelopes[4].type)
        self.assertEqual("ready", execute_envelopes[4].payload["prepared"]["state"])
        self.assertEqual("cell_updated", execute_envelopes[5].type)
        self.assertEqual("done", execute_envelopes[5].payload["cell"]["status"])
        self.assertEqual("kernel", execute_envelopes[5].payload["cell"]["owner"]["kind"])

    def test_follow_up_cell_does_not_block_later_execution(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())

        start_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "start_session", '
                '"request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}'
            )
        )
        start_envelopes = [parse_envelope(message) for message in start_messages]
        session_id = start_envelopes[2].payload["session"]["id"]

        followup_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "magic", "syntax": "sql", "main_lines": ["%%sql", "select 1"]}}}'
            )
        )
        followup_envelopes = [parse_envelope(message) for message in followup_messages]
        self.assertEqual("follow-up", followup_envelopes[5].payload["cell"]["status"])
        self.assertEqual("handler", followup_envelopes[3].payload["cell"]["owner"]["kind"])
        self.assertEqual("handler", followup_envelopes[5].payload["cell"]["owner"]["kind"])
        self.assertEqual("ready", followup_envelopes[4].payload["prepared"]["state"])

        next_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-3", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 13, "kind": "code", "syntax": "python", "main_lines": ["print(2)"]}}}'
            )
        )
        next_envelopes = [parse_envelope(message) for message in next_messages]
        self.assertTrue(next_envelopes[0].ok)
        self.assertEqual("busy", next_envelopes[3].payload["cell"]["status"])
        self.assertEqual("ready", next_envelopes[4].payload["prepared"]["state"])
        self.assertEqual("done", next_envelopes[5].payload["cell"]["status"])

    def test_interrupt_finished_execution_fails_explicitly(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        start_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "start_session", '
                '"request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}'
            )
        )
        session_id = [parse_envelope(message) for message in start_messages][2].payload["session"]["id"]

        server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "main_lines": ["print(1)"]}}}'
            )
        )
        interrupt_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "interrupt_cell", '
                '"request_id": "req-3", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell_id": 12}}'
            )
        )
        interrupt_envelopes = [parse_envelope(message) for message in interrupt_messages]
        self.assertFalse(interrupt_envelopes[0].ok)
        self.assertEqual("invalid_state", interrupt_envelopes[0].error["code"])

    def test_interrupt_handler_owned_execution(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        start_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "start_session", '
                '"request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}'
            )
        )
        session_id = [parse_envelope(message) for message in start_messages][2].payload["session"]["id"]

        server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "magic", "syntax": "sql", "main_lines": ["%%sql", "select 1"]}}}'
            )
        )
        interrupt_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "interrupt_cell", '
                '"request_id": "req-3", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell_id": 12}}'
            )
        )
        interrupt_envelopes = [parse_envelope(message) for message in interrupt_messages]
        self.assertTrue(interrupt_envelopes[0].ok)
        self.assertEqual("interrupted", interrupt_envelopes[2].payload["cell"]["status"])
        self.assertEqual("handler", interrupt_envelopes[2].payload["cell"]["owner"]["kind"])

    def test_interrupt_active_kernel_owned_execution(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        start_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "start_session", '
                '"request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}'
            )
        )
        session_id = [parse_envelope(message) for message in start_messages][2].payload["session"]["id"]

        execute_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "keep_running": true, "main_lines": ["while True: pass"]}}}'
            )
        )
        execute_envelopes = [parse_envelope(message) for message in execute_messages]
        self.assertEqual("busy", execute_envelopes[5].payload["cell"]["status"])
        self.assertEqual("kernel", execute_envelopes[5].payload["cell"]["owner"]["kind"])

        interrupt_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "interrupt_cell", '
                '"request_id": "req-3", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell_id": 12}}'
            )
        )
        interrupt_envelopes = [parse_envelope(message) for message in interrupt_messages]
        self.assertTrue(interrupt_envelopes[0].ok)
        self.assertEqual("interrupt", interrupt_envelopes[1].payload["session"]["last_action"])
        self.assertEqual("interrupted", interrupt_envelopes[2].payload["cell"]["status"])
        self.assertEqual("kernel", interrupt_envelopes[2].payload["cell"]["owner"]["kind"])

    def test_disconnect_marks_active_execution_owner_unknown_and_reconnect_restores_prepared(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        start_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "start_session", '
                '"request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}'
            )
        )
        session_id = [parse_envelope(message) for message in start_messages][2].payload["session"]["id"]

        server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "magic", "syntax": "sql", "main_lines": ["%%sql", "select 1"]}}}'
            )
        )
        disconnect_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "disconnect_session", '
                '"request_id": "req-3", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "reason": "transport_lost"}}'
            )
        )
        disconnect_envelopes = [parse_envelope(message) for message in disconnect_messages]
        self.assertTrue(disconnect_envelopes[0].ok)
        self.assertEqual("disconnected", disconnect_envelopes[1].payload["session"]["state"])
        self.assertEqual("missing", disconnect_envelopes[2].payload["prepared"]["state"])
        self.assertEqual("unknown", disconnect_envelopes[3].payload["cell"]["owner"]["kind"])
        self.assertEqual("follow-up", disconnect_envelopes[3].payload["cell"]["status"])

        reconnect_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "reconnect_session", '
                '"request_id": "req-4", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '"}}'
            )
        )
        reconnect_envelopes = [parse_envelope(message) for message in reconnect_messages]
        self.assertTrue(reconnect_envelopes[0].ok)
        self.assertEqual("starting", reconnect_envelopes[1].payload["session"]["state"])
        self.assertEqual("spawning", reconnect_envelopes[2].payload["prepared"]["state"])
        self.assertEqual("connected", reconnect_envelopes[3].payload["session"]["state"])
        self.assertEqual("ready", reconnect_envelopes[4].payload["prepared"]["state"])

    def test_interrupt_unknown_owner_fails_after_disconnect(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        start_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "start_session", '
                '"request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}'
            )
        )
        session_id = [parse_envelope(message) for message in start_messages][2].payload["session"]["id"]

        server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "magic", "syntax": "sql", "main_lines": ["%%sql", "select 1"]}}}'
            )
        )
        server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "disconnect_session", '
                '"request_id": "req-3", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "reason": "transport_lost"}}'
            )
        )
        interrupt_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "interrupt_cell", '
                '"request_id": "req-4", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell_id": 12}}'
            )
        )
        interrupt_envelopes = [parse_envelope(message) for message in interrupt_messages]
        self.assertFalse(interrupt_envelopes[0].ok)
        self.assertEqual("invalid_state", interrupt_envelopes[0].error["code"])

    def test_stop_session_clears_prepared_and_interrupts_active_cells(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        start_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "start_session", '
                '"request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}'
            )
        )
        session_id = [parse_envelope(message) for message in start_messages][2].payload["session"]["id"]

        server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "keep_running": true, "main_lines": ["while True: pass"]}}}'
            )
        )
        stop_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "stop_session", '
                '"request_id": "req-3", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '"}}'
            )
        )
        stop_envelopes = [parse_envelope(message) for message in stop_messages]
        self.assertTrue(stop_envelopes[0].ok)
        self.assertEqual("stopping", stop_envelopes[1].payload["session"]["state"])
        self.assertEqual("missing", stop_envelopes[2].payload["prepared"]["state"])
        self.assertEqual("interrupted", stop_envelopes[3].payload["cell"]["status"])
        self.assertEqual("stopped", stop_envelopes[4].payload["session"]["state"])


if __name__ == "__main__":
    unittest.main()
