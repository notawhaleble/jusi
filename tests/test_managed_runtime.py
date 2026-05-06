import json
import os
import signal
import sys
import tempfile
import threading
import time
import unittest
from queue import Empty
from shlex import quote as shlex_quote
from types import SimpleNamespace
from typing import Optional
from unittest.mock import patch

from jusi.domain.models import CellExecution, ExecutableCell, Session, SessionTarget, JUSI_HANDLER_HANDOFF_MIME
from jusi.infrastructure.runtime import (
    JUSI_KERNEL_EXTENSIONS_ENV,
    JUSI_SESSION_CONFIG_ENV,
    InMemoryKernelRuntime,
    ManagedClientHandle,
    ManagedKernelRuntime,
    RuntimeDependencyError,
    _jusi_kernel_env,
    build_runtime,
)
from jusi.interfaces.protocol import parse_envelope
from jusi.interfaces.server import ProtocolServer


class FakeClient:
    def __init__(self) -> None:
        self.executed: list[tuple[str, bool | None]] = []
        self.input_replies: list[str] = []
        self.channels_stopped = False
        self.shutdown_called = False
        self.messages = [
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "stream",
                "content": {"name": "stdout", "text": "smoke\n"},
            },
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "display_data",
                "content": {"data": {"text/plain": "alpha"}, "transient": {"display_id": "disp-1"}},
            },
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "clear_output",
                "content": {"wait": True},
            },
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "update_display_data",
                "content": {"data": {"text/plain": "beta"}, "transient": {"display_id": "disp-1"}},
            },
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "execute_result",
                "content": {"data": {"text/plain": "42"}},
            },
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "status",
                "content": {"execution_state": "idle"},
            }
        ]
        self.stdin_messages: list[dict] = []

    def execute(self, code: str, store_history: Optional[bool] = None) -> str:
        self.executed.append((code, store_history))
        return "msg-1"

    def input(self, value: str) -> None:
        self.input_replies.append(value)

    def get_iopub_msg(self, timeout: int = 0) -> dict:
        _ = timeout
        return self.messages.pop(0)

    def get_stdin_msg(self, timeout: float = 0) -> dict:
        _ = timeout
        if not self.stdin_messages:
            raise Empty()
        return self.stdin_messages.pop(0)

    def stop_channels(self) -> None:
        self.channels_stopped = True

    def shutdown(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.shutdown_called = True


class SlowIdleClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.messages = [
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "stream",
                "content": {"name": "stdout", "text": "tick\n"},
            },
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "status",
                "content": {"execution_state": "idle"},
            },
        ]

    def get_iopub_msg(self, timeout: int = 0) -> dict:
        if len(self.messages) == 1:
            time.sleep(0.1)
        return super().get_iopub_msg(timeout=timeout)


class InterruptibleIdleClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.messages = [
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "stream",
                "content": {"name": "stdout", "text": "tick\n"},
            },
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "status",
                "content": {"execution_state": "idle"},
            },
        ]
        self.interrupted = False

    def get_iopub_msg(self, timeout: int = 0) -> dict:
        if len(self.messages) == 1 and not self.interrupted:
            time.sleep(0.2)
        return super().get_iopub_msg(timeout=timeout)


class InputRequestClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.messages = []
        self.stdin_messages = [
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "input_request",
                "content": {"prompt": "value: ", "password": False},
            }
        ]


class InputReplyFlowClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.messages = [
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "execute_input",
                "content": {"execution_count": 7, "code": "input('value: ')"},
            },
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "stream",
                "content": {"name": "stdout", "text": "typed: answer\n"},
            },
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "status",
                "content": {"execution_state": "idle"},
            },
        ]
        self.stdin_messages = [
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "input_request",
                "content": {"prompt": "value: ", "password": False},
            }
        ]


class CompletionClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.shell_messages = [
            {
                "parent_header": {"msg_id": "complete-1"},
                "msg_type": "complete_reply",
                "content": {
                    "matches": ["print", "property"],
                    "cursor_start": 0,
                    "cursor_end": 4,
                },
            }
        ]
        self.completion_requests: list[tuple[str, int]] = []

    def complete(self, code: str, cursor_pos: int) -> str:
        self.completion_requests.append((code, cursor_pos))
        return "complete-1"

    def get_shell_msg(self, timeout: float = 0) -> dict:
        _ = timeout
        return self.shell_messages.pop(0)


class MagicHandoffClient(FakeClient):
    def __init__(
        self,
        *,
        handler_id: str = "vd",
        magic_name: str = "vd",
        content: str = "pods",
        meta: Optional[dict] = None,
    ) -> None:
        super().__init__()
        metadata = {"source": "kernel"}
        if meta:
            metadata.update(meta)
        self.messages = [
            {
                "parent_header": {"msg_id": "msg-boot"},
                "msg_type": "status",
                "content": {"execution_state": "idle"},
            },
            {
                "parent_header": {"msg_id": "msg-magic"},
                "msg_type": "display_data",
                "content": {
                    "data": {
                        JUSI_HANDLER_HANDOFF_MIME: {
                            "handler_id": handler_id,
                            "magic_name": magic_name,
                            "content": content,
                        }
                    },
                    "metadata": {
                        JUSI_HANDLER_HANDOFF_MIME: metadata,
                    },
                },
            },
            {
                "parent_header": {"msg_id": "msg-magic"},
                "msg_type": "status",
                "content": {"execution_state": "idle"},
            },
        ]
        self._execute_ids = ["msg-boot", "msg-magic"]

    def execute(self, code: str, store_history: Optional[bool] = None) -> str:
        self.executed.append((code, store_history))
        return self._execute_ids.pop(0)


class TimedMessageClient(FakeClient):
    def __init__(self, messages: list[dict], delays: list[float]) -> None:
        super().__init__()
        self.messages = messages
        self._delays = delays

    def get_iopub_msg(self, timeout: int = 0) -> dict:
        index = len(self._delays) - len(self.messages)
        if 0 <= index < len(self._delays):
            time.sleep(self._delays[index])
        return super().get_iopub_msg(timeout=timeout)


class SerializedExecutionClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.messages = []
        self._execute_ids = ["msg-1", "msg-2"]
        self._release_first_idle = threading.Event()
        self._lock = threading.Lock()

    def execute(self, code: str, store_history: Optional[bool] = None) -> str:
        with self._lock:
            self.executed.append((code, store_history))
            msg_id = self._execute_ids.pop(0)
            self.messages.append(
                {
                    "parent_header": {"msg_id": msg_id},
                    "msg_type": "stream",
                    "content": {"name": "stdout", "text": f"{msg_id}\n"},
                }
            )
            if msg_id == "msg-1":
                return msg_id
            self.messages.append(
                {
                    "parent_header": {"msg_id": msg_id},
                    "msg_type": "status",
                    "content": {"execution_state": "idle"},
                }
            )
            return msg_id

    def get_iopub_msg(self, timeout: int = 0) -> dict:
        deadline = time.time() + float(timeout)
        while True:
            with self._lock:
                if self.messages:
                    return self.messages.pop(0)
                if self.executed and len(self.executed) == 1 and self._release_first_idle.is_set():
                    self.messages.append(
                        {
                            "parent_header": {"msg_id": "msg-1"},
                            "msg_type": "status",
                            "content": {"execution_state": "idle"},
                        }
                    )
            if timeout and time.time() >= deadline:
                raise Empty()
            time.sleep(0.005)


class LiveDisplayUpdateClient(TimedMessageClient):
    def __init__(self) -> None:
        super().__init__(
            messages=[
                {
                    "parent_header": {"msg_id": "msg-1"},
                    "msg_type": "display_data",
                    "content": {"data": {"text/plain": "alpha"}, "transient": {"display_id": "disp-1"}},
                },
                {
                    "parent_header": {"msg_id": "msg-1"},
                    "msg_type": "update_display_data",
                    "content": {"data": {"text/plain": "beta"}, "transient": {"display_id": "disp-1"}},
                },
                {
                    "parent_header": {"msg_id": "msg-1"},
                    "msg_type": "status",
                    "content": {"execution_state": "idle"},
                },
            ],
            delays=[0.0, 0.12, 0.15],
        )


class LiveClearOutputWaitClient(TimedMessageClient):
    def __init__(self) -> None:
        super().__init__(
            messages=[
                {
                    "parent_header": {"msg_id": "msg-1"},
                    "msg_type": "stream",
                    "content": {"name": "stdout", "text": "before\n"},
                },
                {
                    "parent_header": {"msg_id": "msg-1"},
                    "msg_type": "clear_output",
                    "content": {"wait": True},
                },
                {
                    "parent_header": {"msg_id": "msg-1"},
                    "msg_type": "execute_result",
                    "content": {"data": {"text/plain": "42"}},
                },
                {
                    "parent_header": {"msg_id": "msg-1"},
                    "msg_type": "status",
                    "content": {"execution_state": "idle"},
                },
            ],
            delays=[0.0, 0.0, 0.35, 0.15],
        )


