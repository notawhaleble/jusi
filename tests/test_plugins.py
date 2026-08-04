import json
import os
import tempfile
import threading
import unittest
import time
from typing import Any
from types import SimpleNamespace
from unittest.mock import patch

from jusi.domain.models import ExecutableCell, HandlerHandoff
from jusi.plugins import (
    BaseHandler,
    BasePluginRuntimeVdHandler,
    BaseVdHandler,
    collect_plugin_palette,
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
    run_plugin_runtime,
)
from jusi.infrastructure.runtime_supervisor import monitor_supervisor_liveness
from jusi.infrastructure.runtime import InMemoryKernelRuntime
from jusi.visidata_support import (
    JUSI_PLUGIN_FRONTEND_ACTIONS_ENV,
    JUSI_VISIDATARC_ENV,
    append_plugin_frontend_action,
    apply_pending_visidata_terminal_resize,
    bind_visidata_runtime,
    dispatch_visidata_control_request,
    handle_plugin_runtime_control_request,
    install_visidata_runtime_hooks,
    load_visidatarc_from_env,
    normalize_visidatarc_content,
    open_plugin_url,
    queue_visidata_terminal_resize,
    request_blocking_edit,
)
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


class FakePluginRuntimeVDHandler(BasePluginRuntimeVdHandler):
    def handler_id(self) -> str:
        return "fake_runtime_vd"

    def normalize_followup_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        normalized["normalized"] = "followup"
        return normalized

    def normalize_complete_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        normalized["normalized"] = "complete"
        return normalized

    def normalize_completion_items(self, payload, items):  # type: ignore[no-untyped-def]
        _ = payload
        return [dict(item, detail="normalized") for item in items]


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
        ), patch("jusi_vd.runner.run_vd_runner", return_value=7), patch(
            "jusi.infrastructure.plugin_runtime.load_visidatarc_from_env"
        ) as load_visidatarc_from_env, patch(
            "jusi.infrastructure.plugin_runtime.set_plugin_control_handler"
        ) as set_plugin_control_handler:
            self.assertEqual(7, run_plugin_runtime())
        load_visidatarc_from_env.assert_called_once_with()
        set_plugin_control_handler.assert_called_once()

    def test_run_plugin_runtime_emits_error_record_for_nonzero_runner_exit(self) -> None:
        records: list[dict[str, Any]] = []

        def failing_runner() -> int:
            os.sys.stderr.write("missing dependency\n")
            return 2

        with patch.dict(
            "os.environ",
            {"JUSI_PLUGIN_RUNTIME_CALLABLE": "jusi_vd.runner:run_vd_runner", "JUSI_VD_PAYLOAD_JSON": json.dumps({"content": "", "meta": {}})},
            clear=True,
        ), patch("jusi_vd.runner.run_vd_runner", side_effect=failing_runner), patch(
            "jusi.infrastructure.plugin_runtime.emit_plugin_runtime_record",
            side_effect=lambda record: records.append(dict(record)) or True,
        ):
            self.assertEqual(2, run_plugin_runtime())

        self.assertEqual("execution_event", records[0]["record_type"])
        self.assertEqual("error", records[0]["payload"]["type"])
        self.assertEqual("PluginRuntimeError", records[0]["payload"]["ename"])
        self.assertEqual("missing dependency", records[0]["payload"]["evalue"])
        self.assertEqual({"record_type": "execution_status", "status": "error"}, records[1])

    def test_run_plugin_runtime_starts_supervisor_monitor_when_configured(self) -> None:
        events: list[str] = []

        class FakeThread:
            def __init__(self, *, target=None, args=(), kwargs=None, daemon=False):  # type: ignore[no-untyped-def]
                _ = target, args, kwargs, daemon
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

    def test_normalize_visidatarc_content_adds_trailing_newline(self) -> None:
        self.assertEqual("a=1\n", normalize_visidatarc_content("a=1"))
        self.assertEqual("a=1\n", normalize_visidatarc_content("a=1\n"))
        self.assertEqual("", normalize_visidatarc_content(""))

    def test_load_visidatarc_from_env_uses_env_delivered_content(self) -> None:
        loaded_paths: list[str] = []

        class FakeVD:
            @staticmethod
            def loadConfigFile(path: str) -> None:
                loaded_paths.append(path)
                with open(path, "r", encoding="utf-8") as handle:
                    self.assertEqual("vd.set_theme('asciimono')\n", handle.read())

        fake_visidata = SimpleNamespace(vd=FakeVD())
        with patch.dict("os.environ", {JUSI_VISIDATARC_ENV: "vd.set_theme('asciimono')"}, clear=True), patch.dict(
            "sys.modules", {"visidata": fake_visidata}
        ):
            self.assertTrue(load_visidatarc_from_env())

        self.assertEqual(1, len(loaded_paths))

    def test_append_plugin_frontend_action_writes_jsonl_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "actions.jsonl")
            with patch.dict("os.environ", {JUSI_PLUGIN_FRONTEND_ACTIONS_ENV: path}, clear=True):
                self.assertTrue(append_plugin_frontend_action("yank_text", {"text": "abc"}))
            with open(path, "r", encoding="utf-8") as handle:
                self.assertEqual(
                    {"action_type": "yank_text", "payload": {"text": "abc"}},
                    json.loads(handle.read().strip()),
                )

    def test_open_plugin_url_writes_open_url_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "actions.jsonl")
            with patch.dict("os.environ", {JUSI_PLUGIN_FRONTEND_ACTIONS_ENV: path}, clear=True):
                self.assertTrue(open_plugin_url("https://example.com/report", open_in="tab"))
            with open(path, "r", encoding="utf-8") as handle:
                self.assertEqual(
                    {
                        "action_type": "open_url",
                        "payload": {
                            "url": "https://example.com/report",
                            "open_in": "tab",
                        },
                    },
                    json.loads(handle.read().strip()),
                )

    def test_request_blocking_edit_waits_for_action_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "actions.jsonl")

            def reply() -> None:
                deadline = time.time() + 1.0
                request_id = ""
                while time.time() < deadline and not request_id:
                    if os.path.exists(path):
                        with open(path, "r", encoding="utf-8") as handle:
                            for line in handle:
                                if not line.strip():
                                    continue
                                request_id = json.loads(line)["payload"]["request_id"]
                                break
                    time.sleep(0.01)
                self.assertTrue(request_id)
                handle_plugin_runtime_control_request(
                    {"message_type": "action_result", "payload": {"request_id": request_id, "ok": True}}
                )

            thread = threading.Thread(target=reply, daemon=True)
            thread.start()
            with patch.dict("os.environ", {JUSI_PLUGIN_FRONTEND_ACTIONS_ENV: path}, clear=True):
                result = request_blocking_edit("/tmp/example.txt", line=4)
            thread.join(timeout=1.0)

            self.assertEqual({"request_id": result["request_id"], "ok": True}, result)
            with open(path, "r", encoding="utf-8") as handle:
                record = json.loads(handle.read().strip())
            self.assertEqual("edit_path", record["action_type"])
            self.assertEqual("/tmp/example.txt", record["payload"]["path"])
            self.assertEqual(4, record["payload"]["line"])

    def test_install_visidata_runtime_hooks_redirects_syscopy_to_frontend_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "actions.jsonl")

            class FakeVD:
                def __init__(self) -> None:
                    self.statuses: list[str] = []

                def status(self, message: str) -> None:
                    self.statuses.append(message)

            class FakeSheet:
                pass

            fake_vd = FakeVD()
            fake_visidata = SimpleNamespace(vd=fake_vd, Sheet=FakeSheet)

            class FakeCol:
                def getDisplayValue(self, row):  # type: ignore[no-untyped-def]
                    return row

            with patch.dict(
                "os.environ",
                {JUSI_PLUGIN_FRONTEND_ACTIONS_ENV: path},
                clear=True,
            ), patch.dict("sys.modules", {"visidata": fake_visidata}):
                self.assertTrue(install_visidata_runtime_hooks())
                FakeSheet.syscopyValue(object(), "cell text")
                FakeSheet.syscopyCells_async(object(), [FakeCol()], ["row1", "row2"])

            with open(path, "r", encoding="utf-8") as handle:
                records = [json.loads(line) for line in handle if line.strip()]

            self.assertEqual(
                [
                    {"action_type": "yank_text", "payload": {"text": "cell text"}},
                    {"action_type": "yank_text", "payload": {"text": "row1\nrow2"}},
                ],
                records,
            )
            self.assertEqual(["yanked value to editor", "yanked selection to editor"], fake_vd.statuses)

    def test_visidata_runtime_hooks_dispatch_followup_and_complete_from_active_sheet(self) -> None:
        class FakeRuntime:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []

            def handle_followup(self, payload: dict[str, Any]) -> dict[str, Any]:
                self.calls.append(("followup", dict(payload)))
                return {"handled": True}

            def handle_complete(self, payload: dict[str, Any]) -> dict[str, Any]:
                self.calls.append(("complete", dict(payload)))
                return {"items": [{"value": "pods"}]}

        class FakeVD:
            pass

        class FakeBaseSheet:
            pass

        class DerivedSheet(FakeBaseSheet):
            pass

        fake_vd = FakeVD()
        fake_visidata = SimpleNamespace(vd=fake_vd, Sheet=FakeBaseSheet, BaseSheet=FakeBaseSheet)
        sheet = DerivedSheet()
        runtime = FakeRuntime()

        with patch.dict("sys.modules", {"visidata": fake_visidata}):
            self.assertTrue(install_visidata_runtime_hooks())
            bind_visidata_runtime(sheet, runtime)
            fake_vd.activeSheet = sheet

            followup_result = dispatch_visidata_control_request(
                {"message_type": "followup", "payload": {"cell_text": "select 1"}}
            )
            complete_result = dispatch_visidata_control_request(
                {"message_type": "complete", "payload": {"line_text": "sel"}}
            )

        self.assertEqual({"handled": True}, followup_result)
        self.assertEqual({"items": [{"value": "pods"}]}, complete_result)
        self.assertEqual(
            [
                ("followup", {"cell_text": "select 1"}),
                ("complete", {"line_text": "sel"}),
            ],
            runtime.calls,
        )

    def test_visidata_control_request_queues_resize_and_wakes_curses(self) -> None:
        class FakeVD:
            def __init__(self) -> None:
                self.commands: list[str] = []

            def queueCommand(self, command: str) -> None:
                self.commands.append(command)

        fake_vd = FakeVD()
        fake_visidata = SimpleNamespace(vd=fake_vd)

        with patch.dict("sys.modules", {"visidata": fake_visidata}), patch("curses.ungetch") as ungetch:
            result = dispatch_visidata_control_request(
                {"message_type": "terminal_resize", "payload": {"rows": 42, "cols": 121}}
            )
            duplicate = queue_visidata_terminal_resize({"rows": 42, "cols": 121})

        self.assertEqual(["jusi-terminal-resize"], fake_vd.commands)
        self.assertEqual(42, result["rows"])
        self.assertEqual(121, result["cols"])
        self.assertTrue(result["queued"])
        self.assertTrue(duplicate["queued"])
        self.assertEqual(2, ungetch.call_count)

    def test_apply_pending_visidata_terminal_resize_runs_on_visidata_command_path(self) -> None:
        class FakeScreen:
            def __init__(self) -> None:
                self.cleared = False

            def clear(self) -> None:
                self.cleared = True

        fake_vd = SimpleNamespace(
            _jusi_pending_terminal_resize=(37, 96),
            _jusi_terminal_resize_queued=True,
            scrFull=FakeScreen(),
        )
        fake_vd.setWindows = unittest.mock.Mock()

        with patch("curses.update_lines_cols") as update_lines_cols, patch("curses.resizeterm") as resizeterm:
            result = apply_pending_visidata_terminal_resize(fake_vd)

        update_lines_cols.assert_called_once_with()
        resizeterm.assert_called_once_with(37, 96)
        self.assertTrue(fake_vd.scrFull.cleared)
        fake_vd.setWindows.assert_called_once_with(fake_vd.scrFull)
        self.assertEqual({"applied": True, "rows": 37, "cols": 96}, result)

    def test_visidata_control_request_rejects_invalid_resize(self) -> None:
        self.assertEqual(
            {"queued": False, "reason": "invalid_geometry"},
            dispatch_visidata_control_request(
                {"message_type": "terminal_resize", "payload": {"rows": 0, "cols": "wide"}}
            ),
        )

    def test_install_visidata_runtime_hooks_redirects_launch_editor_to_open_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "actions.jsonl")

            class FakeVD:
                def __init__(self) -> None:
                    self.statuses: list[str] = []
                    self.globals_added: dict[str, object] = {}

                def status(self, message: str) -> None:
                    self.statuses.append(message)

                def addGlobals(self, **kwargs):  # type: ignore[no-untyped-def]
                    self.globals_added.update(kwargs)

                def launchEditor(self, *args):  # type: ignore[no-untyped-def]
                    raise AssertionError("original launchEditor should not be called for open-path flow")

            class FakeSheet:
                pass

            fake_vd = FakeVD()
            fake_visidata = SimpleNamespace(vd=fake_vd, Sheet=FakeSheet)

            with patch.dict(
                "os.environ",
                {JUSI_PLUGIN_FRONTEND_ACTIONS_ENV: path},
                clear=True,
            ), patch.dict("sys.modules", {"visidata": fake_visidata}):
                self.assertTrue(install_visidata_runtime_hooks())
                fake_vd.launchEditor("/tmp/example.txt", "+12")

            with open(path, "r", encoding="utf-8") as handle:
                records = [json.loads(line) for line in handle if line.strip()]

            self.assertEqual(
                [{"action_type": "open_path", "payload": {"path": "/tmp/example.txt", "line": 12}}],
                records,
            )
            self.assertEqual(["opened path in editor"], fake_vd.statuses)
            self.assertIn("launchEditor", fake_vd.globals_added)
            self.assertIn("launchExternalEditorPath", fake_vd.globals_added)

    def test_install_visidata_runtime_hooks_preserves_blocking_external_editor_path(self) -> None:
        original_calls: list[tuple[object, ...]] = []

        class FakeVD:
            def __init__(self) -> None:
                self.statuses: list[str] = []
                self.exceptions: list[str] = []

            def status(self, message: str) -> None:
                self.statuses.append(message)

            def exceptionCaught(self, exc: Exception) -> None:
                self.exceptions.append(str(exc))

            def launchEditor(self, *args):  # type: ignore[no-untyped-def]
                original_calls.append(tuple(args))
                return 0

        class FakeSheet:
            pass

        fake_vd = FakeVD()
        fake_visidata = SimpleNamespace(vd=fake_vd, Sheet=FakeSheet)

        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=True) as handle, patch.dict(
            "sys.modules", {"visidata": fake_visidata}
        ):
            handle.write("edited value\n")
            handle.flush()
            self.assertTrue(install_visidata_runtime_hooks())
            result = fake_vd.launchExternalEditorPath(handle.name, 7)

        self.assertEqual([(handle.name, "+7")], original_calls)
        self.assertEqual("edited value", result)
        self.assertEqual([], fake_vd.exceptions)

    def test_install_visidata_runtime_hooks_blocks_on_edit_path_without_editor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "actions.jsonl")

            class FakeVD:
                def __init__(self) -> None:
                    self.statuses: list[str] = []
                    self.globals_added: dict[str, object] = {}

                def status(self, message: str) -> None:
                    self.statuses.append(message)

                def addGlobals(self, **kwargs):  # type: ignore[no-untyped-def]
                    self.globals_added.update(kwargs)

                def exceptionCaught(self, exc: Exception) -> None:
                    raise AssertionError(str(exc))

            class FakeSheet:
                pass

            fake_vd = FakeVD()
            fake_visidata = SimpleNamespace(vd=fake_vd, Sheet=FakeSheet)

            def reply() -> None:
                deadline = time.time() + 1.0
                request_id = ""
                while time.time() < deadline and not request_id:
                    if os.path.exists(path):
                        with open(path, "r", encoding="utf-8") as handle:
                            for line in handle:
                                if not line.strip():
                                    continue
                                request_id = json.loads(line)["payload"]["request_id"]
                                break
                    time.sleep(0.01)
                self.assertTrue(request_id)
                handle_plugin_runtime_control_request(
                    {"message_type": "action_result", "payload": {"request_id": request_id, "ok": True}}
                )

            thread = threading.Thread(target=reply, daemon=True)
            thread.start()

            with patch.dict(
                "os.environ",
                {JUSI_PLUGIN_FRONTEND_ACTIONS_ENV: path},
                clear=True,
            ), patch.dict("sys.modules", {"visidata": fake_visidata}):
                self.assertTrue(install_visidata_runtime_hooks())
                returned = fake_vd.launchExternalEditor("buffer text", 5)
            thread.join(timeout=1.0)

            with open(path, "r", encoding="utf-8") as handle:
                records = [json.loads(line) for line in handle if line.strip()]

            self.assertEqual("buffer text", returned)
            self.assertEqual(1, len(records))
            self.assertEqual("edit_path", records[0]["action_type"])
            self.assertEqual(5, records[0]["payload"]["line"])
            self.assertEqual([], fake_vd.statuses)

    def test_plugin_runtime_monitor_requests_shutdown_when_supervisor_is_lost(self) -> None:
        stop_event = SimpleNamespace(is_set=lambda: False, wait=lambda _seconds: None)
        with patch("jusi.infrastructure.runtime_supervisor.supervisor_is_alive", return_value=False), patch(
            "jusi.infrastructure.runtime_supervisor.request_process_shutdown"
        ) as request_shutdown:
            monitor_supervisor_liveness(123, stop_event, poll_interval_seconds=0.01)  # type: ignore[arg-type]
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
            self.assertFalse(os.path.exists(pid_path))

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

    def test_vd_base_complete_and_followup_hooks_emit_results(self) -> None:
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

        handler.on_frontend_message(context, "complete", {"prefix": "pod"})  # type: ignore[arg-type]
        handler.on_frontend_message(context, "followup", {"cell_text": "show pods"})  # type: ignore[arg-type]

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
            pushed[0],
        )
        self.assertEqual(
            ("followup_result", {"handler_id": "fake_vd", "payload": {"cell_text": "SHOW PODS"}}),
            pushed[1],
        )

    def test_vd_plugin_runtime_base_forwards_complete_and_followup(self) -> None:
        handler = FakePluginRuntimeVDHandler()
        calls: list[tuple[str, dict[str, Any]]] = []
        pushed: list[tuple[str, dict[str, Any]]] = []

        def call_backend_action(action_name: str, payload: dict[str, Any]) -> dict[str, Any]:
            calls.append((action_name, payload))
            if payload["message_type"] == "complete":
                return {"items": [{"value": "SELECT", "label": "SELECT"}]}
            return {"handled": True}

        context = type(
            "Ctx",
            (),
            {
                "call_backend_action": lambda _self, action_name, payload: call_backend_action(action_name, payload),
                "send_frontend_message": lambda _self, message_type, payload: pushed.append((message_type, payload)),
            },
        )()

        handler.on_frontend_message(context, "complete", {"line_text": "SEL"})  # type: ignore[arg-type]
        handler.on_frontend_message(context, "followup", {"cell_text": "select 1"})  # type: ignore[arg-type]

        self.assertEqual(
            [
                ("plugin_runtime_request", {"message_type": "complete", "payload": {"line_text": "SEL", "normalized": "complete"}}),
                ("plugin_runtime_request", {"message_type": "followup", "payload": {"cell_text": "select 1", "normalized": "followup"}}),
            ],
            calls,
        )
        self.assertEqual(
            (
                "complete_result",
                {
                    "handler_id": "fake_runtime_vd",
                    "message_type": "complete",
                    "items": [
                        {
                            "value": "SELECT",
                            "label": "SELECT",
                            "kind": None,
                            "detail": "normalized",
                            "documentation": None,
                            "start_col": None,
                            "end_col": None,
                        }
                    ],
                },
            ),
            pushed[0],
        )

    def test_vd_handlers_forward_terminal_resize_to_plugin_runtime(self) -> None:
        for handler in (FakeVDHandler(), FakePluginRuntimeVDHandler()):
            calls: list[tuple[str, dict[str, Any]]] = []
            context = SimpleNamespace(
                call_backend_action=lambda action_name, payload: calls.append((action_name, payload)) or {}
            )

            handler.on_frontend_message(context, "terminal_resize", {"rows": 51, "cols": 88})  # type: ignore[arg-type]

            self.assertEqual(
                [
                    (
                        "plugin_runtime_request",
                        {"message_type": "terminal_resize", "payload": {"rows": 51, "cols": 88}},
                    )
                ],
                calls,
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

    def test_registry_substitutes_blank_magic_body_with_declared_bootstrap_body(self) -> None:
        registry = DisplayHandlerRegistry(
            [
                DisplayHandlerSpec(
                    handler_id="sql",
                    factory=object(),
                    magic_commands=(MagicCommand("sql", bootstrap_body=lambda first_line: f"-- bootstrap {first_line}"),),
                )
            ]
        )
        cell = ExecutableCell(cell_id=12, kind="magic", syntax="sql", main_lines=["%%sql prod", " ", ""])

        bootstrapped = registry.cell_with_blank_body_bootstrap(cell)
        nonblank = ExecutableCell(12, "magic", "sql", ["%%sql prod", "select 1"])

        self.assertEqual(["%%sql prod", "-- bootstrap %%sql prod"], bootstrapped.main_lines)
        self.assertIs(nonblank, registry.cell_with_blank_body_bootstrap(nonblank))

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
        entry_points = SelectableEntryPoints(
            {
                DISPLAY_HANDLER_ENTRY_POINT_GROUP: [
                    FakeEntryPoint(
                        DisplayHandlerSpec(
                            handler_id="vd",
                            factory=object,
                            magic_commands=(MagicCommand("vd"),),
                            kernel_extension_modules=("jusi_vd.kernel",),
                        )
                    ),
                    FakeEntryPoint(
                        DisplayHandlerSpec(
                            handler_id="shell",
                            factory=object,
                            magic_commands=(MagicCommand("shell"),),
                            kernel_extension_modules=("jusi_shell.kernel",),
                        )
                    ),
                    FakeEntryPoint(
                        DisplayHandlerSpec(
                            handler_id="sql",
                            factory=object,
                            magic_commands=(MagicCommand("sql"),),
                            kernel_extension_modules=("jusi_sql.kernel",),
                        )
                    ),
                ]
            }
        )

        with patch("jusi.plugins.metadata.entry_points", return_value=entry_points):
            registry = build_display_handler_registry()

        modules = collect_kernel_extension_modules(registry)
        self.assertEqual(("jusi_vd.kernel", "jusi_shell.kernel", "jusi_sql.kernel"), modules)

    def test_collect_plugin_presentation_specs_uses_family_presentation_by_magic_name(self) -> None:
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
                    presentation={"syntax": "pgsql", "indent": "sql", "followup": True, "completion": True},
                    family_presentation={"syntax": "sql", "indent": "sql", "followup": True, "completion": True},
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
                "shared": {"syntax": "sql", "indent": "sql", "followup": True, "completion": True},
            },
            collect_plugin_presentation_specs(registry),
        )

    def test_collect_plugin_palette_uses_magic_names(self) -> None:
        registry = DisplayHandlerRegistry(
            (
                DisplayHandlerSpec(
                    handler_id="sqlite",
                    factory=object,  # type: ignore[arg-type]
                    magic_commands=(MagicCommand("sql"),),
                ),
                DisplayHandlerSpec(
                    handler_id="postgres",
                    factory=object,  # type: ignore[arg-type]
                    magic_commands=(MagicCommand("sql"),),
                ),
                DisplayHandlerSpec(
                    handler_id="mail",
                    factory=object,  # type: ignore[arg-type]
                    magic_commands=(MagicCommand("mail"),),
                ),
            )
        )

        self.assertEqual(
            {
                "sql": {"entries": ["analyticsdb", "mysqlitedb"]},
                "mail": {"entries": ["mymail"]},
            },
            collect_plugin_palette(
                registry,
                {
                    "sql": {"analyticsdb": {"provider": "postgres"}, "mysqlitedb": {"provider": "sqlite"}},
                    "mail": {"mymail": {"provider": "imap"}},
                },
            ),
        )

    def test_collect_plugin_palette_includes_plugins_without_config_entries(self) -> None:
        registry = DisplayHandlerRegistry(
            (
                DisplayHandlerSpec(
                    handler_id="vd",
                    factory=object,  # type: ignore[arg-type]
                    magic_commands=(MagicCommand("vd"),),
                ),
                DisplayHandlerSpec(
                    handler_id="shell",
                    factory=object,  # type: ignore[arg-type]
                    magic_commands=(MagicCommand("shell"),),
                ),
                DisplayHandlerSpec(
                    handler_id="sqlite",
                    factory=object,  # type: ignore[arg-type]
                    magic_commands=(MagicCommand("sql"),),
                ),
            )
        )

        self.assertEqual(
            {
                "vd": {"entries": []},
                "shell": {"entries": []},
                "sql": {"entries": ["mysqlitedb"]},
            },
            collect_plugin_palette(
                registry,
                {
                    "sql": {"mysqlitedb": {"provider": "sqlite"}},
                },
            ),
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

    def test_native_terminal_attach_env_redacts_unrelated_process_env(self) -> None:
        with patch.dict("os.environ", {"SECRET_TOKEN": "abc"}, clear=False), patch(
            "jusi_vd.plugin.util.find_spec", return_value=SimpleNamespace(name="visidata")
        ):
            server, session_id, active_client_id = start_bound_vd_server()

            inspect_messages = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "inspect_client", '
                    '"request_id": "req-inspect-redacted", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '", "client_id": "'
                    + active_client_id
                    + '"}}'
                )
            )
            inspect_envelope = [parse_envelope(message) for message in inspect_messages][0]
            transport = inspect_envelope.payload["client"]["transport"]
            child_env = json.loads(transport["attach_env"]["JUSI_TERMINAL_ENV_JSON"])

            self.assertNotIn("SECRET_TOKEN", child_env)
            server.close()

    def test_build_vd_env_redacts_unrelated_process_env(self) -> None:
        with patch.dict("os.environ", {"SECRET_TOKEN": "abc", "PATH": "/tmp/bin"}, clear=True):
            env = _build_vd_env()
        self.assertNotIn("SECRET_TOKEN", env)
        self.assertEqual("/tmp/bin", env["PATH"])

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
