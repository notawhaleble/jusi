import os
import tempfile
import unittest
import time
from unittest.mock import patch

from jusi.plugins import (
    DISPLAY_HANDLER_ENTRY_POINT_GROUP,
    DisplayHandlerRegistry,
    DisplayHandlerSpec,
    MagicCommand,
    VDDisplayHandlerBase,
    VDDisplayHandler,
    _build_vd_env,
    _build_vd_command,
    builtin_display_handler_specs,
    build_display_handler_registry,
    load_display_handler_specs,
)
from jusi.infrastructure.runtime import InMemoryKernelRuntime
from jusi.interfaces.protocol import parse_envelope
from jusi.interfaces.server import ProtocolServer


TEST_VD_CMD = (
    "/bin/sh -lc \"stty -echo; printf 'vd live ready\\r\\nvd> '; "
    "while IFS= read -r line; do printf 'echo %s\\r\\nvd> ' \\\"$line\\\"; done\""
)
TEST_VD_SIGNAL_CMD = (
    "/bin/sh -lc \"trap \\\"printf 'INT\\\\r\\\\nvd> '\\\" INT; stty -echo; printf 'vd live ready\\r\\nvd> '; "
    "while IFS= read -r line; do printf 'echo %s\\r\\nvd> ' \\\"$line\\\"; done\""
)


def inspect_client_lines(server: ProtocolServer, session_id: str, client_id: str) -> list[str]:
    inspect_messages = server.handle_message(
        (
            '{"version": 1, "kind": "request", "type": "inspect_client", '
            '"request_id": "req-inspect", "payload": {"notebook_id": "nb-1", "session_id": "'
            + session_id
            + '", "client_id": "'
            + client_id
            + '"}}'
        )
    )
    inspect_envelope = [parse_envelope(message) for message in inspect_messages][0]
    return inspect_envelope.payload["client"]["lines"]


def wait_for_client_lines(server: ProtocolServer, session_id: str, client_id: str, expected: list[str]) -> list[str]:
    deadline = time.time() + 2.0
    last_lines: list[str] = []
    while time.time() < deadline:
        last_lines = inspect_client_lines(server, session_id, client_id)
        if all(item in last_lines for item in expected):
            return last_lines
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for client lines {expected!r}; got {last_lines!r}")


def wait_for_handler_messages(
    server: ProtocolServer,
    *,
    message_types: list[str],
) -> list[dict]:
    deadline = time.time() + 2.0
    collected: list[dict] = []
    needed = set(message_types)
    while time.time() < deadline:
        drained = [parse_envelope(message) for message in server.drain_pending_messages()]
        for envelope in drained:
            if envelope.type != "handler_message":
                continue
            collected.append(envelope.payload)
        seen = {payload["message_type"] for payload in collected}
        if needed.issubset(seen):
            return collected
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for handler messages {message_types!r}; got {collected!r}")


def decode_terminal_bytes(payloads: list[dict]) -> str:
    chunks: list[bytes] = []
    for payload in payloads:
        if payload["message_type"] != "terminal_bytes":
            continue
        raw_hex = str(payload["payload"].get("hex", ""))
        if raw_hex:
            chunks.append(bytes.fromhex(raw_hex))
    return b"".join(chunks).decode("utf-8", errors="replace")


def start_bound_vd_server(*, command: str = TEST_VD_CMD) -> tuple[ProtocolServer, str, str]:
    server = ProtocolServer(runtime=InMemoryKernelRuntime())

    start_messages = server.handle_message(
        (
            '{"version": 1, "kind": "request", "type": "start_session", '
            '"request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}'
        )
    )
    start_envelopes = [parse_envelope(message) for message in start_messages]
    session_id = start_envelopes[2].payload["session"]["id"]
    client_id = start_envelopes[4].payload["prepared"]["id"]
    server.handle_message(
        (
            '{"version": 1, "kind": "request", "type": "bind_prepared_client", '
            '"request_id": "req-bind", "payload": {"notebook_id": "nb-1", "session_id": "'
            + session_id
            + '", "client_id": "'
            + client_id
            + '", "client_bufnr": 91}}'
        )
    )
    execute_messages = server.handle_message(
        (
            '{"version": 1, "kind": "request", "type": "execute_cell", '
            '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
            + session_id
            + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "main_lines": ["%%vd pods"]}}}'
        )
    )
    execute_envelopes = [parse_envelope(message) for message in execute_messages]
    active_client_id = execute_envelopes[3].payload["cell"]["client_id"]
    message_response = server.handle_message(
        (
            '{"version": 1, "kind": "request", "type": "handler_message", '
            '"request_id": "req-handler", "payload": {"notebook_id": "nb-1", "session_id": "'
            + session_id
            + '", "client_id": "'
            + active_client_id
            + '", "handler_id": "vd", "message_type": "bootstrap_done", "payload": {"bufnr": 91}}}'
        )
    )
    assert parse_envelope(message_response[0]).ok
    wait_for_handler_messages(server, message_types=["handler_snapshot", "terminal_bytes"])
    return server, session_id, active_client_id


