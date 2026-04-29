import json
import os
import signal
import tempfile
import unittest
from unittest.mock import patch

from jusi.__main__ import main
from jusi.infrastructure.client_process import ClientProcessRunner
from jusi.infrastructure.client_runtime_handshake import ClientRuntimeReadyMessage
from jusi.infrastructure.client_runtime_protocol import ClientRuntimeCommand, ClientRuntimeSnapshot


class ClientProcessTest(unittest.TestCase):
    def test_main_routes_client_runtime_mode(self) -> None:
        with patch("jusi.__main__.run_client_runtime", return_value=0) as run_client_runtime:
            rc = main(["client-runtime"])
        self.assertEqual(0, rc)
        run_client_runtime.assert_called_once_with()

    def test_main_routes_client_process_mode(self) -> None:
        with patch("jusi.__main__.run_client_runtime", return_value=0) as run_client_runtime:
            rc = main(["client-process"])
        self.assertEqual(0, rc)
        run_client_runtime.assert_called_once_with("transcript")

    def test_client_process_runner_writes_ready_then_stops(self) -> None:
        writes: list[str] = []

        class FakeStdout:
            def write(self, value: str) -> int:
                writes.append(value)
                return len(value)

            def flush(self) -> None:
                return None

        runner = ClientProcessRunner()
        original_signal = signal.signal

        def fake_signal(sig: int, handler):  # type: ignore[no-untyped-def]
            original_signal(sig, handler)
            return handler

        def fake_sleep(_seconds: float) -> None:
            runner._stop(signal.SIGTERM, None)

        with patch("sys.stdout", new=FakeStdout()):
            with patch("signal.signal", side_effect=fake_signal):
                with patch("time.sleep", side_effect=fake_sleep):
                    rc = runner.run()

        self.assertEqual(0, rc)
        self.assertEqual([ClientRuntimeReadyMessage(mode="transcript").to_wire() + "\n"], writes)

    def test_client_process_runner_applies_control_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            commands_path = os.path.join(tmpdir, "commands.jsonl")
            status_path = os.path.join(tmpdir, "status.json")
            writes: list[str] = []

            class FakeStdout:
                def write(self, value: str) -> int:
                    writes.append(value)
                    return len(value)

                def flush(self) -> None:
                    return None

            runner = ClientProcessRunner()
            runner._control_dir = tmpdir
            runner._commands_path = commands_path
            runner._status_path = status_path
            runner._state.client_id = "client-1"
            runner._state.notebook_id = "nb-1"
            runner._state.session_id = "sess-1"

            original_signal = signal.signal
            ticks = {"count": 0}

            def fake_signal(sig: int, handler):  # type: ignore[no-untyped-def]
                original_signal(sig, handler)
                return handler

            def fake_sleep(_seconds: float) -> None:
                ticks["count"] += 1
                if ticks["count"] == 1:
                    with open(commands_path, "a", encoding="utf-8") as handle:
                        handle.write(json.dumps(ClientRuntimeCommand.bind(91).to_dict()) + "\n")
                        handle.write(json.dumps(ClientRuntimeCommand.activate(12).to_dict()) + "\n")
                        handle.write(
                            json.dumps(
                                ClientRuntimeCommand.execution_event(
                                    {"type": "execution_started", "cell_id": 12, "kind": "code", "syntax": "python"}
                                ).to_dict()
                            )
                            + "\n"
                        )
                        handle.write(
                            json.dumps(ClientRuntimeCommand.execution_event({"type": "execution_state", "status": "busy"}).to_dict())
                            + "\n"
                        )
                        handle.write(
                            json.dumps(
                                ClientRuntimeCommand.execution_event(
                                    {"type": "stream", "name": "stdout", "text": "hello\nworld\n"}
                                ).to_dict()
                            )
                            + "\n"
                        )
                        handle.write(json.dumps(ClientRuntimeCommand.execution_status("busy").to_dict()) + "\n")
                        handle.write(
                            json.dumps(
                                ClientRuntimeCommand.execution_event(
                                    {
                                        "type": "error",
                                        "ename": "ValueError",
                                        "evalue": "boom",
                                        "traceback": ["frame one", "frame two"],
                                    }
                                ).to_dict()
                            )
                            + "\n"
                        )
                        handle.write(
                            json.dumps(
                                ClientRuntimeCommand.execution_event(
                                    {"type": "display_data", "data": {"text/plain": "alpha\nbeta"}}
                                ).to_dict()
                            )
                            + "\n"
                        )
                        handle.write(
                            json.dumps(
                                ClientRuntimeCommand.execution_event(
                                    {"type": "execute_result", "data": {"text/plain": "42"}}
                                ).to_dict()
                            )
                            + "\n"
                        )
                        handle.write(json.dumps(ClientRuntimeCommand.execution_status("interrupted").to_dict()) + "\n")
                        handle.write(json.dumps(ClientRuntimeCommand.shutdown("healthcheck").to_dict()) + "\n")

            with patch.dict(
                os.environ,
                {
                    "JUSI_CLIENT_ID": "client-1",
                    "JUSI_NOTEBOOK_ID": "nb-1",
                    "JUSI_SESSION_ID": "sess-1",
                    "JUSI_CLIENT_CONTROL_DIR": tmpdir,
                },
                clear=False,
            ):
                with patch("sys.stdout", new=FakeStdout()):
                    with patch("signal.signal", side_effect=fake_signal):
                        with patch("time.sleep", side_effect=fake_sleep):
                            rc = runner.run()

            self.assertEqual(0, rc)
            self.assertEqual([ClientRuntimeReadyMessage(mode="transcript").to_wire() + "\n"], writes)
            with open(status_path, "r", encoding="utf-8") as handle:
                status = json.load(handle)
            self.assertEqual(-1, status["client_bufnr"])
            self.assertEqual(12, status["active_cell_id"])
            self.assertEqual("interrupted", status["execution_status"])
            self.assertEqual(
                [
                    {"type": "execution_started", "cell_id": 12, "kind": "code", "syntax": "python"},
                    {"type": "execution_state", "status": "busy"},
                    {"type": "stream", "name": "stdout", "text": "hello\nworld\n"},
                    {"type": "error", "ename": "ValueError", "evalue": "boom", "traceback": ["frame one", "frame two"]},
                    {"type": "display_data", "data": {"text/plain": "alpha\nbeta"}},
                    {"type": "execute_result", "data": {"text/plain": "42"}},
                ],
                status["transcript"],
            )
            self.assertEqual("cell 12: interrupted", status["view_title"])
            self.assertEqual(
                [
                    "meta> client=client-1 session=sess-1 bufnr=unbound",
                    "started cell 12 [code:python]",
                    "state: busy",
                    "stdout> hello",
                    "stdout> world",
                    "error: ValueError: boom",
                    "trace> frame one",
                    "trace> frame two",
                    "display> alpha",
                    "display> beta",
                    "result> 42",
                    "interrupted",
                ],
                status["view_lines"],
            )
            self.assertEqual("healthcheck", status["shutdown_reason"])
            self.assertEqual(
                [
                    "ready",
                    "bind:91",
                    "activate:12",
                    "event:execution_started",
                    "event:execution_state",
                    "event:stream",
                    "status:busy",
                    "event:error",
                    "event:display_data",
                    "event:execute_result",
                    "status:interrupted",
                    "shutdown:healthcheck",
                ],
                status["lifecycle"],
            )

    def test_client_runtime_protocol_roundtrips(self) -> None:
        command = ClientRuntimeCommand.execution_event({"type": "stream", "name": "stdout", "text": "x\n"})
        self.assertEqual(command, ClientRuntimeCommand.from_dict(command.to_dict()))

        snapshot = ClientRuntimeSnapshot(
            client_id="client-1",
            notebook_id="nb-1",
            session_id="sess-1",
            client_bufnr=7,
            active_cell_id=12,
            execution_status="busy",
            view_revision=3,
            lifecycle=["ready", "activate:12"],
            transcript=[{"type": "stream", "name": "stdout", "text": "x\n"}],
            view_title="cell 12: busy",
            view_lines=["stdout> x"],
            shutdown_reason="",
            transport={"kind": "native_terminal"},
        )
        self.assertEqual(snapshot, ClientRuntimeSnapshot.from_dict(snapshot.to_dict()))

    def test_client_process_runner_exits_when_supervisor_is_lost(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = os.path.join(tmpdir, "status.json")
            writes: list[str] = []

            class FakeStdout:
                def write(self, value: str) -> int:
                    writes.append(value)
                    return len(value)

                def flush(self) -> None:
                    return None

            runner = ClientProcessRunner()
            runner._control_dir = tmpdir
            runner._commands_path = os.path.join(tmpdir, "commands.jsonl")
            runner._status_path = status_path
            runner._state.client_id = "client-1"
            runner._state.notebook_id = "nb-1"
            runner._state.session_id = "sess-1"
            runner._supervisor_pid = 4242

            original_signal = signal.signal

            def fake_signal(sig: int, handler):  # type: ignore[no-untyped-def]
                original_signal(sig, handler)
                return handler

            with patch("sys.stdout", new=FakeStdout()):
                with patch("signal.signal", side_effect=fake_signal):
                    with patch("os.getppid", return_value=1):
                        rc = runner.run()

            self.assertEqual(0, rc)
            self.assertEqual([ClientRuntimeReadyMessage(mode="transcript").to_wire() + "\n"], writes)
            with open(status_path, "r", encoding="utf-8") as handle:
                status = json.load(handle)
            self.assertEqual("supervisor_lost", status["shutdown_reason"])
            self.assertEqual(
                [
                    "ready",
                    "shutdown:supervisor_lost",
                ],
                status["lifecycle"],
            )

    def test_terminal_attach_renders_transcript_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = os.path.join(tmpdir, "status.json")
            with open(status_path, "w", encoding="utf-8") as handle:
                json.dump(
                    ClientRuntimeSnapshot(
                        client_id="client-1",
                        notebook_id="nb-1",
                        session_id="sess-1",
                        active_cell_id=12,
                        execution_status="done",
                        view_revision=3,
                        transcript=[{"type": "stream", "name": "stdout", "text": "hello\n"}],
                        view_lines=["stdout> hello", "finished: done"],
                        shutdown_reason="done",
                    ).to_dict(),
                    handle,
                )

            writes: list[str] = []

            class FakeStdout:
                def write(self, value: str) -> int:
                    writes.append(value)
                    return len(value)

                def flush(self) -> None:
                    return None

            with patch.dict(
                os.environ,
                {
                    "JUSI_TERMINAL_MODE": "transcript",
                    "JUSI_CLIENT_STATUS_FILE": status_path,
                },
                clear=False,
            ):
                with patch("sys.stdout", new=FakeStdout()):
                    rc = main(["client-process", "terminal-attach"])

        self.assertEqual(0, rc)
        self.assertEqual(["\033[2J\033[H", "hello\n"], writes)


if __name__ == "__main__":
    unittest.main()