class ErrorClient(TimedMessageClient):
    def __init__(self) -> None:
        super().__init__(
            messages=[
                {
                    "parent_header": {"msg_id": "msg-1"},
                    "msg_type": "error",
                    "content": {
                        "ename": "ValueError",
                        "evalue": "boom",
                        "traceback": [
                            "Traceback (most recent call last):",
                            '  File "<stdin>", line 1, in <module>',
                            "ValueError: boom",
                        ],
                    },
                },
                {
                    "parent_header": {"msg_id": "msg-1"},
                    "msg_type": "status",
                    "content": {"execution_state": "idle"},
                },
            ],
            delays=[0.05, 0.1],
        )


class FakeManager:
    def __init__(self) -> None:
        self.connection_file = "/tmp/kernel.json"
        self.interrupted = False
        self.shutdown = False
        self.cleaned = False

    def interrupt_kernel(self) -> None:
        self.interrupted = True

    def shutdown_kernel(self, now: bool = False) -> None:
        self.shutdown = now

    def cleanup_resources(self) -> None:
        self.cleaned = True


class ManagedRuntimeTest(unittest.TestCase):
    def test_jusi_kernel_env_injects_session_config_and_extension_modules(self) -> None:
        env = _jusi_kernel_env(
            ("jusi_sql.kernel",),
            {"sql": {"my_sqlite_db": {"provider": "sqlite", "path": "/tmp/demo.sqlite"}}},
        )

        self.assertEqual(
            '{"sql": {"my_sqlite_db": {"provider": "sqlite", "path": "/tmp/demo.sqlite"}}}',
            env[JUSI_SESSION_CONFIG_ENV],
        )
        self.assertEqual('["jusi_sql.kernel"]', env[JUSI_KERNEL_EXTENSIONS_ENV])

    def test_protocol_server_managed_handoff_starts_handler_worker(self) -> None:
        manager = FakeManager()
        client = FakeClient()
        client.messages = [
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "display_data",
                "content": {
                    "data": {
                        JUSI_HANDLER_HANDOFF_MIME: {
                            "handler_id": "vd",
                            "magic_name": "vd",
                            "content": "pods",
                        }
                    },
                    "metadata": {
                        JUSI_HANDLER_HANDOFF_MIME: {"source": "kernel"},
                    },
                },
            },
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "status",
                "content": {"execution_state": "idle"},
            },
        ]
        with patch("jusi.infrastructure.runtime._start_new_kernel", return_value=(manager, client)), patch(
            "jusi_vd.plugin.util.find_spec", return_value=SimpleNamespace(name="visidata")
        ):
            server, session_id, _ = self._start_bound_managed_server(client)
            execute_messages = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "execute_cell", '
                    '"request_id": "req-exec-handoff", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "main_lines": ["%%vd", "pods"]}}}'
                )
            )
            execute_envelopes = [parse_envelope(message) for message in execute_messages]
            deadline = time.time() + 1.0
            pending_envelopes: list = []
            while time.time() < deadline:
                pending_envelopes.extend(parse_envelope(message) for message in server.drain_pending_messages())
                cell_events = [
                    envelope
                    for envelope in execute_envelopes + pending_envelopes
                    if envelope.type == "cell_updated"
                ]
                if cell_events and cell_events[-1].payload["cell"]["owner"]["kind"] == "handler":
                    break
                time.sleep(0.02)
            cell_events = [
                envelope
                for envelope in execute_envelopes + pending_envelopes
                if envelope.type == "cell_updated"
            ]
            self.assertEqual("handler", cell_events[-1].payload["cell"]["owner"]["kind"])
            self.assertEqual("follow-up", cell_events[-1].payload["cell"]["status"])
            active_client_id = cell_events[0].payload["cell"]["client_id"]
            inspect = self._inspect_client_view(server, session_id, active_client_id, "req-inspect-handoff-worker")
            self.assertEqual("native_terminal", inspect["transport"]["kind"])
            self.assertTrue(any("handler.handoff> magic=vd handler=vd" == line for line in inspect["lines"]))
            server.close()

    def test_execute_cell_records_jusi_handler_handoff_from_display_data(self) -> None:
        runtime = ManagedKernelRuntime()
        self.addCleanup(runtime.close)
        fake_client = FakeClient()
        fake_client.messages = [
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "display_data",
                "content": {
                    "data": {
                        JUSI_HANDLER_HANDOFF_MIME: {
                            "handler_id": "todo",
                            "magic_name": "todo",
                            "content": "buy milk",
                        }
                    },
                    "metadata": {
                        JUSI_HANDLER_HANDOFF_MIME: {"cur_proj": "home"},
                    },
                },
            },
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "status",
                "content": {"execution_state": "idle"},
            },
        ]
        runtime._sessions["sess-1"] = SimpleNamespace(
            manager=None,
            client=fake_client,
            interrupted_client_ids=set(),
            pending_inputs={},
        )
        session = Session(notebook_id="nb-1", session_id="sess-1")
        runtime.prepare_client("nb-1", "sess-1")
        client_id = runtime.prepare_client("nb-1", "sess-1")
        runtime.bind_prepared_client(session, client_id, 91)
        execution = CellExecution(cell_id=12, client_id=client_id, client_bufnr=91)

        status = runtime.execute_cell(
            session,
            ExecutableCell(cell_id=12, kind="code", syntax="python", main_lines=["display('x')"]),
            execution,
        )

        self.assertEqual("follow-up", status)
        view = runtime.read_client_view(session, client_id)
        self.assertIn("handler.handoff> magic=todo handler=todo", view["lines"])
        self.assertIn('handler.meta> {"cur_proj": "home"}', view["lines"])

    def test_managed_magic_execution_registers_kernel_magic_and_records_handoff(self) -> None:
        runtime = ManagedKernelRuntime()
        self.addCleanup(runtime.close)
        fake_client = MagicHandoffClient()
        fake_client._execute_ids = ["msg-magic"]
        runtime._sessions["sess-1"] = SimpleNamespace(
            manager=None,
            client=fake_client,
            interrupted_client_ids=set(),
            pending_inputs={},
            handoffs={},
            jusi_magics_registered=True,
        )
        session = Session(notebook_id="nb-1", session_id="sess-1")
        client_id = runtime.prepare_client("nb-1", "sess-1")
        runtime.bind_prepared_client(session, client_id, 91)
        execution = CellExecution(cell_id=12, client_id=client_id, client_bufnr=91)

        status = runtime.execute_cell(
            session,
            ExecutableCell(cell_id=12, kind="magic", syntax="python", main_lines=["%%vd", "pods"]),
            execution,
        )

        self.assertEqual("follow-up", status)
        self.assertEqual(1, len(fake_client.executed))
        self.assertEqual(("%%vd\npods", False), fake_client.executed[0])
        view = runtime.read_client_view(session, client_id)
        self.assertIn("handler.handoff> magic=vd handler=vd", view["lines"])

    def _start_bound_managed_server(self, client: FakeClient) -> tuple[ProtocolServer, str, str]:
        manager = FakeManager()
        with patch("jusi.infrastructure.runtime._start_new_kernel", return_value=(manager, client)):
            server = ProtocolServer(runtime=ManagedKernelRuntime())
            self.addCleanup(server.close)
            start_messages = server.handle_message(
                '{"version": 1, "kind": "request", "type": "start_session", "request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}'
            )
            start_envelopes = [parse_envelope(message) for message in start_messages]
            session_id = start_envelopes[2].payload["session"]["id"]
            return server, session_id, ""

    def _inspect_client_view(self, server: ProtocolServer, session_id: str, client_id: str, request_id: str) -> dict:
        inspect_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "inspect_client", '
                '"request_id": "'
                + request_id
                + '", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "client_id": "'
                + client_id
                + '"}}'
            )
        )
        return [parse_envelope(message) for message in inspect_messages][0].payload["client"]

    def _wait_for_view_lines(
        self,
        server: ProtocolServer,
        session_id: str,
        client_id: str,
        request_id: str,
        expected_lines: list[str],
        timeout: float = 1.0,
    ) -> dict:
        deadline = time.time() + timeout
        last_view: dict = {}
        while time.time() < deadline:
            last_view = self._inspect_client_view(server, session_id, client_id, request_id)
            if last_view.get("lines") == expected_lines:
                return last_view
            time.sleep(0.02)
        self.assertEqual(expected_lines, last_view.get("lines"))
        return last_view

    def _wait_for_view_predicate(
        self,
        server: ProtocolServer,
        session_id: str,
        client_id: str,
        request_id: str,
        predicate,
        timeout: float = 1.0,
    ) -> dict:
        deadline = time.time() + timeout
        last_view: dict = {}
        while time.time() < deadline:
            last_view = self._inspect_client_view(server, session_id, client_id, request_id)
            if predicate(last_view):
                return last_view
            time.sleep(0.02)
        self.assertTrue(predicate(last_view), last_view)
        return last_view

    def test_build_runtime_uses_managed_mode(self) -> None:
        with patch.dict(os.environ, {"JUSI_RUNTIME": "managed"}, clear=False):
            runtime = build_runtime()
        self.assertIsInstance(runtime, ManagedKernelRuntime)

    def test_build_runtime_defaults_to_managed_mode(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            runtime = build_runtime()
        self.assertIsInstance(runtime, ManagedKernelRuntime)

    def test_protocol_server_managed_plain_python_completion_uses_kernel_complete_request(self) -> None:
        server, session_id, _ = self._start_bound_managed_server(CompletionClient())
        runtime = server._runtime
        session = server._store.get_by_notebook("nb-1")
        self.assertIsNotNone(session)
        client_id = runtime.prepare_client("nb-1", session_id)
        runtime.bind_prepared_client(session, client_id, 91)  # type: ignore[arg-type]

        response = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "handler_message", '
                '"request_id": "req-complete", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "client_id": "'
                + client_id
                + '", "handler_id": "python", "message_type": "complete", '
                '"payload": {"cell_text": "prin", "line_text": "prin", "cursor_row": 0, "cursor_col": 4}}}'
            )
        )
        self.assertTrue(parse_envelope(response[0]).ok)

        pending = [parse_envelope(message) for message in server.drain_pending_messages()]
        self.assertEqual("handler_message", pending[0].type)
        self.assertEqual("complete_result", pending[0].payload["message_type"])
        self.assertEqual(
            [
                {"value": "print", "label": "print", "kind": None, "detail": None, "documentation": None, "start_col": 0, "end_col": 4},
                {"value": "property", "label": "property", "kind": None, "detail": None, "documentation": None, "start_col": 0, "end_col": 4},
            ],
            pending[0].payload["payload"]["items"],
        )
        managed_client = runtime._sessions[session_id].client
        self.assertEqual([("prin", 4)], managed_client.completion_requests)

    def test_managed_start_target_uses_python3_for_venv_target_by_default(self) -> None:
        manager = FakeManager()
        client = FakeClient()
        with patch("jusi.infrastructure.runtime._start_new_kernel", return_value=(manager, client)) as start_kernel:
            runtime = ManagedKernelRuntime()
            runtime.start_target(
                SessionTarget(source="start", alias="jusi", kind="venv", value="venv:///tmp/venv"),
                "jusi",
            )
        start_kernel.assert_called_once()
        kwargs = start_kernel.call_args.kwargs
        self.assertEqual("python3", kwargs["kernel_name"])
        self.assertEqual(["--ext", "jusi.kernel"], kwargs["extra_arguments"])
        self.assertIn(JUSI_SESSION_CONFIG_ENV, kwargs["env"])
        self.assertIn(JUSI_KERNEL_EXTENSIONS_ENV, kwargs["env"])

    def test_managed_start_target_uses_target_config_kernel_name_for_docker_target(self) -> None:
        manager = FakeManager()
        client = FakeClient()
        with patch("jusi.infrastructure.runtime._start_new_kernel", return_value=(manager, client)) as start_kernel:
            runtime = ManagedKernelRuntime()
            runtime.start_target(
                SessionTarget(
                    source="start",
                    alias="dockerjusi",
                    kind="docker",
                    value="docker:///jolly_blackwell",
                    config={"kernel_name": "python3"},
                ),
                "dockerjusi",
            )
        start_kernel.assert_called_once()
        kwargs = start_kernel.call_args.kwargs
        self.assertEqual("python3", kwargs["kernel_name"])
        self.assertEqual(["--ext", "jusi.kernel"], kwargs["extra_arguments"])
        self.assertIn(JUSI_SESSION_CONFIG_ENV, kwargs["env"])
        self.assertIn(JUSI_KERNEL_EXTENSIONS_ENV, kwargs["env"])

    def test_managed_runtime_start_execute_interrupt_and_stop(self) -> None:
        manager = FakeManager()
        client = FakeClient()
        with patch("jusi.infrastructure.runtime._start_new_kernel", return_value=(manager, client)):
            runtime = ManagedKernelRuntime()
            session_id, connection = runtime.start_managed("python3")

        self.assertEqual("/tmp/kernel.json", connection)
        session = Session(notebook_id="nb-1", session_id=session_id, connection=connection, kernel_name="python3")
        prepared_client_id = runtime.prepare_client("nb-1", session_id)
        runtime.bind_prepared_client(session, prepared_client_id, 91)
        runtime.activate_client(session, prepared_client_id, 12)
        runtime_client = runtime.get_client(session_id, prepared_client_id)
        self.assertIsNotNone(runtime_client)
        self.assertIsInstance(runtime_client.handle, ManagedClientHandle)
        self.assertTrue(runtime_client.handle.is_running())
        self.assertEqual("active", runtime_client.state)
        self.assertEqual(12, runtime_client.cell_id)
        self.assertEqual(3, len(runtime_client.handle.lifecycle))
        self.assertTrue(runtime_client.handle.lifecycle[0].startswith("spawn:"))
        self.assertEqual("bind:91", runtime_client.handle.lifecycle[1])
        self.assertEqual("activate:12", runtime_client.handle.lifecycle[2])
        runtime.update_client_execution_status(session, prepared_client_id, "busy")
        execution = CellExecution(cell_id=12, status="busy", owner_kind="kernel", client_id=prepared_client_id)

        status = runtime.execute_cell(
            session,
            ExecutableCell(cell_id=12, kind="code", syntax="python", main_lines=["print(1)"]),
            execution,
        )
        self.assertEqual("done", status)
        self.assertEqual([("print(1)", None)], client.executed)
        self.assertEqual(12, len(runtime_client.handle.lifecycle))
        self.assertEqual("status:busy", runtime_client.handle.lifecycle[3])
        self.assertEqual("event:execution_started", runtime_client.handle.lifecycle[4])
        self.assertEqual("event:stream", runtime_client.handle.lifecycle[5])
        self.assertEqual("event:display_data", runtime_client.handle.lifecycle[6])
        self.assertEqual("event:clear_output", runtime_client.handle.lifecycle[7])
        self.assertEqual("event:update_display_data", runtime_client.handle.lifecycle[8])
        self.assertEqual("event:execute_result", runtime_client.handle.lifecycle[9])
        self.assertEqual("status:done", runtime_client.handle.lifecycle[10])
        self.assertEqual("event:execution_finished", runtime_client.handle.lifecycle[11])
        self.assertEqual(
            {
                "client_id": prepared_client_id,
                "notebook_id": "nb-1",
                "session_id": session_id,
                "client_bufnr": 91,
                "active_cell_id": 12,
                "execution_status": "done",
                "view_revision": 11,
                "lifecycle": [
                    "ready",
                    "bind:91",
                    "activate:12",
                    "status:busy",
                    "event:execution_started",
                    "event:stream",
                    "event:display_data",
                    "event:clear_output",
                    "event:update_display_data",
                    "event:execute_result",
                    "status:done",
                    "event:execution_finished",
                ],
                "transcript": [
                    {"type": "execution_started", "cell_id": 12, "kind": "code", "syntax": "python"},
                    {"type": "stream", "name": "stdout", "text": "smoke\n"},
                    {"type": "display_data", "data": {"text/plain": "alpha"}, "display_id": "disp-1"},
                    {"type": "clear_output", "wait": True},
                    {"type": "update_display_data", "data": {"text/plain": "beta"}, "display_id": "disp-1"},
                    {"type": "execute_result", "data": {"text/plain": "42"}},
                    {"type": "execution_finished", "status": "done"},
                ],
                "view_title": "cell 12: done",
                "view_lines": [
                    f"meta> client={prepared_client_id} session={session_id} bufnr=91",
                    "display> beta",
                    "result> 42",
                    "finished: done",
                ],
                "shutdown_reason": "",
            },
            runtime_client.handle.read_status(),
        )
        self.assertEqual(
            {
                "title": "cell 12: done",
                "lines": [
                    f"meta> client={prepared_client_id} session={session_id} bufnr=91",
                    "display> beta",
                    "result> 42",
                    "finished: done",
                ],
                "execution_status": "done",
                "active_cell_id": 12,
                "revision": 11,
            },
            runtime.read_client_view(session, prepared_client_id),
        )

        interrupt_status = runtime.interrupt_kernel(session, execution)
        self.assertEqual("interrupted", interrupt_status)
        self.assertTrue(manager.interrupted)
        self.assertEqual("interrupted", runtime_client.handle.read_status()["execution_status"])

        runtime.stop_session(session)
        self.assertEqual([], runtime.list_clients(session_id))
        self.assertFalse(runtime_client.handle.is_running())
        self.assertTrue(client.channels_stopped)
        self.assertTrue(manager.shutdown)
        self.assertTrue(manager.cleaned)

    def test_managed_runtime_attach_connection_file_prepares_client_and_detaches_without_kernel_shutdown(self) -> None:
        client = FakeClient()
        with patch("jusi.infrastructure.runtime._attach_existing_kernel", return_value=client):
            runtime = ManagedKernelRuntime()
            session_id, connection = runtime.attach_target(
                SessionTarget(source="attach", kind="connection_file", value="/tmp/external-kernel.json")
            )

        self.assertEqual("/tmp/external-kernel.json", connection)
        session = Session(notebook_id="nb-1", session_id=session_id, connection=connection)
        prepared_client_id = runtime.prepare_client("nb-1", session_id)
        runtime.bind_prepared_client(session, prepared_client_id, 91)
        runtime.activate_client(session, prepared_client_id, 12)
        runtime_client = runtime.get_client(session_id, prepared_client_id)
        self.assertIsNotNone(runtime_client)
        self.assertTrue(runtime_client.handle.is_running())

        runtime.stop_session(session)
        self.assertEqual([], runtime.list_clients(session_id))
        self.assertFalse(runtime_client.handle.is_running())
        self.assertTrue(client.channels_stopped)
        self.assertTrue(client.shutdown_called)

    def test_managed_runtime_attach_stop_signals_peer_supervisors(self) -> None:
        client = FakeClient()
        peer_signals: list[tuple[int, int]] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            connection_file = os.path.join(tmpdir, "external-kernel.json")
            with patch("jusi.infrastructure.runtime._attach_existing_kernel", return_value=client):
                with patch("jusi.infrastructure.runtime.os.getpid", return_value=1001):
                    with patch("jusi.infrastructure.runtime._is_live_pid", side_effect=lambda pid: pid in {1001, 1002}):
                        runtime = ManagedKernelRuntime()
                        session_id, connection = runtime.attach_target(
                            SessionTarget(source="attach", kind="connection_file", value=connection_file)
                        )
                        with patch("jusi.infrastructure.runtime.os.kill", side_effect=lambda pid, sig: peer_signals.append((pid, sig))):
                            registry_path = f"{connection}.jusi-attached.json"
                            with open(registry_path, "w", encoding="utf-8") as handle:
                                json.dump([1001, 1002], handle)
                            session = Session(notebook_id="nb-1", session_id=session_id, connection=connection)
                            runtime.stop_session(session)
                            self.assertIn((1002, signal.SIGTERM), peer_signals)

    def test_protocol_server_managed_attach_session_uses_connection_file_target(self) -> None:
        client = FakeClient()
        with patch("jusi.infrastructure.runtime._attach_existing_kernel", return_value=client):
            server = ProtocolServer(runtime=ManagedKernelRuntime())
            attach_messages = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "attach_session", '
                    '"request_id": "req-1", "payload": {"notebook_id": "nb-1", '
                    '"target": {"source": "attach", "kind": "connection_file", "value": "/tmp/external-kernel.json"}}}'
                )
            )
            attach_envelopes = [parse_envelope(message) for message in attach_messages]

            self.assertTrue(attach_envelopes[0].ok)
            self.assertEqual("connected", attach_envelopes[2].payload["session"]["state"])
            session_id = attach_envelopes[2].payload["session"]["id"]

            stop_messages = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "stop_session", '
                    '"request_id": "req-stop", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '"}}'
                )
            )
            stop_envelopes = [parse_envelope(message) for message in stop_messages]
            self.assertTrue(stop_envelopes[0].ok)
            deadline = time.time() + 1.0
            pending: list = []
            while time.time() < deadline and not pending:
                pending = [parse_envelope(message) for message in server.drain_pending_messages()]
                if not pending:
                    time.sleep(0.02)
            self.assertEqual("stopped", pending[0].payload["session"]["state"])
            self.assertTrue(client.channels_stopped)

    def test_managed_attached_session_obeys_shared_timeout_deadline(self) -> None:
        client = FakeClient()
        with tempfile.TemporaryDirectory() as tmpdir:
            connection_file = os.path.join(tmpdir, "external-kernel.json")
            with patch("jusi.infrastructure.runtime._attach_existing_kernel", return_value=client):
                server = ProtocolServer(runtime=ManagedKernelRuntime())
                try:
                    attach_messages = server.handle_message(
                        (
                            '{"version": 1, "kind": "request", "type": "attach_session", '
                            '"request_id": "req-1", "payload": {"notebook_id": "nb-1", '
                            '"target": {"source": "attach", "kind": "connection_file", "value": "'
                            + connection_file
                            + '"}}}'
                        )
                    )
                    session_id = [parse_envelope(message) for message in attach_messages][2].payload["session"]["id"]
                    registry_path = f"{connection_file}.jusi-attached.json"
                    with open(registry_path, "w", encoding="utf-8") as handle:
                        json.dump({"pids": [os.getpid()], "expires_at": 0}, handle)

                    should_exit = server.poll_session_timeouts()

                    self.assertTrue(should_exit)
                    self.assertEqual([], server._runtime.list_clients(session_id))
                    self.assertTrue(client.channels_stopped)
                    self.assertTrue(client.shutdown_called)
                finally:
                    server.close()

    def test_shared_view_applies_clear_output_immediately_or_on_waited_next_output(self) -> None:
        session = Session(notebook_id="nb-1", session_id="session-1", connection="", kernel_name="python3")
        runtime = InMemoryKernelRuntime()
        client_id = runtime.prepare_client("nb-1", session.session_id)
        runtime.bind_prepared_client(session, client_id, 91)
        runtime.activate_client(session, client_id, 12)
        runtime.update_client_execution_status(session, client_id, "busy")

        runtime.append_client_execution_event(
            session,
            client_id,
            {"type": "execution_started", "cell_id": 12, "kind": "code", "syntax": "python"},
        )
        runtime.append_client_execution_event(
            session,
            client_id,
            {"type": "stream", "name": "stdout", "text": "before\n"},
        )
        runtime.append_client_execution_event(
            session,
            client_id,
            {"type": "clear_output", "wait": False},
        )
        self.assertEqual(
            {
                "title": "cell 12: busy",
                "lines": [
                    f"meta> client={client_id} session={session.session_id} bufnr=91",
                    "status> busy",
                ],
                "execution_status": "busy",
                "active_cell_id": 12,
                "revision": 6,
            },
            runtime.read_client_view(session, client_id),
        )

        runtime.append_client_execution_event(
            session,
            client_id,
            {"type": "stream", "name": "stdout", "text": "after\n"},
        )
        self.assertEqual(
            {
                "title": "cell 12: busy",
                "lines": [
                    f"meta> client={client_id} session={session.session_id} bufnr=91",
                    "stdout> after",
                ],
                "execution_status": "busy",
                "active_cell_id": 12,
                "revision": 7,
            },
            runtime.read_client_view(session, client_id),
        )

        runtime.append_client_execution_event(
            session,
            client_id,
            {"type": "display_data", "data": {"text/plain": "alpha"}, "display_id": "disp-1"},
        )
        runtime.append_client_execution_event(
            session,
            client_id,
            {"type": "clear_output", "wait": True},
        )
        self.assertEqual(
            {
                "title": "cell 12: busy",
                "lines": [
                    f"meta> client={client_id} session={session.session_id} bufnr=91",
                    "stdout> after",
                    "display> alpha",
                ],
                "execution_status": "busy",
                "active_cell_id": 12,
                "revision": 9,
            },
            runtime.read_client_view(session, client_id),
        )

        runtime.append_client_execution_event(
            session,
            client_id,
            {"type": "execute_result", "data": {"text/plain": "42"}},
        )
        self.assertEqual(
            {
                "title": "cell 12: busy",
                "lines": [
                    f"meta> client={client_id} session={session.session_id} bufnr=91",
                    "result> 42",
                ],
                "execution_status": "busy",
                "active_cell_id": 12,
                "revision": 10,
            },
            runtime.read_client_view(session, client_id),
        )

    def test_shared_view_preserves_full_traceback_lines(self) -> None:
        session = Session(notebook_id="nb-1", session_id="session-1", connection="", kernel_name="python3")
        runtime = InMemoryKernelRuntime()
        client_id = runtime.prepare_client("nb-1", session.session_id)
        runtime.bind_prepared_client(session, client_id, 91)
        runtime.activate_client(session, client_id, 12)
        runtime.update_client_execution_status(session, client_id, "error")
        runtime.append_client_execution_event(
            session,
            client_id,
            {"type": "execution_started", "cell_id": 12, "kind": "code", "syntax": "python"},
        )
        runtime.append_client_execution_event(
            session,
            client_id,
            {
                "type": "error",
                "ename": "ValueError",
                "evalue": "boom",
                "traceback": [
                    "Traceback (most recent call last):",
                    '  File "<stdin>", line 1, in <module>',
                    "ValueError: boom",
                ],
            },
        )
        runtime.append_client_execution_event(
            session,
            client_id,
            {"type": "execution_finished", "status": "error"},
        )

        self.assertEqual(
            {
                "title": "cell 12: error",
                "lines": [
                    f"meta> client={client_id} session={session.session_id} bufnr=91",
                    "started cell 12 [code:python]",
                    "error: ValueError: boom",
                    "trace> Traceback (most recent call last):",
                    'trace> File "<stdin>", line 1, in <module>',
                    "trace> ValueError: boom",
                    "finished: error",
                ],
                "execution_status": "error",
                "active_cell_id": 12,
                "revision": 6,
            },
            runtime.read_client_view(session, client_id),
        )

    def test_managed_runtime_shutdown_client_drives_handle_lifecycle(self) -> None:
        manager = FakeManager()
        client = FakeClient()
        with patch("jusi.infrastructure.runtime._start_new_kernel", return_value=(manager, client)):
            runtime = ManagedKernelRuntime()
            session_id, connection = runtime.start_managed("python3")

        session = Session(notebook_id="nb-1", session_id=session_id, connection=connection, kernel_name="python3")
        prepared_client_id = runtime.prepare_client("nb-1", session_id)
        runtime.bind_prepared_client(session, prepared_client_id, 91)
        runtime_client = runtime.get_client(session_id, prepared_client_id)
        self.assertIsNotNone(runtime_client)
        self.assertTrue(runtime_client.handle.is_running())

        handle = runtime_client.handle
        runtime.shutdown_client(session, prepared_client_id, "user_close")

        self.assertEqual(3, len(handle.lifecycle))
        self.assertTrue(handle.lifecycle[0].startswith("spawn:"))
        self.assertEqual("bind:91", handle.lifecycle[1])
        self.assertEqual("shutdown:user_close", handle.lifecycle[2])
        self.assertFalse(handle.is_running())
        self.assertEqual("user_close", handle.read_status()["shutdown_reason"])
        self.assertIsNone(runtime.get_client(session_id, prepared_client_id))

    def test_protocol_server_shutdown_busy_client_interrupts_kernel_first(self) -> None:
        manager = FakeManager()
        client = FakeClient()
        with patch("jusi.infrastructure.runtime._start_new_kernel", return_value=(manager, client)):
            server = ProtocolServer(runtime=ManagedKernelRuntime())
            self.addCleanup(server.close)
            start_messages = server.handle_message(
                '{"version": 1, "kind": "request", "type": "start_session", "request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}'
            )
            start_envelopes = [parse_envelope(message) for message in start_messages]
            session_id = start_envelopes[2].payload["session"]["id"]

            execute_messages = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "execute_cell", '
                    '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "keep_running": true, "main_lines": ["while True: pass"]}}}'
                )
            )
            execute_envelopes = [parse_envelope(message) for message in execute_messages]
            cell_events = [env for env in execute_envelopes if env.type == "cell_updated"]
            active_client_id = cell_events[-1].payload["cell"]["client_id"]

            shutdown_messages = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "shutdown_client", '
                    '"request_id": "req-shutdown", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '", "cell_id": 12, "client_id": "'
                    + active_client_id
                    + '", "reason": "user_close"}}'
                )
            )
            shutdown_envelopes = [parse_envelope(message) for message in shutdown_messages]
            self.assertTrue(shutdown_envelopes[0].ok)
            self.assertTrue(manager.interrupted)

    def test_managed_plain_code_native_terminal_transport_uses_transcript_status_file_only(self) -> None:
        manager = FakeManager()
        client = FakeClient()
        with patch("jusi.infrastructure.runtime._start_new_kernel", return_value=(manager, client)):
            server = ProtocolServer(runtime=ManagedKernelRuntime())
            self.addCleanup(server.close)
            start_messages = server.handle_message(
                '{"version": 1, "kind": "request", "type": "start_session", "request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}'
            )
            start_envelopes = [parse_envelope(message) for message in start_messages]
            session_id = start_envelopes[2].payload["session"]["id"]

            execute_messages = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "execute_cell", '
                    '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "keep_running": true, "main_lines": ["while True: pass"]}}}'
                )
            )
            execute_envelopes = [parse_envelope(message) for message in execute_messages]
            cell_events = [env for env in execute_envelopes if env.type == "cell_updated"]
            active_client_id = cell_events[-1].payload["cell"]["client_id"]

            inspect = self._inspect_client_view(server, session_id, active_client_id, "req-inspect-plain-transport")
            transport_env = inspect["transport"]["attach_env"]
            self.assertEqual("transcript", transport_env["JUSI_TERMINAL_MODE"])
            self.assertIn("JUSI_CLIENT_STATUS_FILE", transport_env)
            self.assertNotIn("JUSI_PLUGIN_RUNTIME_CONTROL_SOCKET", transport_env)
            self.assertNotIn("JUSI_PLUGIN_RUNTIME_PID_FILE", transport_env)
            self.assertNotIn("JUSI_PLUGIN_FRONTEND_ACTIONS_FILE", transport_env)

    def test_managed_runtime_surfaces_input_request_and_leaves_execution_busy(self) -> None:
        manager = FakeManager()
        client = FakeClient()
        client.stdin_messages = [
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "input_request",
                "content": {"prompt": "value: ", "password": False},
            }
        ]
        client.messages = []
        with patch("jusi.infrastructure.runtime._start_new_kernel", return_value=(manager, client)):
            runtime = ManagedKernelRuntime()
            session_id, connection = runtime.start_managed("python3")

        session = Session(notebook_id="nb-1", session_id=session_id, connection=connection, kernel_name="python3")
        prepared_client_id = runtime.prepare_client("nb-1", session_id)
        runtime.bind_prepared_client(session, prepared_client_id, 91)
        runtime.activate_client(session, prepared_client_id, 12)
        runtime.update_client_execution_status(session, prepared_client_id, "busy")
        execution = CellExecution(cell_id=12, status="busy", owner_kind="kernel", client_id=prepared_client_id)

        status = runtime.execute_cell(
            session,
            ExecutableCell(cell_id=12, kind="code", syntax="python", main_lines=["input('value: ')"]),
            execution,
        )

        self.assertEqual("busy", status)
        runtime_client = runtime.get_client(session_id, prepared_client_id)
        self.assertIsNotNone(runtime_client)
        self.assertEqual(
            {
                "title": "cell 12: busy",
                "lines": [
                    f"meta> client={prepared_client_id} session={session_id} bufnr=91",
                    "started cell 12 [code:python]",
                    "input> value: ",
                ],
                "execution_status": "busy",
                "active_cell_id": 12,
                "revision": 5,
            },
            runtime.read_client_view(session, prepared_client_id),
        )
        runtime.stop_session(session)

    def test_managed_runtime_input_reply_resumes_pending_execution_and_records_execute_input(self) -> None:
        manager = FakeManager()
        client = InputReplyFlowClient()
        with patch("jusi.infrastructure.runtime._start_new_kernel", return_value=(manager, client)):
            runtime = ManagedKernelRuntime()
            session_id, connection = runtime.start_managed("python3")

        session = Session(notebook_id="nb-1", session_id=session_id, connection=connection, kernel_name="python3")
        prepared_client_id = runtime.prepare_client("nb-1", session_id)
        runtime.bind_prepared_client(session, prepared_client_id, 91)
        runtime.activate_client(session, prepared_client_id, 12)
        runtime.update_client_execution_status(session, prepared_client_id, "busy")
        execution = CellExecution(cell_id=12, status="busy", owner_kind="kernel", client_id=prepared_client_id)

        initial_status = runtime.execute_cell(
            session,
            ExecutableCell(cell_id=12, kind="code", syntax="python", main_lines=["input('value: ')"]),
            execution,
        )
        self.assertEqual("busy", initial_status)

        resumed_status = runtime.reply_input(session, execution, "answer")
        self.assertEqual("done", resumed_status)
        self.assertEqual(["answer"], client.input_replies)
        self.assertEqual(
            {
                "title": "cell 12: done",
                "lines": [
                    f"meta> client={prepared_client_id} session={session_id} bufnr=91",
                    "started cell 12 [code:python]",
                    "input> value: ",
                    "execute[7]> input('value: ')",
                    "stdout> typed: answer",
                    "finished: done",
                ],
                "execution_status": "done",
                "active_cell_id": 12,
                "revision": 9,
            },
            runtime.read_client_view(session, prepared_client_id),
        )
        runtime_client = runtime.get_client(session_id, prepared_client_id)
        self.assertIsNotNone(runtime_client)
        self.assertEqual(
            [
                {"type": "execution_started", "cell_id": 12, "kind": "code", "syntax": "python"},
                {"type": "input_request", "prompt": "value: ", "password": False},
                {"type": "execute_input", "execution_count": 7, "code": "input('value: ')"},
                {"type": "stream", "name": "stdout", "text": "typed: answer\n"},
                {"type": "execution_finished", "status": "done"},
            ],
            runtime_client.handle.read_status()["transcript"],
        )
        runtime.stop_session(session)

    def test_managed_runtime_preserves_comm_messages_as_debug_transcript(self) -> None:
        manager = FakeManager()
        client = FakeClient()
        client.messages = [
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "comm_open",
                "content": {"comm_id": "comm-1", "target_name": "jupyter.widget", "data": {"state": {"value": 1}}},
            },
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "comm_msg",
                "content": {"comm_id": "comm-1", "data": {"method": "update", "state": {"value": 2}}},
            },
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "comm_close",
                "content": {"comm_id": "comm-1", "data": {"reason": "done"}},
            },
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "status",
                "content": {"execution_state": "idle"},
            },
        ]
        with patch("jusi.infrastructure.runtime._start_new_kernel", return_value=(manager, client)):
            runtime = ManagedKernelRuntime()
            session_id, connection = runtime.start_managed("python3")

        session = Session(notebook_id="nb-1", session_id=session_id, connection=connection, kernel_name="python3")
        prepared_client_id = runtime.prepare_client("nb-1", session_id)
        runtime.bind_prepared_client(session, prepared_client_id, 91)
        runtime.activate_client(session, prepared_client_id, 12)
        runtime.update_client_execution_status(session, prepared_client_id, "busy")
        execution = CellExecution(cell_id=12, status="busy", owner_kind="kernel", client_id=prepared_client_id)

        status = runtime.execute_cell(
            session,
            ExecutableCell(cell_id=12, kind="code", syntax="python", main_lines=["widget()"]),
            execution,
        )

        self.assertEqual("done", status)
        runtime_client = runtime.get_client(session_id, prepared_client_id)
        self.assertIsNotNone(runtime_client)
        self.assertEqual(
            [
                {"type": "execution_started", "cell_id": 12, "kind": "code", "syntax": "python"},
                {
                    "type": "comm_open",
                    "comm_id": "comm-1",
                    "target_name": "jupyter.widget",
                    "data": {"state": {"value": 1}},
                },
                {
                    "type": "comm_msg",
                    "comm_id": "comm-1",
                    "data": {"method": "update", "state": {"value": 2}},
                },
                {
                    "type": "comm_close",
                    "comm_id": "comm-1",
                    "data": {"reason": "done"},
                },
                {"type": "execution_finished", "status": "done"},
            ],
            runtime_client.handle.read_status()["transcript"],
        )
        self.assertEqual(
            {
                "title": "cell 12: done",
                "lines": [
                    f"meta> client={prepared_client_id} session={session_id} bufnr=91",
                    "started cell 12 [code:python]",
                    "comm_open comm_id=comm-1 target=jupyter.widget",
                    'comm.data> {"state": {"value": 1}}',
                    "comm_msg comm_id=comm-1",
                    'comm.data> {"method": "update", "state": {"value": 2}}',
                    "comm_close comm_id=comm-1",
                    'comm.data> {"reason": "done"}',
                    "finished: done",
                ],
                "execution_status": "done",
                "active_cell_id": 12,
                "revision": 9,
            },
            runtime.read_client_view(session, prepared_client_id),
        )
        runtime.stop_session(session)

    def test_protocol_server_managed_execute_completes_via_pending_event_queue(self) -> None:
        manager = FakeManager()
        client = SlowIdleClient()
        with patch("jusi.infrastructure.runtime._start_new_kernel", return_value=(manager, client)):
            server = ProtocolServer(runtime=ManagedKernelRuntime())
            start_messages = server.handle_message(
                '{"version": 1, "kind": "request", "type": "start_session", "request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}'
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
            self.assertEqual(["response", "event", "event"], [envelope.kind for envelope in execute_envelopes])
            active_client_id = execute_envelopes[2].payload["cell"]["client_id"]
            self.assertEqual("busy", execute_envelopes[2].payload["cell"]["status"])
            self.assertNotIn("client_bufnr", execute_envelopes[2].payload["cell"])

            delayed_messages: list[str] = []
            deadline = time.time() + 1.0
            while time.time() < deadline and not delayed_messages:
                delayed_messages = server.drain_pending_messages()
                if not delayed_messages:
                    time.sleep(0.02)
            self.assertTrue(delayed_messages)
            delayed_envelopes = [parse_envelope(message) for message in delayed_messages]
            self.assertEqual(1, len(delayed_envelopes))
            self.assertEqual("cell_updated", delayed_envelopes[0].type)
            self.assertEqual("done", delayed_envelopes[0].payload["cell"]["status"])
            self.assertEqual(active_client_id, delayed_envelopes[0].payload["cell"]["client_id"])
            self.assertNotIn("client_bufnr", delayed_envelopes[0].payload["cell"])

            stop_messages = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "stop_session", '
                    '"request_id": "req-stop", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '"}}'
                )
            )
            stop_envelopes = [parse_envelope(message) for message in stop_messages]
            self.assertTrue(stop_envelopes[0].ok)
            self.assertEqual("session_updated", stop_envelopes[1].type)
            self.assertEqual("stopping", stop_envelopes[1].payload["session"]["state"])

            stop_pending: list[str] = []
            deadline = time.time() + 1.0
            while time.time() < deadline and not stop_pending:
                stop_pending = server.drain_pending_messages()
                if not stop_pending:
                    time.sleep(0.02)
            self.assertTrue(stop_pending)
            stop_pending_envelopes = [parse_envelope(message) for message in stop_pending]
            self.assertEqual("session_updated", stop_pending_envelopes[0].type)
            self.assertEqual("stopped", stop_pending_envelopes[0].payload["session"]["state"])

    def test_protocol_server_managed_serializes_execute_requests_per_session(self) -> None:
        manager = FakeManager()
        client = SerializedExecutionClient()
        with patch("jusi.infrastructure.runtime._start_new_kernel", return_value=(manager, client)):
            server = ProtocolServer(runtime=ManagedKernelRuntime())
            self.addCleanup(server.close)
            start_messages = server.handle_message(
                '{"version": 1, "kind": "request", "type": "start_session", "request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}'
            )
            start_envelopes = [parse_envelope(message) for message in start_messages]
            session_id = start_envelopes[2].payload["session"]["id"]

            first_execute = server.handle_message(
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "main_lines": ["print(1)"]}}}'
            )
            self.assertTrue([parse_envelope(message) for message in first_execute][0].ok)

            second_execute = server.handle_message(
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-3", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 13, "kind": "code", "syntax": "python", "main_lines": ["print(2)"]}}}'
            )
            self.assertTrue([parse_envelope(message) for message in second_execute][0].ok)

            time.sleep(0.05)
            self.assertEqual(1, len(client.executed))

            client._release_first_idle.set()
            deadline = time.time() + 1.0
            while time.time() < deadline and len(client.executed) < 2:
                server.drain_pending_messages()
                time.sleep(0.02)

            self.assertEqual(2, len(client.executed))

    def test_protocol_server_managed_interrupt_does_not_finish_as_done(self) -> None:
        manager = FakeManager()
        client = InterruptibleIdleClient()

        def interrupt_and_mark() -> None:
            manager.interrupted = True
            client.interrupted = True

        manager.interrupt_kernel = interrupt_and_mark  # type: ignore[method-assign]

        with patch("jusi.infrastructure.runtime._start_new_kernel", return_value=(manager, client)):
            server = ProtocolServer(runtime=ManagedKernelRuntime())
            start_messages = server.handle_message(
                '{"version": 1, "kind": "request", "type": "start_session", "request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}'
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
            self.assertEqual("busy", execute_envelopes[2].payload["cell"]["status"])

            interrupt_messages = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "interrupt_cell", '
                    '"request_id": "req-interrupt", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '", "cell_id": 12}}'
                )
            )
            interrupt_envelopes = [parse_envelope(message) for message in interrupt_messages]
            self.assertTrue(interrupt_envelopes[0].ok)
            self.assertEqual("interrupted", interrupt_envelopes[2].payload["cell"]["status"])

            delayed_messages: list[str] = []
            deadline = time.time() + 1.0
            while time.time() < deadline and not delayed_messages:
                delayed_messages = server.drain_pending_messages()
                if not delayed_messages:
                    time.sleep(0.02)
            self.assertTrue(delayed_messages)
            delayed_envelopes = [parse_envelope(message) for message in delayed_messages]
            self.assertEqual("cell_updated", delayed_envelopes[0].type)
            self.assertEqual("interrupted", delayed_envelopes[0].payload["cell"]["status"])

            stop_messages = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "stop_session", '
                    '"request_id": "req-stop", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '"}}'
                )
            )
            stop_envelopes = [parse_envelope(message) for message in stop_messages]
            self.assertTrue(stop_envelopes[0].ok)

    def test_protocol_server_managed_stop_responds_promptly_after_client_shutdowns(self) -> None:
        with patch("jusi.infrastructure.runtime._start_new_kernel", return_value=(FakeManager(), SlowIdleClient())):
            server = ProtocolServer(runtime=ManagedKernelRuntime())
            start_messages = server.handle_message(
                '{"version": 1, "kind": "request", "type": "start_session", "request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}'
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
            active_client_id = execute_envelopes[2].payload["cell"]["client_id"]

            delayed_messages: list[str] = []
            deadline = time.time() + 1.0
            while time.time() < deadline and not delayed_messages:
                delayed_messages = server.drain_pending_messages()
                if not delayed_messages:
                    time.sleep(0.02)
            self.assertTrue(delayed_messages)

            active_shutdown = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "shutdown_client", '
                    '"request_id": "req-shutdown-active", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '", "cell_id": 12, "client_id": "'
                    + active_client_id
                    + '", "reason": "session_stop"}}'
                )
            )
            self.assertTrue([parse_envelope(message) for message in active_shutdown][0].ok)

            stop_messages = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "stop_session", '
                    '"request_id": "req-stop", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '"}}'
                )
            )
            stop_envelopes = [parse_envelope(message) for message in stop_messages]
            self.assertTrue(stop_envelopes[0].ok)
            self.assertEqual("session_updated", stop_envelopes[1].type)
            self.assertEqual("stopping", stop_envelopes[1].payload["session"]["state"])

            stop_pending: list[str] = []
            deadline = time.time() + 1.0
            while time.time() < deadline and not stop_pending:
                stop_pending = server.drain_pending_messages()
                if not stop_pending:
                    time.sleep(0.02)
            self.assertTrue(stop_pending)
            stop_pending_envelopes = [parse_envelope(message) for message in stop_pending]
            self.assertEqual("session_updated", stop_pending_envelopes[0].type)
            self.assertEqual("stopped", stop_pending_envelopes[0].payload["session"]["state"])

    def test_protocol_server_managed_input_request_stays_busy_and_updates_view(self) -> None:
        manager = FakeManager()
        client = InputRequestClient()
        with patch("jusi.infrastructure.runtime._start_new_kernel", return_value=(manager, client)):
            server = ProtocolServer(runtime=ManagedKernelRuntime())
            start_messages = server.handle_message(
                '{"version": 1, "kind": "request", "type": "start_session", "request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}'
            )
            start_envelopes = [parse_envelope(message) for message in start_messages]
            session_id = start_envelopes[2].payload["session"]["id"]

            execute_messages = server.handle_message(
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "main_lines": ["input(\\"value: \\")"]}}}'
            )
            execute_envelopes = [parse_envelope(message) for message in execute_messages]
            active_client_id = execute_envelopes[2].payload["cell"]["client_id"]
            self.assertEqual("busy", execute_envelopes[2].payload["cell"]["status"])

            delayed_messages: list[str] = []
            deadline = time.time() + 1.0
            while time.time() < deadline and not delayed_messages:
                delayed_messages = server.drain_pending_messages()
                if not delayed_messages:
                    time.sleep(0.02)
            self.assertTrue(delayed_messages)
            delayed_envelopes = [parse_envelope(message) for message in delayed_messages]
            self.assertEqual("cell_updated", delayed_envelopes[0].type)
            self.assertEqual("busy", delayed_envelopes[0].payload["cell"]["status"])

            first_inspect = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "inspect_client", '
                    '"request_id": "req-inspect-1", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '", "client_id": "'
                    + active_client_id
                    + '"}}'
                )
            )
            first_view = [parse_envelope(message) for message in first_inspect][0].payload["client"]
            self.assertEqual("cell 12: busy", first_view["title"])
            self.assertEqual(
                [
                    f"meta> client={active_client_id} session={session_id} bufnr=unbound",
                    "started cell 12 [code:python]",
                    "input> value: ",
                ],
                first_view["lines"],
            )
            self.assertGreater(first_view["revision"], 0)

            second_inspect = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "inspect_client", '
                    '"request_id": "req-inspect-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '", "client_id": "'
                    + active_client_id
                    + '"}}'
                )
            )
            second_view = [parse_envelope(message) for message in second_inspect][0].payload["client"]
            self.assertEqual(first_view["revision"], second_view["revision"])

            stop_messages = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "stop_session", '
                    '"request_id": "req-stop", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '"}}'
                )
            )
            stop_envelopes = [parse_envelope(message) for message in stop_messages]
            self.assertTrue(stop_envelopes[0].ok)

    def test_protocol_server_managed_input_reply_resumes_execution(self) -> None:
        manager = FakeManager()
        client = InputReplyFlowClient()
        with patch("jusi.infrastructure.runtime._start_new_kernel", return_value=(manager, client)):
            server = ProtocolServer(runtime=ManagedKernelRuntime())
            start_messages = server.handle_message(
                '{"version": 1, "kind": "request", "type": "start_session", "request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}'
            )
            start_envelopes = [parse_envelope(message) for message in start_messages]
            session_id = start_envelopes[2].payload["session"]["id"]

            execute_messages = server.handle_message(
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "main_lines": ["input(\\"value: \\")"]}}}'
            )
            execute_envelopes = [parse_envelope(message) for message in execute_messages]
            active_client_id = execute_envelopes[2].payload["cell"]["client_id"]
            self.assertEqual("busy", execute_envelopes[2].payload["cell"]["status"])

            first_pending: list[str] = []
            deadline = time.time() + 1.0
            while time.time() < deadline and not first_pending:
                first_pending = server.drain_pending_messages()
                if not first_pending:
                    time.sleep(0.02)
            self.assertTrue(first_pending)
            first_pending_envelopes = [parse_envelope(message) for message in first_pending]
            self.assertEqual("cell_updated", first_pending_envelopes[0].type)
            self.assertEqual("busy", first_pending_envelopes[0].payload["cell"]["status"])

            waiting_view = self._wait_for_view_lines(
                server,
                session_id,
                active_client_id,
                "req-inspect-waiting",
                [
                    f"meta> client={active_client_id} session={session_id} bufnr=unbound",
                    "started cell 12 [code:python]",
                    "input> value: ",
                ],
            )
            waiting_revision = waiting_view["revision"]

            input_messages = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "input_reply", '
                    '"request_id": "req-3", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '", "cell_id": 12, "client_id": "'
                    + active_client_id
                    + '", "value": "answer"}}'
                )
            )
            input_envelopes = [parse_envelope(message) for message in input_messages]
            self.assertTrue(input_envelopes[0].ok)
            self.assertEqual("session_updated", input_envelopes[1].type)
            self.assertEqual("input_reply", input_envelopes[1].payload["session"]["last_action"])

            resumed_view = self._wait_for_view_predicate(
                server,
                session_id,
                active_client_id,
                "req-inspect-resumed",
                lambda view: "execute[7]> input('value: ')" in view["lines"] and "stdout> typed: answer" in view["lines"],
            )
            self.assertGreater(resumed_view["revision"], waiting_revision)

            final_pending: list[str] = []
            deadline = time.time() + 1.0
            while time.time() < deadline and not final_pending:
                final_pending = server.drain_pending_messages()
                if not final_pending:
                    time.sleep(0.02)
            self.assertTrue(final_pending)
            final_pending_envelopes = [parse_envelope(message) for message in final_pending]
            self.assertEqual("cell_updated", final_pending_envelopes[0].type)
            self.assertEqual("done", final_pending_envelopes[0].payload["cell"]["status"])

            final_view = self._wait_for_view_predicate(
                server,
                session_id,
                active_client_id,
                "req-inspect-final",
                lambda view: view["execution_status"] == "done" and "finished: done" in view["lines"],
            )
            self.assertEqual("cell 12: done", final_view["title"])

            stop_messages = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "stop_session", '
                    '"request_id": "req-stop", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '"}}'
                )
            )
            self.assertTrue([parse_envelope(message) for message in stop_messages][0].ok)

    def test_protocol_server_managed_display_updates_replace_in_place_while_busy(self) -> None:
        server, session_id, _ = self._start_bound_managed_server(LiveDisplayUpdateClient())

        execute_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "main_lines": ["display()"]}}}'
            )
        )
        execute_envelopes = [parse_envelope(message) for message in execute_messages]
        active_client_id = execute_envelopes[2].payload["cell"]["client_id"]
        self.assertEqual("busy", execute_envelopes[2].payload["cell"]["status"])

        first_view = self._wait_for_view_lines(
            server,
            session_id,
            active_client_id,
            "req-inspect-1",
            [
                f"meta> client={active_client_id} session={session_id} bufnr=unbound",
                "started cell 12 [code:python]",
                "display> beta",
                "finished: done",
            ],
        )
        self.assertEqual("cell 12: done", first_view["title"])

        second_view = first_view
        self.assertEqual(
            [
                f"meta> client={active_client_id} session={session_id} bufnr=unbound",
                "started cell 12 [code:python]",
                "display> beta",
                "finished: done",
            ],
            second_view["lines"],
        )

        delayed_messages: list[str] = []
        deadline = time.time() + 1.0
        while time.time() < deadline and not delayed_messages:
            delayed_messages = server.drain_pending_messages()
            if not delayed_messages:
                time.sleep(0.02)
        self.assertTrue(delayed_messages)
        delayed_envelopes = [parse_envelope(message) for message in delayed_messages]
        self.assertEqual("cell_updated", delayed_envelopes[0].type)
        self.assertEqual("done", delayed_envelopes[0].payload["cell"]["status"])

        stop_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "stop_session", '
                '"request_id": "req-stop", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '"}}'
            )
        )
        stop_envelopes = [parse_envelope(message) for message in stop_messages]
        self.assertTrue(stop_envelopes[0].ok)

    def test_protocol_server_managed_clear_output_wait_preserves_then_replaces_live_view(self) -> None:
        server, session_id, _ = self._start_bound_managed_server(LiveClearOutputWaitClient())

        execute_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "main_lines": ["clear_then_result()"]}}}'
            )
        )
        execute_envelopes = [parse_envelope(message) for message in execute_messages]
        active_client_id = execute_envelopes[2].payload["cell"]["client_id"]
        self.assertEqual("busy", execute_envelopes[2].payload["cell"]["status"])

        first_view = self._wait_for_view_predicate(
            server,
            session_id,
            active_client_id,
            "req-inspect-1",
            lambda view: "result> 42" in view["lines"] and "stdout> before" not in view["lines"],
        )

        second_view = first_view

        delayed_messages: list[str] = []
        deadline = time.time() + 1.0
        while time.time() < deadline and not delayed_messages:
            delayed_messages = server.drain_pending_messages()
            if not delayed_messages:
                time.sleep(0.02)
        self.assertTrue(delayed_messages)
        delayed_envelopes = [parse_envelope(message) for message in delayed_messages]
        self.assertEqual("cell_updated", delayed_envelopes[0].type)
        self.assertEqual("done", delayed_envelopes[0].payload["cell"]["status"])

        stop_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "stop_session", '
                '"request_id": "req-stop", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '"}}'
            )
        )
        stop_envelopes = [parse_envelope(message) for message in stop_messages]
        self.assertTrue(stop_envelopes[0].ok)

    def test_protocol_server_managed_error_view_shows_full_traceback(self) -> None:
        server, session_id, _ = self._start_bound_managed_server(ErrorClient())

        execute_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "main_lines": ["raise ValueError(\\"boom\\")"]}}}'
            )
        )
        execute_envelopes = [parse_envelope(message) for message in execute_messages]
        active_client_id = execute_envelopes[2].payload["cell"]["client_id"]
        self.assertEqual("busy", execute_envelopes[2].payload["cell"]["status"])

        error_view = self._wait_for_view_predicate(
            server,
            session_id,
            active_client_id,
            "req-inspect-error",
            lambda view: "error: ValueError: boom" in view["lines"] and "trace> ValueError: boom" in view["lines"],
        )
        self.assertEqual(
            [
                f"meta> client={active_client_id} session={session_id} bufnr=unbound",
                "started cell 12 [code:python]",
                "error: ValueError: boom",
                "trace> Traceback (most recent call last):",
                'trace> File "<stdin>", line 1, in <module>',
                "trace> ValueError: boom",
            ],
            error_view["lines"],
        )

        delayed_messages: list[str] = []
        deadline = time.time() + 1.0
        while time.time() < deadline and not delayed_messages:
            delayed_messages = server.drain_pending_messages()
            if not delayed_messages:
                time.sleep(0.02)
        self.assertTrue(delayed_messages)
        delayed_envelopes = [parse_envelope(message) for message in delayed_messages]
        self.assertEqual("cell_updated", delayed_envelopes[0].type)
        self.assertEqual("error", delayed_envelopes[0].payload["cell"]["status"])

        final_view = self._wait_for_view_predicate(
            server,
            session_id,
            active_client_id,
            "req-inspect-error-final",
            lambda view: view["execution_status"] == "error" and "finished: error" in view["lines"],
        )
        self.assertEqual("cell 12: error", final_view["title"])

        stop_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "stop_session", '
                '"request_id": "req-stop", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '"}}'
            )
        )
        stop_envelopes = [parse_envelope(message) for message in stop_messages]
        self.assertTrue(stop_envelopes[0].ok)

    def test_managed_runtime_ignores_configured_client_command_after_inprocess_pivot(self) -> None:
        manager = FakeManager()
        client = FakeClient()
        command = f"{shlex_quote(sys.executable)} -u -c {shlex_quote('raise SystemExit(13)')}"
        with patch.dict(os.environ, {"JUSI_CLIENT_CMD": command}, clear=False):
            with patch("jusi.infrastructure.runtime._start_new_kernel", return_value=(manager, client)):
                runtime = ManagedKernelRuntime()
                session_id, connection = runtime.start_managed("python3")

            session = Session(notebook_id="nb-1", session_id=session_id, connection=connection, kernel_name="python3")
            prepared_client_id = runtime.prepare_client("nb-1", session_id)
            runtime_client = runtime.get_client(session_id, prepared_client_id)
            self.assertIsNotNone(runtime_client)
            self.assertTrue(runtime_client.handle.lifecycle[0].startswith("spawn:"))
            self.assertIsNone(getattr(runtime_client.handle, "pid", None))
            runtime.shutdown_client(session, prepared_client_id, "healthcheck")

    def test_managed_runtime_stores_client_metadata_in_snapshot(self) -> None:
        manager = FakeManager()
        client = FakeClient()
        with patch("jusi.infrastructure.runtime._start_new_kernel", return_value=(manager, client)):
            runtime = ManagedKernelRuntime()
            session_id, connection = runtime.start_managed("python3")

        session = Session(notebook_id="nb-1", session_id=session_id, connection=connection, kernel_name="python3")
        prepared_client_id = runtime.prepare_client("nb-1", session_id)
        runtime_client = runtime.get_client(session_id, prepared_client_id)
        self.assertIsNotNone(runtime_client)
        self.assertEqual(
            {
                "client_id": prepared_client_id,
                "notebook_id": "nb-1",
                "session_id": session_id,
                "client_bufnr": -1,
                "active_cell_id": None,
                "execution_status": "",
                "view_revision": 0,
                "lifecycle": ["ready"],
                "transcript": [],
                "view_title": f"client {prepared_client_id}: idle",
                "view_lines": [
                    f"meta> client={prepared_client_id} session={session_id} bufnr=unbound",
                    "waiting for transcript",
                ],
                "shutdown_reason": "",
            },
            runtime_client.handle.read_status(),
        )
        runtime.shutdown_client(session, prepared_client_id, "healthcheck")

    def test_managed_runtime_reports_missing_dependency_cleanly(self) -> None:
        with patch("builtins.__import__", side_effect=ModuleNotFoundError("missing")):
            runtime = ManagedKernelRuntime()
            with self.assertRaises(RuntimeDependencyError):
                runtime.start_managed("python3")

    def test_managed_runtime_reports_missing_kernelspec_cleanly(self) -> None:
        class FakeNoSuchKernel(Exception):
            pass

        with patch("jusi.infrastructure.runtime._start_new_kernel", side_effect=RuntimeDependencyError("missing kernelspec")):
            runtime = ManagedKernelRuntime()
            with self.assertRaises(RuntimeDependencyError):
                runtime.start_managed("python3")


if __name__ == "__main__":
    unittest.main()
