import json
import os
import tempfile
import unittest
import time
from typing import Any
from types import SimpleNamespace
from unittest.mock import patch

from jusi.domain.models import HandlerHandoff
from jusi.plugins import (
    BaseHandler,
    BaseVdHandler,
    collect_kernel_extension_modules,
    collect_plugin_presentation_specs,
    DISPLAY_HANDLER_ENTRY_POINT_GROUP,
    DisplayHandlerRegistry,
    DisplayHandlerSpec,
    HandlerContext,
    MagicCommand,
    builtin_display_handler_specs,
    build_display_handler_registry,
    load_display_handler_specs,
    default_frontend_channel,
)
from jusi.infrastructure.client_process import run_terminal_attach
from jusi.infrastructure.plugin_runtime import (
    _monitor_supervisor_liveness,
    run_plugin_runtime,
)
from jusi.infrastructure.runtime import InMemoryKernelRuntime
from jusi.interfaces.protocol import parse_envelope
from jusi.interfaces.server import ProtocolServer
from jusi_vd.plugin import VDDisplayHandler, _build_vd_command, _build_vd_env

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


def start_bound_vd_server() -> tuple[ProtocolServer, str, str]:
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
            + '", "cell": {"id": 12, "kind": "magic", "syntax": "python", "main_lines": ["%%vd", "pods"]}}}'
        )
    )
    execute_envelopes = [parse_envelope(message) for message in execute_messages]
    active_client_id = execute_envelopes[3].payload["cell"]["client_id"]
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


class FakeVDHandler(BaseVdHandler):
    def handler_id(self) -> str:
        return "fake_vd"

    def complete(self, context, payload):  # type: ignore[no-untyped-def]
        _ = context
        prefix = str(payload.get("prefix", ""))
        return [
            {"value": prefix + "_one", "label": prefix + "_one", "kind": "row"},
            {"value": prefix + "_two", "label": prefix + "_two", "kind": "row"},
        ]

    def followup(self, context, payload):  # type: ignore[no-untyped-def]
        context.push_frontend_message(
            "followup_result",
            {
                "handler_id": self.handler_id(),
                "payload": {"cell_text": str(payload.get("cell_text", "")).upper()},
            },
        )


class AsyncBaseHandler(BaseHandler):
    def handler_id(self) -> str:
        return "async"

    async def handle(self, context, cell):  # type: ignore[no-untyped-def]
        _ = (context, cell)
        return "follow-up"


class FakeGenericHandler(BaseHandler):
    def handler_id(self) -> str:
        return "generic"

    def handle(self, context, cell):  # type: ignore[no-untyped-def]
        _ = (context, cell)
        return "follow-up"

    def complete(self, context, payload):  # type: ignore[no-untyped-def]
        _ = context
        prefix = str(payload.get("current_word", ""))
        return [{"value": prefix + "_done", "label": None, "kind": None}]

    def followup(self, context, payload):  # type: ignore[no-untyped-def]
        context.send_frontend_message(
            "followup_result",
            {"handler_id": self.handler_id(), "payload": {"cell_text": str(payload.get("cell_text", ""))}},
        )


class FakeOpenHandler(BaseHandler):
    def handler_id(self) -> str:
        return "open_handler"

    def handle(self, context, cell):  # type: ignore[no-untyped-def]
        _ = cell
        context.open_path("/tmp/example.txt", open_in="split", line=12, column=3)
        return "follow-up"


