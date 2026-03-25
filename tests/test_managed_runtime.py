import os
import sys
import time
import unittest
from queue import Empty
from shlex import quote as shlex_quote
from typing import Optional
from unittest.mock import patch

from jusi.domain.models import CellExecution, ExecutableCell, Session
from jusi.infrastructure.runtime import InMemoryKernelRuntime, ManagedClientHandle, ManagedKernelRuntime, RuntimeDependencyError, build_runtime
from jusi.interfaces.protocol import parse_envelope
from jusi.interfaces.server import ProtocolServer


class FakeClient:
    def __init__(self) -> None:
        self.executed: list[tuple[str, bool | None]] = []
        self.channels_stopped = False
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
    def test_build_runtime_uses_managed_mode(self) -> None:
        with patch.dict(os.environ, {"JUSI_RUNTIME": "managed"}, clear=False):
            runtime = build_runtime()
        self.assertIsInstance(runtime, ManagedKernelRuntime)

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
            client_id = start_envelopes[4].payload["prepared"]["id"]

            bind_messages = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "bind_prepared_client", '
                    '"request_id": "req-bind", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '", "client_id": "'
                    + client_id
                    + '", "client_bufnr": 91}}'
                )
            )
            bind_envelopes = [parse_envelope(message) for message in bind_messages]
            self.assertTrue(bind_envelopes[0].ok)

            execute_messages = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "execute_cell", '
                    '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "main_lines": ["print(1)"]}}}'
                )
            )
            execute_envelopes = [parse_envelope(message) for message in execute_messages]
            self.assertEqual(["response", "event", "event", "event", "event"], [envelope.kind for envelope in execute_envelopes])
            self.assertEqual("busy", execute_envelopes[3].payload["cell"]["status"])
            self.assertEqual("binding", execute_envelopes[4].payload["prepared"]["state"])

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
            client_id = start_envelopes[4].payload["prepared"]["id"]

            bind_messages = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "bind_prepared_client", '
                    '"request_id": "req-bind", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '", "client_id": "'
                    + client_id
                    + '", "client_bufnr": 91}}'
                )
            )
            bind_envelopes = [parse_envelope(message) for message in bind_messages]
            self.assertTrue(bind_envelopes[0].ok)

            execute_messages = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "execute_cell", '
                    '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "main_lines": ["print(1)"]}}}'
                )
            )
            execute_envelopes = [parse_envelope(message) for message in execute_messages]
            self.assertEqual("busy", execute_envelopes[3].payload["cell"]["status"])

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

    def test_managed_runtime_uses_configured_client_command(self) -> None:
        manager = FakeManager()
        client = FakeClient()
        script = (
            "import json, os, sys; "
            "status_path = os.path.join(os.environ['JUSI_CLIENT_CONTROL_DIR'], 'status.json'); "
            "json.dump({'client_id': os.environ['JUSI_CLIENT_ID'], 'notebook_id': os.environ['JUSI_NOTEBOOK_ID'], "
            "'session_id': os.environ['JUSI_SESSION_ID'], 'client_bufnr': -1, 'active_cell_id': None, "
            "'lifecycle': ['ready'], 'shutdown_reason': ''}, open(status_path, 'w', encoding='utf-8')); "
            "sys.stdout.write('ready\\n'); sys.stdout.flush()"
        )
        command = f"{shlex_quote(sys.executable)} -u -c {shlex_quote(script)}"
        with patch.dict(os.environ, {"JUSI_CLIENT_CMD": command}, clear=False):
            with patch("jusi.infrastructure.runtime._start_new_kernel", return_value=(manager, client)):
                runtime = ManagedKernelRuntime()
                session_id, connection = runtime.start_managed("python3")

            session = Session(notebook_id="nb-1", session_id=session_id, connection=connection, kernel_name="python3")
            prepared_client_id = runtime.prepare_client("nb-1", session_id)
            runtime_client = runtime.get_client(session_id, prepared_client_id)
            self.assertIsNotNone(runtime_client)
            self.assertTrue(runtime_client.handle.lifecycle[0].startswith("spawn:"))
            runtime.shutdown_client(session, prepared_client_id, "healthcheck")

    def test_managed_runtime_passes_client_metadata_to_child_process(self) -> None:
        manager = FakeManager()
        client = FakeClient()
        script = (
            "import json, os, sys; "
            "status_path = os.path.join(os.environ['JUSI_CLIENT_CONTROL_DIR'], 'status.json'); "
            "json.dump({'client_id': os.environ['JUSI_CLIENT_ID'], 'notebook_id': os.environ['JUSI_NOTEBOOK_ID'], "
            "'session_id': os.environ['JUSI_SESSION_ID'], 'client_bufnr': -1, 'active_cell_id': None, "
            "'lifecycle': ['ready'], 'shutdown_reason': ''}, open(status_path, 'w', encoding='utf-8')); "
            "sys.stdout.write('ready\\n'); sys.stdout.flush(); "
            "sys.stdout.write('|'.join([os.environ['JUSI_CLIENT_ID'], os.environ['JUSI_NOTEBOOK_ID'], os.environ['JUSI_SESSION_ID']])) ; "
            "sys.stdout.flush()"
        )
        command = f"{shlex_quote(sys.executable)} -u -c {shlex_quote(script)}"
        with patch.dict(os.environ, {"JUSI_CLIENT_CMD": command}, clear=False):
            with patch("jusi.infrastructure.runtime._start_new_kernel", return_value=(manager, client)):
                runtime = ManagedKernelRuntime()
                session_id, connection = runtime.start_managed("python3")

            session = Session(notebook_id="nb-1", session_id=session_id, connection=connection, kernel_name="python3")
            prepared_client_id = runtime.prepare_client("nb-1", session_id)
            runtime_client = runtime.get_client(session_id, prepared_client_id)
            self.assertIsNotNone(runtime_client)
            self.assertIsNotNone(runtime_client.handle.process.stdout)
            metadata_line = runtime_client.handle.process.stdout.readline().strip()
            self.assertEqual(f"{prepared_client_id}|nb-1|{session_id}", metadata_line)
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