class SelectableEntryPoints:
    def __init__(self, mapping):
        self._mapping = mapping

    def select(self, *, group):
        return list(self._mapping.get(group, []))


class FakeEntryPoint:
    def __init__(self, loaded):
        self._loaded = loaded

    def load(self):
        return self._loaded


def build_sql_spec():
    return DisplayHandlerSpec(
        handler_id="sql",
        factory=object(),
        magic_commands=(MagicCommand("sql"),),
    )


class FakeVDHandler(VDDisplayHandlerBase):
    def handler_id(self) -> str:
        return "fake_vd"

    def complete(self, context, payload):  # type: ignore[no-untyped-def]
        _ = context
        prefix = str(payload.get("prefix", ""))
        return [prefix + "_one", prefix + "_two"]

    def followup(self, context, payload):  # type: ignore[no-untyped-def]
        _ = context
        return {"cell_text": str(payload.get("cell_text", "")).upper()}


class FakePopen:
    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.args = args
        self.kwargs = kwargs
        self.pid = 43210

    def poll(self):  # type: ignore[no-untyped-def]
        return 0

    def wait(self, timeout=None):  # type: ignore[no-untyped-def]
        _ = timeout
        return 0


class PluginRegistryTest(unittest.TestCase):
    def test_builtin_display_handler_specs_include_vd(self) -> None:
        specs = builtin_display_handler_specs()
        self.assertEqual("vd", specs[0].handler_id)
        self.assertEqual("vd", specs[0].magic_commands[0].name)
        self.assertIsInstance(specs[0].factory(), VDDisplayHandler)

    def test_vd_env_defaults_term_to_xterm_256color(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            env = _build_vd_env()
        self.assertEqual("xterm-256color", env["TERM"])

    def test_build_vd_command_finds_binary_next_to_sys_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            python_path = os.path.join(tempdir, "python")
            vd_path = os.path.join(tempdir, "vd")
            with open(python_path, "w", encoding="utf-8") as handle:
                handle.write("")
            with open(vd_path, "w", encoding="utf-8") as handle:
                handle.write("#!/bin/sh\n")
            os.chmod(vd_path, 0o755)
            with patch("jusi.plugins.shutil.which", return_value=""), patch("jusi.plugins.sys.executable", python_path):
                command, notice = _build_vd_command()
        self.assertEqual(vd_path, command[0])
        self.assertEqual("", notice)

    def test_vd_base_copy_complete_and_followup_hooks_emit_results(self) -> None:
        handler = FakeVDHandler()
        pushed: list[tuple[str, dict]] = []
        context = type(
            "Ctx",
            (),
            {
                "push_frontend_message": lambda _self, message_type, payload: pushed.append((message_type, payload)),
            },
        )()

        handler.handle_copy(context, {"text": "abc"})  # type: ignore[arg-type]
        handler.on_frontend_message(context, "vd_complete", {"prefix": "pod"})  # type: ignore[arg-type]
        handler.on_frontend_message(context, "vd_followup", {"cell_text": "show pods"})  # type: ignore[arg-type]

        self.assertEqual(("vd_copy_result", {"handler_id": "fake_vd", "text": "abc"}), pushed[0])
        self.assertEqual(
            ("vd_complete_result", {"handler_id": "fake_vd", "items": ["pod_one", "pod_two"]}),
            pushed[1],
        )
        self.assertEqual(
            ("vd_followup_result", {"handler_id": "fake_vd", "payload": {"cell_text": "SHOW PODS"}}),
            pushed[2],
        )

    def test_terminal_resize_before_bootstrap_is_applied_at_spawn(self) -> None:
        handler = FakeVDHandler()
        context = type(
            "Ctx",
            (),
            {
                "channel": type(
                    "Chan",
                    (),
                    {
                        "emit_event": lambda _self, *_args, **_kwargs: None,
                        "request_action": lambda _self, *_args, **_kwargs: None,
                    },
                )(),
                "push_frontend_message": lambda _self, *_args, **_kwargs: None,
            },
        )()
        ioctl_calls: list[tuple[int, bytes]] = []

        def record_ioctl(fd, op, payload):  # type: ignore[no-untyped-def]
            _ = op
            ioctl_calls.append((fd, payload))
            return 0

        with patch("jusi.plugins.subprocess.Popen", FakePopen), patch("jusi.plugins.fcntl.ioctl", side_effect=record_ioctl), patch(
            "jusi.plugins.threading.Thread.start", lambda _self: None
        ):
            handler.on_frontend_message(context, "terminal_resize", {"rows": 21, "cols": 158})  # type: ignore[arg-type]
            handler.on_frontend_message(context, "bootstrap_done", {"rows": 21, "cols": 158})  # type: ignore[arg-type]
            handler.stop()

        self.assertTrue(ioctl_calls)
        winsize = ioctl_calls[0][1]
        self.assertEqual((21, 158), tuple(int.from_bytes(winsize[index:index + 2], "little") for index in (0, 2)))

    def test_terminal_resize_before_bootstrap_sets_spawn_env_geometry(self) -> None:
        handler = FakeVDHandler()
        context = type(
            "Ctx",
            (),
            {
                "channel": type(
                    "Chan",
                    (),
                    {
                        "emit_event": lambda _self, *_args, **_kwargs: None,
                        "request_action": lambda _self, *_args, **_kwargs: None,
                    },
                )(),
                "push_frontend_message": lambda _self, *_args, **_kwargs: None,
            },
        )()
        captured_env: dict[str, str] = {}

        class FakeEnvPopen:
            def __init__(self, *_args, **kwargs) -> None:  # type: ignore[no-untyped-def]
                captured_env.update(kwargs.get("env", {}))
                self.pid = 43210

            def poll(self):
                return None

            def wait(self, timeout=None):
                _ = timeout
                return 0

        with patch.dict("os.environ", {"LINES": "45", "COLUMNS": "158"}), patch(
            "jusi.plugins.subprocess.Popen", FakeEnvPopen
        ), patch("jusi.plugins.threading.Thread.start", lambda _self: None):
            handler.on_frontend_message(context, "terminal_resize", {"rows": 21, "cols": 158})  # type: ignore[arg-type]
            handler.on_frontend_message(context, "bootstrap_done", {"rows": 21, "cols": 158})  # type: ignore[arg-type]
            handler.stop()

        self.assertEqual("21", captured_env["LINES"])
        self.assertEqual("158", captured_env["COLUMNS"])

    def test_registry_matches_magic_cell_to_handler(self) -> None:
        registry = DisplayHandlerRegistry(
            [
                DisplayHandlerSpec(
                    handler_id="vd",
                    factory=object(),
                    magic_commands=(MagicCommand("vd"), MagicCommand("sql")),
                )
            ]
        )

        self.assertEqual("vd", registry.find_for_cell(["%%vd pods"]).handler_id)
        self.assertEqual("vd", registry.find_for_cell(["%%sql select 1"]).handler_id)
        self.assertIsNone(registry.find_for_cell(["print('x')"]))

    def test_registry_rejects_duplicate_handler_ids(self) -> None:
        registry = DisplayHandlerRegistry()
        registry.register(DisplayHandlerSpec(handler_id="vd", factory=object()))

        with self.assertRaisesRegex(ValueError, "Duplicate display handler id"):
            registry.register(DisplayHandlerSpec(handler_id="vd", factory=object()))

    def test_registry_rejects_duplicate_magic_commands(self) -> None:
        registry = DisplayHandlerRegistry(
            [DisplayHandlerSpec(handler_id="vd", factory=object(), magic_commands=(MagicCommand("vd"),))]
        )

        with self.assertRaisesRegex(ValueError, "Duplicate magic command"):
            registry.register(DisplayHandlerSpec(handler_id="sql", factory=object(), magic_commands=(MagicCommand("vd"),)))

    def test_load_display_handler_specs_from_entry_points(self) -> None:
        spec = DisplayHandlerSpec(
            handler_id="vd",
            factory=object(),
            magic_commands=(MagicCommand("vd"),),
        )
        entry_points = SelectableEntryPoints({DISPLAY_HANDLER_ENTRY_POINT_GROUP: [FakeEntryPoint(spec)]})

        with patch("jusi.plugins.metadata.entry_points", return_value=entry_points):
            loaded = load_display_handler_specs()

        self.assertEqual((spec,), loaded)

    def test_load_display_handler_specs_accepts_factory_entry_point(self) -> None:
        entry_points = SelectableEntryPoints({DISPLAY_HANDLER_ENTRY_POINT_GROUP: [FakeEntryPoint(build_sql_spec)]})

        with patch("jusi.plugins.metadata.entry_points", return_value=entry_points):
            loaded = load_display_handler_specs()

        self.assertEqual("sql", loaded[0].handler_id)
        self.assertEqual("sql", loaded[0].magic_commands[0].name)

    def test_build_display_handler_registry_combines_builtins_and_entry_points(self) -> None:
        entry_points = SelectableEntryPoints(
            {
                DISPLAY_HANDLER_ENTRY_POINT_GROUP: [
                    FakeEntryPoint(
                        DisplayHandlerSpec(
                            handler_id="sql",
                            factory=object,
                            magic_commands=(MagicCommand("sql"),),
                        )
                    )
                ]
            }
        )

        with patch("jusi.plugins.metadata.entry_points", return_value=entry_points):
            registry = build_display_handler_registry()

        self.assertEqual("vd", registry.find_for_cell(["%%vd pods"]).handler_id)
        self.assertEqual("sql", registry.find_for_cell(["%%sql select 1"]).handler_id)

    def test_protocol_server_uses_registry_to_mark_handler_owned_execution(self) -> None:
        registry = DisplayHandlerRegistry(
            [
                DisplayHandlerSpec(
                    handler_id="sql",
                    factory=VDDisplayHandler,
                    magic_commands=(MagicCommand("sql"),),
                )
            ]
        )
        server = ProtocolServer(runtime=InMemoryKernelRuntime(), display_handlers=registry)

        start_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "start_session", '
                '"request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}'
            )
        )
        start_envelopes = [parse_envelope(message) for message in start_messages]
        session_id = start_envelopes[2].payload["session"]["id"]
        client_id = start_envelopes[4].payload["prepared"]["id"]

        server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "bind_prepared_client", '
                '"request_id": "req-bind", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "client_id": "'
                + client_id
                + '", "client_bufnr": 91}}'
            )
        )

        execute_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "code", "syntax": "sql", "main_lines": ["%%sql", "select 1"]}}}'
            )
        )
        execute_envelopes = [parse_envelope(message) for message in execute_messages]
        self.assertEqual("handler", execute_envelopes[3].payload["cell"]["owner"]["kind"])
        self.assertEqual("follow-up", execute_envelopes[7].payload["cell"]["status"])

    def test_builtin_vd_handler_executes_without_magic_cell_kind(self) -> None:
        with patch.dict("os.environ", {"JUSI_VD_CMD": TEST_VD_CMD}):
            server = ProtocolServer(runtime=InMemoryKernelRuntime())

            start_messages = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "start_session", '
                    '"request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}'
                )
            )
            start_envelopes = [parse_envelope(message) for message in start_messages]
            session_id = start_envelopes[2].payload["session"]["id"]
            client_id = start_envelopes[4].payload["prepared"]["id"]

            server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "bind_prepared_client", '
                    '"request_id": "req-bind", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '", "client_id": "'
                    + client_id
                    + '", "client_bufnr": 91}}'
                )
            )

            execute_messages = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "execute_cell", '
                    '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "main_lines": ["%%vd pods"]}}}'
                )
            )
            execute_envelopes = [parse_envelope(message) for message in execute_messages]
            self.assertEqual("handler", execute_envelopes[3].payload["cell"]["owner"]["kind"])
            self.assertEqual("handler_message", execute_envelopes[5].type)
            self.assertEqual("handler_snapshot", execute_envelopes[5].payload["message_type"])
            self.assertEqual("handler_message", execute_envelopes[6].type)
            self.assertEqual("action_request", execute_envelopes[6].payload["message_type"])
            action_payload = execute_envelopes[6].payload["payload"]
            self.assertEqual("pods", action_payload["payload"]["expression"])
            self.assertEqual("json", action_payload["payload"]["source"]["format"])
            self.assertTrue(action_payload["payload"]["source"]["path"].endswith(".json"))
            self.assertEqual("follow-up", execute_envelopes[7].payload["cell"]["status"])

            active_client_id = execute_envelopes[3].payload["cell"]["client_id"]
            lines = inspect_client_lines(server, session_id, active_client_id)
            self.assertIn("handler> vd mode=browse", lines)
            self.assertIn("handler.entry> %%vd pods", lines)
            self.assertIn("frontend.action> vd.bootstrap", lines)
            server.close()

    def test_handler_message_request_roundtrips_into_active_vd_handler(self) -> None:
        with patch.dict("os.environ", {"JUSI_VD_CMD": TEST_VD_CMD}):
            server, session_id, active_client_id = start_bound_vd_server()

            lines = inspect_client_lines(server, session_id, active_client_id)
            self.assertTrue(any(line.startswith("handler.event> frontend_message ") for line in lines))
            self.assertIn("handler> vd mode=live", lines)

            input_response = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "handler_message", '
                    '"request_id": "req-handler-input", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '", "client_id": "'
                    + active_client_id
                    + '", "handler_id": "vd", "message_type": "send_input", "payload": {"text": "open pods"}}}'
                )
            )
            self.assertTrue(parse_envelope(input_response[0]).ok)

            pushed = wait_for_handler_messages(
                server,
                message_types=["terminal_input", "terminal_bytes"],
            )
            self.assertTrue(any(payload["message_type"] == "terminal_input" and payload["payload"]["text"] == "open pods" for payload in pushed))
            self.assertIn("echo open pods", decode_terminal_bytes(pushed))
            server.close()

    def test_terminal_input_requires_enter_to_submit(self) -> None:
        with patch.dict("os.environ", {"JUSI_VD_CMD": TEST_VD_CMD}):
            server, session_id, active_client_id = start_bound_vd_server()
            response = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "handler_message", '
                    '"request_id": "req-terminal-input", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '", "client_id": "'
                    + active_client_id
                    + '", "handler_id": "vd", "message_type": "terminal_input", "payload": {"text": "open pods"}}}'
                )
            )
            self.assertTrue(parse_envelope(response[0]).ok)
            pushed = wait_for_handler_messages(server, message_types=["terminal_input"])
            self.assertTrue(any(payload["message_type"] == "terminal_input" for payload in pushed))
            lines = inspect_client_lines(server, session_id, active_client_id)
            self.assertNotIn("handler.out> echo open pods", lines)

            enter_response = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "handler_message", '
                    '"request_id": "req-terminal-key", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '", "client_id": "'
                    + active_client_id
                    + '", "handler_id": "vd", "message_type": "terminal_key", "payload": {"key": "enter"}}}'
                )
            )
            self.assertTrue(parse_envelope(enter_response[0]).ok)
            pushed = wait_for_handler_messages(server, message_types=["terminal_key", "terminal_bytes"])
            self.assertTrue(any(payload["message_type"] == "terminal_key" for payload in pushed))
            self.assertIn("echo open pods", decode_terminal_bytes(pushed))
            server.close()

    def test_terminal_signal_interrupt_reaches_pty_process(self) -> None:
        with patch.dict("os.environ", {"JUSI_VD_CMD": TEST_VD_SIGNAL_CMD}):
            server, session_id, active_client_id = start_bound_vd_server(command=TEST_VD_SIGNAL_CMD)
            response = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "handler_message", '
                    '"request_id": "req-terminal-signal", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '", "client_id": "'
                    + active_client_id
                    + '", "handler_id": "vd", "message_type": "terminal_signal", "payload": {"name": "interrupt"}}}'
                )
            )
            self.assertTrue(parse_envelope(response[0]).ok)
            pushed = wait_for_handler_messages(server, message_types=["terminal_bytes"])
            self.assertIn("INT", decode_terminal_bytes(pushed))
            server.close()


if __name__ == "__main__":
    unittest.main()