class PluginRegistryTest(unittest.TestCase):
    def test_builtin_display_handler_specs_include_vd(self) -> None:
        specs = builtin_display_handler_specs()
        self.assertEqual((), specs)

    def test_vd_env_defaults_term_to_xterm_256color(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            env = _build_vd_env()
        self.assertEqual("xterm-256color", env["TERM"])

    def test_base_handler_supports_async_handle(self) -> None:
        handler = AsyncBaseHandler()
        status = handler.execute(object(), object())  # type: ignore[arg-type]
        self.assertEqual("follow-up", status)

    def test_handler_context_open_path_requests_frontend_action(self) -> None:
        pushed: list[tuple[str, dict[str, Any]]] = []
        channel = default_frontend_channel(
            handler_id="open_handler",
            notebook_id="nb-1",
            session_id="sess-1",
            client_id="client-1",
            append_execution_event=lambda _event: None,
            emit_handler_message=lambda notebook_id, session_id, client_id, handler_id, message_type, payload: pushed.append(
                (
                    message_type,
                    {
                        "notebook_id": notebook_id,
                        "session_id": session_id,
                        "client_id": client_id,
                        "handler_id": handler_id,
                        "payload": dict(payload),
                    },
                )
            ),
        )
        context = HandlerContext(
            notebook_id="nb-1",
            session_id="sess-1",
            cell_id=5,
            client_id="client-1",
            channel=channel,
            push_frontend_message=lambda *_args: None,
            invoke_backend_action=lambda *_args: {},
            append_execution_event=lambda *_args: None,
            update_execution_status=lambda *_args: None,
            set_client_transport=lambda *_args: None,
        )

        FakeOpenHandler().execute(context, object())  # type: ignore[arg-type]

        self.assertEqual(
            [
                (
                    "action_request",
                    {
                        "notebook_id": "nb-1",
                        "session_id": "sess-1",
                        "client_id": "client-1",
                        "handler_id": "open_handler",
                        "payload": {
                            "action_type": "open_path",
                            "payload": {
                                "path": "/tmp/example.txt",
                                "open_in": "split",
                                "line": 12,
                                "column": 3,
                            },
                        },
                    },
                )
            ],
            pushed,
        )

    def test_run_terminal_attach_execs_advertised_command(self) -> None:
        captured = {}

        def fake_execvpe(executable, args, env):  # type: ignore[no-untyped-def]
            captured["executable"] = executable
            captured["args"] = list(args)
            captured["env"] = dict(env)
            raise SystemExit(0)

        with patch.dict(
            "os.environ",
            {
                "JUSI_TERMINAL_CMD_JSON": json.dumps(["/bin/sh", "-lc", "echo hi"]),
                "JUSI_TERMINAL_ENV_JSON": json.dumps({"TERM": "xterm-256color", "FOO": "bar"}),
            },
            clear=True,
        ), patch("jusi.infrastructure.client_process.os.execvpe", side_effect=fake_execvpe):
            with self.assertRaises(SystemExit):
                run_terminal_attach()

        self.assertEqual("/bin/sh", captured["executable"])
        self.assertEqual(["/bin/sh", "-lc", "echo hi"], captured["args"])
        self.assertEqual("xterm-256color", captured["env"]["TERM"])
        self.assertEqual("bar", captured["env"]["FOO"])

    def test_run_terminal_attach_clears_inherited_lines_and_columns(self) -> None:
        captured = {}

        def fake_execvpe(executable, args, env):  # type: ignore[no-untyped-def]
            captured["executable"] = executable
            captured["args"] = list(args)
            captured["env"] = dict(env)
            raise SystemExit(0)

        with patch.dict(
            "os.environ",
            {
                "LINES": "45",
                "COLUMNS": "158",
                "JUSI_TERMINAL_CMD_JSON": json.dumps(["/bin/sh", "-lc", "echo hi"]),
                "JUSI_TERMINAL_ENV_JSON": json.dumps({"TERM": "xterm-256color"}),
            },
            clear=True,
        ), patch("jusi.infrastructure.client_process.os.execvpe", side_effect=fake_execvpe):
            with self.assertRaises(SystemExit):
                run_terminal_attach()

        self.assertNotIn("LINES", captured["env"])
        self.assertNotIn("COLUMNS", captured["env"])

    def test_run_plugin_runtime_dispatches_to_configured_callable(self) -> None:
        with patch.dict(
            "os.environ",
            {"JUSI_PLUGIN_RUNTIME_CALLABLE": "jusi_vd.runner:run_vd_runner", "JUSI_VD_PAYLOAD_JSON": json.dumps({"content": "", "meta": {}})},
            clear=True,
        ), patch("jusi_vd.runner.run_vd_runner", return_value=7):
            self.assertEqual(7, run_plugin_runtime())

    def test_run_plugin_runtime_starts_supervisor_monitor_when_configured(self) -> None:
        events: list[str] = []

        class FakeThread:
            def __init__(self, *, target=None, args=(), daemon=False):  # type: ignore[no-untyped-def]
                _ = target, args, daemon
                self._alive = False

            def start(self) -> None:
                events.append("start")
                self._alive = True

            def is_alive(self) -> bool:
                return self._alive

            def join(self, timeout=None) -> None:  # type: ignore[no-untyped-def]
                _ = timeout
                events.append("join")
                self._alive = False

        with patch.dict(
            "os.environ",
            {
                "JUSI_PLUGIN_RUNTIME_CALLABLE": "jusi_vd.runner:run_vd_runner",
                "JUSI_VD_PAYLOAD_JSON": json.dumps({"content": "", "meta": {}}),
                "JUSI_SUPERVISOR_PID": "123",
            },
            clear=True,
        ), patch("jusi_vd.runner.run_vd_runner", return_value=7), patch(
            "jusi.infrastructure.plugin_runtime.threading.Thread", FakeThread
        ):
            self.assertEqual(7, run_plugin_runtime())

        self.assertEqual(["start", "join"], events)

    def test_plugin_runtime_monitor_requests_shutdown_when_supervisor_is_lost(self) -> None:
        stop_event = SimpleNamespace(is_set=lambda: False, wait=lambda _seconds: None)
        with patch("jusi.infrastructure.plugin_runtime._supervisor_is_alive", return_value=False), patch(
            "jusi.infrastructure.plugin_runtime._request_process_shutdown"
        ) as request_shutdown:
            _monitor_supervisor_liveness(123, stop_event)  # type: ignore[arg-type]
        request_shutdown.assert_called_once_with()

    def test_run_plugin_runtime_writes_pid_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_path = os.path.join(tmpdir, "plugin-runtime.pid")
            observed: dict[str, int] = {}

            def fake_runner() -> int:
                with open(pid_path, "r", encoding="utf-8") as handle:
                    observed["pid"] = int(handle.read().strip())
                return 7

            with patch.dict(
                "os.environ",
                {
                    "JUSI_PLUGIN_RUNTIME_CALLABLE": "jusi_vd.runner:run_vd_runner",
                    "JUSI_VD_PAYLOAD_JSON": json.dumps({"content": "", "meta": {}}),
                    "JUSI_PLUGIN_RUNTIME_PID_FILE": pid_path,
                },
                clear=True,
            ), patch("jusi_vd.runner.run_vd_runner", side_effect=fake_runner):
                self.assertEqual(7, run_plugin_runtime())

            self.assertEqual(os.getpid(), observed["pid"])
            self.assertTrue(os.path.exists(pid_path))

    def test_build_vd_command_finds_binary_next_to_sys_executable(self) -> None:
        with patch("jusi_vd.plugin.util.find_spec", return_value=SimpleNamespace(name="visidata")), patch(
            "jusi_vd.plugin.sys.executable", "/tmp/venv/bin/python"
        ):
            command, notice = _build_vd_command()
        self.assertEqual(["/tmp/venv/bin/python", "-m", "jusi", "plugin-runtime"], command)
        self.assertEqual("", notice)

    def test_build_vd_command_requires_visidata_module(self) -> None:
        with patch("jusi_vd.plugin.util.find_spec", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "VisiData is not available"):
                _build_vd_command()

    def test_vd_base_copy_complete_and_followup_hooks_emit_results(self) -> None:
        handler = FakeVDHandler()
        pushed: list[tuple[str, dict]] = []
        context = type(
            "Ctx",
            (),
            {
                "push_frontend_message": lambda _self, message_type, payload: pushed.append((message_type, payload)),
                "send_frontend_message": lambda _self, message_type, payload: pushed.append((message_type, payload)),
            },
        )()

        handler.handle_copy(context, {"text": "abc"})  # type: ignore[arg-type]
        handler.on_frontend_message(context, "complete", {"prefix": "pod"})  # type: ignore[arg-type]
        handler.on_frontend_message(context, "followup", {"cell_text": "show pods"})  # type: ignore[arg-type]

        self.assertEqual(("vd_copy_result", {"handler_id": "fake_vd", "message_type": "vd_copy", "text": "abc"}), pushed[0])
        self.assertEqual(
            (
                "complete_result",
                {
                    "handler_id": "fake_vd",
                    "message_type": "complete",
                    "items": [
                        {"value": "pod_one", "label": "pod_one", "kind": "row", "detail": None, "documentation": None, "start_col": None, "end_col": None},
                        {"value": "pod_two", "label": "pod_two", "kind": "row", "detail": None, "documentation": None, "start_col": None, "end_col": None},
                    ],
                },
            ),
            pushed[1],
        )
        self.assertEqual(
            ("followup_result", {"handler_id": "fake_vd", "payload": {"cell_text": "SHOW PODS"}}),
            pushed[2],
        )

    def test_base_handler_generic_complete_and_followup_hooks_emit_results(self) -> None:
        handler = FakeGenericHandler()
        pushed: list[tuple[str, dict]] = []
        context = type(
            "Ctx",
            (),
            {
                "emit_frontend_event": lambda _self, event_type, payload: pushed.append((event_type, payload)),
                "send_frontend_message": lambda _self, message_type, payload: pushed.append((message_type, payload)),
            },
        )()

        handler.on_frontend_message(
            context,
            "complete",
            {"cell_text": "select 1", "current_word": "sel", "line_text": "select 1", "cursor_row": 0, "cursor_col": 3},
        )  # type: ignore[arg-type]
        handler.on_frontend_message(
            context,
            "followup",
            {"cell_text": "select 2", "cursor_row": 0, "cursor_col": 8},
        )  # type: ignore[arg-type]

        self.assertEqual(
            (
                "complete_result",
                {
                    "handler_id": "generic",
                    "message_type": "complete",
                    "items": [{"value": "sel_done", "label": None, "kind": None, "detail": None, "documentation": None, "start_col": None, "end_col": None}],
                },
            ),
            pushed[0],
        )
        self.assertEqual(
            ("followup_result", {"handler_id": "generic", "payload": {"cell_text": "select 2"}}),
            pushed[1],
        )

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

    def test_registry_allows_duplicate_magic_commands_for_distinct_handlers(self) -> None:
        registry = DisplayHandlerRegistry(
            [DisplayHandlerSpec(handler_id="vd", factory=object(), magic_commands=(MagicCommand("vd"),))]
        )
        registry.register(DisplayHandlerSpec(handler_id="sql", factory=object(), magic_commands=(MagicCommand("vd"),)))
        self.assertIsNone(registry.find_for_cell(["%%vd"]))

    def test_registry_validates_handoff_against_magic_and_handler(self) -> None:
        registry = DisplayHandlerRegistry(
            [
                DisplayHandlerSpec(handler_id="sql-postgres", factory=object(), magic_commands=(MagicCommand("sql"),)),
                DisplayHandlerSpec(handler_id="sql-clickhouse", factory=object(), magic_commands=(MagicCommand("sql"),)),
            ]
        )

        self.assertEqual(
            "sql-clickhouse",
            registry.validate_handoff(
                HandlerHandoff(handler_id="sql-clickhouse", magic_name="sql", content="select 1")
            ).handler_id,
        )
        self.assertIsNone(
            registry.validate_handoff(
                HandlerHandoff(handler_id="sql-clickhouse", magic_name="vd", content="")
            )
        )

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
                            handler_id="vd",
                            factory=object,
                            magic_commands=(MagicCommand("vd"),),
                        )
                    ),
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

    def test_collect_kernel_extension_modules_reads_registry_specs(self) -> None:
        registry = build_display_handler_registry()
        modules = collect_kernel_extension_modules(registry)
        self.assertEqual(("jusi_vd.kernel", "jusi_shell.kernel", "jusi_sql.kernel"), modules)

    def test_collect_plugin_presentation_specs_uses_magic_names(self) -> None:
        registry = DisplayHandlerRegistry(
            (
                DisplayHandlerSpec(
                    handler_id="one",
                    factory=object,  # type: ignore[arg-type]
                    magic_commands=(MagicCommand("one"), MagicCommand("shared")),
                    presentation={"syntax": "sql", "indent": "sql", "followup": True, "completion": True},
                ),
                DisplayHandlerSpec(
                    handler_id="two",
                    factory=object,  # type: ignore[arg-type]
                    magic_commands=(MagicCommand("shared"),),
                    presentation={"syntax": "pgsql"},
                ),
                DisplayHandlerSpec(
                    handler_id="empty",
                    factory=object,  # type: ignore[arg-type]
                    magic_commands=(MagicCommand("empty"),),
                ),
            )
        )

        self.assertEqual(
            {
                "one": {"syntax": "sql", "indent": "sql", "followup": True, "completion": True},
                "shared": {"syntax": "pgsql", "indent": "sql", "followup": True, "completion": True},
            },
            collect_plugin_presentation_specs(registry),
        )

    def test_builtin_vd_handler_executes_from_magic_cell_handoff(self) -> None:
        with patch("jusi_vd.plugin.util.find_spec", return_value=SimpleNamespace(name="visidata")):
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
                    + '", "cell": {"id": 12, "kind": "magic", "syntax": "python", "main_lines": ["%%vd", "pods"]}}}'
                )
            )
            execute_envelopes = [parse_envelope(message) for message in execute_messages]
            cell_events = [envelope for envelope in execute_envelopes if envelope.type == "cell_updated"]
            self.assertEqual("handler", cell_events[-1].payload["cell"]["owner"]["kind"])
            handler_messages = [parse_envelope(message) for message in server.drain_pending_messages()]
            message_types = [envelope.payload["message_type"] for envelope in handler_messages]
            self.assertIn("handler_snapshot", message_types)
            self.assertEqual("follow-up", execute_envelopes[-1].payload["cell"]["status"])

            active_client_id = cell_events[0].payload["cell"]["client_id"]
            inspect_messages = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "inspect_client", '
                    '"request_id": "req-inspect-pre-bootstrap", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '", "client_id": "'
                    + active_client_id
                    + '"}}'
                )
            )
            inspect_envelope = [parse_envelope(message) for message in inspect_messages][0]
            self.assertEqual("native_terminal", inspect_envelope.payload["client"]["transport"]["kind"])
            self.assertTrue(inspect_envelope.payload["client"]["transport"]["attach_env"]["JUSI_TERMINAL_CMD_JSON"])
            lines = inspect_client_lines(server, session_id, active_client_id)
            self.assertIn("handler> vd mode=ready", lines)
            self.assertIn("handler.entry> %%vd", lines)
            server.close()

    def test_vd_transport_uses_jusi_vd_runner_with_payload(self) -> None:
        captured_transport = {}

        class FakeContext:
            notebook_id = "nb-1"
            session_id = "sess-1"
            cell_id = 12
            client_id = "client-1"
            magic_name = "vd"
            content = "gASVBwAAAAAAAACMA2FiY5Qu"
            meta = {"ftype": "pandas"}

            def __init__(self) -> None:
                self.channel = type("Channel", (), {"emit_event": lambda *_args, **_kwargs: None})()

            def invoke_backend_action(self, action_name, payload):  # type: ignore[no-untyped-def]
                raise AssertionError((action_name, payload))

            def append_execution_event(self, event):  # type: ignore[no-untyped-def]
                _ = event

            def update_execution_status(self, status):  # type: ignore[no-untyped-def]
                self.status = status

            def set_client_transport(self, transport):  # type: ignore[no-untyped-def]
                captured_transport.update(
                    {
                        "kind": transport.kind,
                        "attach_env": dict(transport.attach_env),
                    }
                )

        context = FakeContext()
        cell = type(
            "Cell",
            (),
            {
                "cell_id": 12,
                "kind": "magic",
                "syntax": "python",
                "main_lines": ["%%vd", "a"],
            },
        )()

        with patch.dict("os.environ", {}, clear=True), patch(
            "jusi_vd.plugin.util.find_spec", return_value=SimpleNamespace(name="visidata")
        ), patch("jusi_vd.plugin.sys.executable", "/tmp/venv/bin/python"):
            handler = VDDisplayHandler()
            status = handler.execute(context, cell)

        self.assertEqual("follow-up", status)
        self.assertEqual("native_terminal", captured_transport["kind"])
        advertised_command = json.loads(captured_transport["attach_env"]["JUSI_TERMINAL_CMD_JSON"])
        self.assertEqual(["/tmp/venv/bin/python", "-m", "jusi", "plugin-runtime"], advertised_command)
        child_env = json.loads(captured_transport["attach_env"]["JUSI_TERMINAL_ENV_JSON"])
        self.assertEqual("jusi_vd.runner:run_vd_runner", child_env["JUSI_PLUGIN_RUNTIME_CALLABLE"])
        self.assertEqual(
            {"content": "gASVBwAAAAAAAACMA2FiY5Qu", "meta": {"ftype": "pandas"}},
            json.loads(child_env["JUSI_VD_PAYLOAD_JSON"]),
        )

    def test_handler_message_still_records_frontend_messages(self) -> None:
        with patch("jusi_vd.plugin.util.find_spec", return_value=SimpleNamespace(name="visidata")):
            server, session_id, active_client_id = start_bound_vd_server()

            response = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "handler_message", '
                    '"request_id": "req-handler-compat", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '", "client_id": "'
                    + active_client_id
                    + '", "handler_id": "vd", "message_type": "frontend_debug", "payload": {"note": "ping"}}}'
                )
            )
            self.assertTrue(parse_envelope(response[0]).ok)
            lines = wait_for_client_lines(
                server,
                session_id,
                active_client_id,
                ['handler.event> frontend_message {"handler_id": "vd", "message_type": "frontend_debug", "payload": {"note": "ping"}}'],
            )
            self.assertTrue(any(line.startswith("handler.event> frontend_message ") for line in lines))
            self.assertIn("handler> vd mode=ready", lines)
            server.close()

    def test_bootstrap_exposes_native_terminal_transport_metadata(self) -> None:
        with patch("jusi_vd.plugin.util.find_spec", return_value=SimpleNamespace(name="visidata")):
            server, session_id, active_client_id = start_bound_vd_server()

            inspect_messages = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "inspect_client", '
                    '"request_id": "req-inspect-transport", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '", "client_id": "'
                    + active_client_id
                    + '"}}'
                )
            )
            inspect_envelope = [parse_envelope(message) for message in inspect_messages][0]
            transport = inspect_envelope.payload["client"]["transport"]

            self.assertEqual("native_terminal", transport["kind"])
            self.assertEqual([os.sys.executable, "-m", "jusi", "client-process", "terminal-attach"], transport["attach_cmd"])
            self.assertEqual(session_id, transport["session_id"])
            self.assertEqual(active_client_id, transport["client_id"])
            self.assertEqual("vd", transport["handler_id"])
            self.assertIn("JUSI_TERMINAL_CMD_JSON", transport["attach_env"])
            server.close()

    def test_bootstrap_propagates_pythonpath_into_native_terminal_attach_env(self) -> None:
        with patch.dict("os.environ", {"PYTHONPATH": "/tmp/jusi-src"}), patch(
            "jusi_vd.plugin.util.find_spec", return_value=SimpleNamespace(name="visidata")
        ):
            server, session_id, active_client_id = start_bound_vd_server()

            inspect_messages = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "inspect_client", '
                    '"request_id": "req-inspect-pythonpath", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '", "client_id": "'
                    + active_client_id
                    + '"}}'
                )
            )
            inspect_envelope = [parse_envelope(message) for message in inspect_messages][0]
            transport = inspect_envelope.payload["client"]["transport"]

            self.assertTrue(transport["attach_env"]["PYTHONPATH"].endswith("/tmp/jusi-src"))
            server.close()

    def test_native_terminal_attach_env_does_not_force_lines_or_columns(self) -> None:
        with patch("jusi_vd.plugin.util.find_spec", return_value=SimpleNamespace(name="visidata")):
            server, session_id, active_client_id = start_bound_vd_server()
            inspect_messages = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "inspect_client", '
                    '"request_id": "req-inspect-attach-env", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '", "client_id": "'
                    + active_client_id
                    + '"}}'
                )
            )
            inspect_envelope = [parse_envelope(message) for message in inspect_messages][0]
            transport = inspect_envelope.payload["client"]["transport"]
            child_env = json.loads(transport["attach_env"]["JUSI_TERMINAL_ENV_JSON"])

            self.assertNotIn("LINES", child_env)
            self.assertNotIn("COLUMNS", child_env)
            server.close()


if __name__ == "__main__":
    unittest.main()
