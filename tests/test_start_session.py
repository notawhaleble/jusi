import time
import threading
import os
import unittest
from unittest.mock import patch

from jusi.application.ports import AttachSessionCommand, HandlerMessageCommand, StartSessionCommand
from jusi.application.use_cases import AttachSession, HandlerMessage, StartSession
from jusi.domain.models import ClientTransport, Session, SessionTarget
from jusi.infrastructure.client_runtime_controller import ActiveClientController, LiveClientControllerRegistry
from jusi.infrastructure.runtime import InMemoryClientHandle, InMemoryKernelRuntime, InMemorySessionStore
from jusi.visidata_support import JUSI_VISIDATARC_ENV
from jusi.interfaces.events import CollectingEventSink
from jusi.interfaces.protocol import parse_envelope
from jusi.interfaces.server import ProtocolServer
from jusi.plugins import DisplayHandlerRegistry, DisplayHandlerSpec, MagicCommand


class ExplodingExecuteRuntime(InMemoryKernelRuntime):
    def supports_background_execute(self) -> bool:
        return True

    def execute_cell(self, session, cell, client):  # type: ignore[no-untyped-def]
        _ = (session, cell, client)
        raise RuntimeError("boom")


class DeadPluginRuntimeHandle(InMemoryClientHandle):
    def plugin_runtime_is_alive(self):
        return False


class MisleadingPluginLivenessHandle(InMemoryClientHandle):
    def plugin_runtime_is_alive(self):
        return False


class DeadPluginRuntimeRuntime(InMemoryKernelRuntime):
    def _build_client_handle(self, client_id: str, notebook_id: str, session_id: str):  # type: ignore[no-untyped-def]
        return DeadPluginRuntimeHandle(
            client_id=client_id,
            notebook_id=notebook_id,
            session_id=session_id,
        )


class MisleadingPluginLivenessRuntime(InMemoryKernelRuntime):
    def _build_client_handle(self, client_id: str, notebook_id: str, session_id: str):  # type: ignore[no-untyped-def]
        return MisleadingPluginLivenessHandle(
            client_id=client_id,
            notebook_id=notebook_id,
            session_id=session_id,
        )


class FrontendActionHandle(InMemoryClientHandle):
    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.pending_actions: list[dict] = []

    def drain_frontend_actions(self) -> list[dict]:
        actions = list(self.pending_actions)
        self.pending_actions.clear()
        return actions


class FrontendActionRuntime(InMemoryKernelRuntime):
    def _build_client_handle(self, client_id: str, notebook_id: str, session_id: str):  # type: ignore[no-untyped-def]
        return FrontendActionHandle(
            client_id=client_id,
            notebook_id=notebook_id,
            session_id=session_id,
        )


class ExplodingStartRuntime(InMemoryKernelRuntime):
    def start_target(self, target, kernel_name):  # type: ignore[no-untyped-def]
        _ = (target, kernel_name)
        raise RuntimeError("start failed")


class ExplodingAttachRuntime(InMemoryKernelRuntime):
    def attach_target(self, target):  # type: ignore[no-untyped-def]
        _ = target
        raise RuntimeError("attach failed")


class ExplodingStopRuntime(InMemoryKernelRuntime):
    def stop_session(self, session):  # type: ignore[no-untyped-def]
        _ = session
        raise RuntimeError("stop failed")


class BlockingBackgroundExecuteRuntime(InMemoryKernelRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.seen_client_ids: list[str] = []

    def supports_background_execute(self) -> bool:
        return True

    def execute_cell(self, session, cell, client):  # type: ignore[no-untyped-def]
        self.started.set()
        self.release.wait(timeout=1.0)
        self.seen_client_ids.append(client.client_id)
        return super().execute_cell(session, cell, client)


class HandlerMessageProbe:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict]] = []

    def on_frontend_message(self, _context: object, message_type: str, payload: dict[str, object]) -> None:
        self.messages.append((message_type, dict(payload)))

    def interrupt(self) -> None:
        return None

    def stop(self) -> None:
        return None


class StartSessionTest(unittest.TestCase):
    def start_and_bind(self, server: ProtocolServer, notebook_id: str = "nb-1", kernel_name: str = "python3") -> tuple[str, str]:
        self.addCleanup(server.close)
        start_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "start_session", '
                '"request_id": "req-1", "payload": {"notebook_id": "' + notebook_id + '", "kernel_name": "' + kernel_name + '"}}'
            )
        )
        start_envelopes = [parse_envelope(message) for message in start_messages]
        session_id = start_envelopes[2].payload["session"]["id"]
        client_id = server._runtime.prepare_client(notebook_id, session_id)
        session = server._store.get_by_notebook(notebook_id)
        self.assertIsNotNone(session)
        server._runtime.bind_prepared_client(session, client_id, 91)
        return session_id, client_id

    def test_start_session_emits_starting_then_connected_updates(self) -> None:
        events = CollectingEventSink()
        use_case = StartSession(runtime=InMemoryKernelRuntime(), store=InMemorySessionStore(), events=events)

        session = use_case.execute(
            StartSessionCommand(
                notebook_id="nb-1",
                kernel_name="python3",
                target=SessionTarget(source="start", alias="python3", kind="kernel"),
            )
        )

        self.assertEqual("connected", session.state)
        self.assertTrue(session.session_id.startswith("sess-"))
        self.assertEqual("inmemory://python3/1", session.connection)

    def test_start_session_marks_failed_when_runtime_start_raises(self) -> None:
        events = CollectingEventSink()
        store = InMemorySessionStore()
        use_case = StartSession(runtime=ExplodingStartRuntime(), store=store, events=events)

        with self.assertRaisesRegex(RuntimeError, "start failed"):
            use_case.execute(
                StartSessionCommand(
                    notebook_id="nb-1",
                    kernel_name="python3",
                    target=SessionTarget(source="start", alias="python3", kind="kernel"),
                )
            )

        session = store.get_by_notebook("nb-1")
        self.assertIsNotNone(session)
        self.assertEqual("failed", session.state)  # type: ignore[union-attr]
        self.assertEqual("start failed", session.last_error)  # type: ignore[union-attr]
        self.assertEqual("starting", events.events[0]["payload"]["state"])
        self.assertEqual("failed", events.events[1]["payload"]["state"])

    def test_attach_session_marks_failed_when_runtime_attach_raises(self) -> None:
        events = CollectingEventSink()
        store = InMemorySessionStore()
        use_case = AttachSession(runtime=ExplodingAttachRuntime(), store=store, events=events)

        with self.assertRaisesRegex(RuntimeError, "attach failed"):
            use_case.execute(
                AttachSessionCommand(
                    notebook_id="nb-1",
                    target=SessionTarget(source="attach", kind="connection_file", value="/tmp/kernel.json"),
                )
            )

        session = store.get_by_notebook("nb-1")
        self.assertIsNotNone(session)
        self.assertEqual("failed", session.state)  # type: ignore[union-attr]
        self.assertEqual("attach failed", session.last_error)  # type: ignore[union-attr]
        self.assertEqual("starting", events.events[0]["payload"]["state"])
        self.assertEqual("failed", events.events[1]["payload"]["state"])

    def test_start_session_keeps_visidatarc_in_session_memory(self) -> None:
        events = CollectingEventSink()
        store = InMemorySessionStore()
        use_case = StartSession(runtime=InMemoryKernelRuntime(), store=store, events=events)

        session = use_case.execute(
            StartSessionCommand(
                notebook_id="nb-1",
                kernel_name="python3",
                target=SessionTarget(source="start", alias="python3", kind="kernel"),
                visidatarc="from visidata import vd\nvd.set_theme('asciimono')",
            )
        )

        self.assertEqual(
            "from visidata import vd\nvd.set_theme('asciimono')\n",
            session.visidatarc_content,
        )
        stored = store.get_by_notebook("nb-1")
        self.assertIsNotNone(stored)
        self.assertEqual(session.visidatarc_content, stored.visidatarc_content)

    def test_protocol_server_start_connects_without_prepared_client_updates(self) -> None:
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
        self.assertIsNone(envelopes[2].payload["session"]["expires_at"])
        self.assertEqual(
            {"source": "start", "alias": "python3", "kind": "kernel", "value": "", "config": {}},
            envelopes[2].payload["session"]["target"],
        )

    def test_protocol_server_start_accepts_top_level_visidatarc(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "start_session", '
                '"request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3", '
                '"visidatarc": "from visidata import vd\\nvd.set_theme(\\"asciimono\\")"}}'
            )
        )
        envelopes = [parse_envelope(message) for message in messages]

        self.assertTrue(envelopes[0].ok)
        session = server._store.get_by_notebook("nb-1")
        self.assertIsNotNone(session)
        self.assertEqual(
            'from visidata import vd\nvd.set_theme("asciimono")\n',
            session.visidatarc_content,
        )

    def test_handler_message_rejects_non_handler_runtime_mode(self) -> None:
        store = InMemorySessionStore()
        store.save(
            Session(
                notebook_id="nb-1",
                session_id="sess-1",
                state="connected",
                target=SessionTarget(source="start", alias="python3", kind="kernel"),
            )
        )
        registry = LiveClientControllerRegistry()
        controller = HandlerMessageProbe()
        registry.register(
            "sess-1",
            "client-1",
            ActiveClientController(
                client_id="client-1",
                controller=controller,
                runtime_mode="transcript",
                handler_id="vd",
            ),
        )
        use_case = HandlerMessage(store=store, active_client_controllers=registry)

        with self.assertRaisesRegex(ValueError, "does not accept handler messages"):
            use_case.execute(
                HandlerMessageCommand(
                    notebook_id="nb-1",
                    session_id="sess-1",
                    client_id="client-1",
                    handler_id="vd",
                    message_type="followup",
                    payload={"cell_text": "x"},
                )
            )
        self.assertEqual([], controller.messages)

    def test_start_session_includes_plugin_presentation_specs(self) -> None:
        registry = DisplayHandlerRegistry(
            (
                DisplayHandlerSpec(
                    handler_id="acme",
                    factory=object,  # type: ignore[arg-type]
                    magic_commands=(MagicCommand("acme"),),
                    presentation={"syntax": "sh", "indent": "sh", "followup": True, "completion": False},
                ),
            )
        )
        server = ProtocolServer(runtime=InMemoryKernelRuntime(), display_handlers=registry)
        self.addCleanup(server.close)

        messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "start_session", '
                '"request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}'
            )
        )
        envelopes = [parse_envelope(message) for message in messages]

        self.assertEqual(
            {"acme": {"syntax": "sh", "indent": "sh", "followup": True, "completion": False}},
            envelopes[2].payload["session"]["plugin_specs"],
        )

    def test_start_session_includes_plugin_palette_from_target_config(self) -> None:
        registry = DisplayHandlerRegistry(
            (
                DisplayHandlerSpec(
                    handler_id="vd",
                    factory=object,  # type: ignore[arg-type]
                    magic_commands=(MagicCommand("vd"),),
                ),
                DisplayHandlerSpec(
                    handler_id="sqlite",
                    factory=object,  # type: ignore[arg-type]
                    magic_commands=(MagicCommand("sql"),),
                ),
            )
        )
        server = ProtocolServer(runtime=InMemoryKernelRuntime(), display_handlers=registry)
        self.addCleanup(server.close)

        messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "start_session", '
                '"request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "py", '
                '"target": {"source": "start", "alias": "py", "kind": "venv", "value": "venv://myenv1", '
                '"config": {"sql": {"analyticsdb": {"provider": "postgres"}, "mysqlitedb": {"provider": "sqlite"}}}}}}'
            )
        )
        envelopes = [parse_envelope(message) for message in messages]

        self.assertEqual(
            {
                "vd": {"entries": []},
                "sql": {"entries": ["analyticsdb", "mysqlitedb"]},
            },
            envelopes[2].payload["session"]["palette"],
        )

    def test_protocol_server_start_accepts_explicit_target_identity(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "start_session", '
                '"request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "py", '
                '"target": {"source": "start", "alias": "py", "kind": "venv", "value": "venv://myenv1", '
                '"config": {"label": "local venv"}}}}'
            )
        )
        envelopes = [parse_envelope(message) for message in messages]

        self.assertTrue(envelopes[0].ok)
        self.assertIsNone(envelopes[2].payload["session"]["expires_at"])
        self.assertEqual(
            {
                "source": "start",
                "alias": "py",
                "kind": "venv",
                "value": "venv://myenv1",
                "config": {"label": "local venv"},
            },
            envelopes[2].payload["session"]["target"],
        )

    def test_start_session_uses_python3_for_venv_target_by_default(self) -> None:
        events = CollectingEventSink()
        use_case = StartSession(runtime=InMemoryKernelRuntime(), store=InMemorySessionStore(), events=events)

        session = use_case.execute(
            StartSessionCommand(
                notebook_id="nb-1",
                kernel_name="jusi",
                target=SessionTarget(source="start", alias="jusi", kind="venv", value="venv:///tmp/venv"),
            )
        )

        self.assertEqual("python3", session.kernel_name)

    def test_start_session_uses_target_config_kernel_name_for_docker_target(self) -> None:
        events = CollectingEventSink()
        use_case = StartSession(runtime=InMemoryKernelRuntime(), store=InMemorySessionStore(), events=events)

        session = use_case.execute(
            StartSessionCommand(
                notebook_id="nb-1",
                kernel_name="dockerjusi",
                target=SessionTarget(
                    source="start",
                    alias="dockerjusi",
                    kind="docker",
                    value="docker:///jolly_blackwell",
                    config={"kernel_name": "python3"},
                ),
            )
        )

        self.assertEqual("python3", session.kernel_name)

    def test_attach_session_emits_external_connection_file_connected_updates(self) -> None:
        events = CollectingEventSink()
        use_case = AttachSession(runtime=InMemoryKernelRuntime(), store=InMemorySessionStore(), events=events)

        session = use_case.execute(
            AttachSessionCommand(
                notebook_id="nb-1",
                target=SessionTarget(source="attach", kind="connection_file", value="/tmp/kernel.json"),
            )
        )

        self.assertEqual("connected", session.state)
        self.assertTrue(session.session_id.startswith("sess-"))
        self.assertEqual("/tmp/kernel.json", session.connection)
        self.assertEqual("attach", session.target.source)
        self.assertEqual("connection_file", session.target.kind)
        self.assertIsNone(session.expires_at)
        self.assertEqual(["session_updated", "session_updated"], [event["type"] for event in events.events])
        self.assertEqual("starting", events.events[0]["payload"]["state"])
        self.assertEqual("connected", events.events[1]["payload"]["state"])

    def test_attach_session_keeps_visidatarc_in_session_memory(self) -> None:
        events = CollectingEventSink()
        store = InMemorySessionStore()
        use_case = AttachSession(runtime=InMemoryKernelRuntime(), store=store, events=events)

        session = use_case.execute(
            AttachSessionCommand(
                notebook_id="nb-1",
                target=SessionTarget(source="attach", kind="connection_file", value="/tmp/kernel.json"),
                visidatarc="vd.options.set('disp_menu', False)",
            )
        )

        self.assertEqual("vd.options.set('disp_menu', False)\n", session.visidatarc_content)
        stored = store.get_by_notebook("nb-1")
        self.assertIsNotNone(stored)
        self.assertEqual(session.visidatarc_content, stored.visidatarc_content)

    def test_plugin_transport_receives_session_visidatarc_content(self) -> None:
        runtime = InMemoryKernelRuntime()
        session = Session(
            notebook_id="nb-1",
            session_id="sess-1",
            state="connected",
            visidatarc_content="vd.set_theme('asciimono')\n",
        )
        client_id = runtime.prepare_client("nb-1", "sess-1")
        runtime.set_client_transport(
            session,
            client_id,
            ClientTransport(
                kind="native_terminal",
                attach_cmd=["python", "-m", "jusi", "plugin-runtime"],
                attach_env={"FOO": "bar"},
                session_id="sess-1",
                client_id=client_id,
                handler_id="vd",
            ),
        )

        inspect = runtime.read_client_view(session, client_id)
        self.assertEqual("vd.set_theme('asciimono')\n", inspect["transport"]["attach_env"][JUSI_VISIDATARC_ENV])

    def test_attach_session_rejects_non_connection_file_target_kind(self) -> None:
        events = CollectingEventSink()
        use_case = AttachSession(runtime=InMemoryKernelRuntime(), store=InMemorySessionStore(), events=events)

        with self.assertRaisesRegex(ValueError, "target.kind=connection_file"):
            use_case.execute(
                AttachSessionCommand(
                    notebook_id="nb-1",
                    target=SessionTarget(source="attach", kind="ssh", value="ssh://user@host1"),
                )
            )

    def test_protocol_server_attach_records_explicit_target(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "attach_session", '
                '"request_id": "req-1", "payload": {"notebook_id": "nb-1", '
                '"target": {"source": "attach", "kind": "connection_file", "value": "/tmp/kernel.json", "config": {"label": "host1"}}}}'
            )
        )
        envelopes = [parse_envelope(message) for message in messages]

        self.assertTrue(envelopes[0].ok)
        self.assertEqual("starting", envelopes[1].payload["session"]["state"])
        self.assertEqual("connected", envelopes[2].payload["session"]["state"])
        self.assertIsNone(envelopes[2].payload["session"]["expires_at"])
        self.assertEqual(
            {
                "source": "attach",
                "alias": "",
                "kind": "connection_file",
                "value": "/tmp/kernel.json",
                "config": {"label": "host1"},
            },
            envelopes[2].payload["session"]["target"],
        )
        self.assertEqual(3, len(envelopes))

    def test_attach_session_includes_plugin_metadata(self) -> None:
        registry = DisplayHandlerRegistry(
            (
                DisplayHandlerSpec(
                    handler_id="vd",
                    factory=object,  # type: ignore[arg-type]
                    magic_commands=(MagicCommand("vd"),),
                    presentation={"syntax": "python", "indent": "python", "followup": True, "completion": False},
                ),
                DisplayHandlerSpec(
                    handler_id="sqlite",
                    factory=object,  # type: ignore[arg-type]
                    magic_commands=(MagicCommand("sql"),),
                    family_presentation={"syntax": "sql", "indent": "sql", "followup": True, "completion": True},
                    presentation={"syntax": "sqlite", "indent": "sql", "followup": True, "completion": True},
                ),
            )
        )
        server = ProtocolServer(runtime=InMemoryKernelRuntime(), display_handlers=registry)
        self.addCleanup(server.close)

        messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "attach_session", '
                '"request_id": "req-1", "payload": {"notebook_id": "nb-1", '
                '"target": {"source": "attach", "kind": "connection_file", "value": "/tmp/kernel.json", '
                '"config": {"sql": {"analyticsdb": {"provider": "postgres"}}}}}}'
            )
        )
        envelopes = [parse_envelope(message) for message in messages]

        self.assertEqual(
            {
                "vd": {"syntax": "python", "indent": "python", "followup": True, "completion": False},
                "sql": {"syntax": "sql", "indent": "sql", "followup": True, "completion": True},
            },
            envelopes[2].payload["session"]["plugin_specs"],
        )
        self.assertEqual(
            {
                "vd": {"entries": []},
                "sql": {"entries": ["analyticsdb"]},
            },
            envelopes[2].payload["session"]["palette"],
        )

    def test_protocol_server_attach_rejects_non_connection_file_target_kind(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "attach_session", '
                '"request_id": "req-1", "payload": {"notebook_id": "nb-1", '
                '"target": {"source": "attach", "kind": "ssh", "value": "ssh://user@host1"}}}'
            )
        )
        envelope = [parse_envelope(message) for message in messages][0]
        self.assertFalse(envelope.ok)
        self.assertEqual("invalid_state", envelope.error["code"])

    def test_internal_client_bind_helper_marks_runtime_client_bound(self) -> None:
        runtime = InMemoryKernelRuntime()
        server = ProtocolServer(runtime=runtime)
        session_id, client_id = self.start_and_bind(server)

        self.assertTrue(session_id.startswith("sess-"))
        self.assertEqual("client-1", client_id)
        runtime_client = runtime.get_client(session_id, client_id)
        self.assertIsNotNone(runtime_client)
        self.assertEqual(91, runtime_client.client_bufnr)
        self.assertEqual("active", runtime_client.state)
        self.assertEqual(["bind:91"], runtime_client.handle.lifecycle)

    def test_shutdown_untracked_client_fails_explicitly(self) -> None:
        runtime = InMemoryKernelRuntime()
        server = ProtocolServer(runtime=runtime)
        session_id, client_id = self.start_and_bind(server)

        shutdown_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "shutdown_client", '
                '"request_id": "req-shutdown", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell_id": 0, "client_id": "'
                + client_id
                + '", "reason": "user_close"}}'
            )
        )
        shutdown_envelopes = [parse_envelope(message) for message in shutdown_messages]
        self.assertFalse(shutdown_envelopes[0].ok)
        self.assertEqual("invalid_state", shutdown_envelopes[0].error["code"])

    def test_execute_does_not_require_prebound_client(self) -> None:
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
                + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "main_lines": ["print(1)"]}}}'
            )
        )
        execute_envelopes = [parse_envelope(message) for message in execute_messages]
        self.assertTrue(execute_envelopes[0].ok)

    def test_execute_flow_allocates_direct_cell_owned_client(self) -> None:
        runtime = InMemoryKernelRuntime()
        server = ProtocolServer(runtime=runtime)
        session_id, _client_id = self.start_and_bind(server)

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
        self.assertEqual("cell_updated", execute_envelopes[2].type)
        self.assertEqual("busy", execute_envelopes[2].payload["cell"]["status"])
        self.assertEqual("transcript", execute_envelopes[2].payload["cell"]["runtime_mode"])
        self.assertEqual("cell_updated", execute_envelopes[3].type)
        self.assertEqual("done", execute_envelopes[3].payload["cell"]["status"])
        active_client = runtime.get_client(session_id, execute_envelopes[2].payload["cell"]["client_id"])
        self.assertIsNotNone(active_client)
        self.assertEqual("active", active_client.state)
        self.assertEqual(12, active_client.cell_id)
        self.assertEqual(
            [
                "activate:12",
                "status:busy",
                "transport:native_terminal",
                "event:execution_started",
                "status:done",
                "event:execution_finished",
            ],
            active_client.handle.lifecycle,
        )
        session = server._store.get_by_notebook("nb-1")
        self.assertIsNotNone(session)
        client_view = runtime.read_client_view(session, active_client.client_id)
        self.assertEqual("native_terminal", client_view["transport"]["kind"])
        self.assertEqual([os.sys.executable, "-m", "jusi", "client-process", "terminal-attach"], client_view["transport"]["attach_cmd"])
        self.assertEqual(
            {
                "title": "cell 12: done",
                "lines": [
                    f"meta> client={active_client.client_id} session={session_id} bufnr=unbound",
                    "started cell 12 [code:python]",
                    "finished: done",
                ],
                "execution_status": "done",
                "active_cell_id": 12,
                "revision": 6,
                "transport": client_view["transport"],
            },
            client_view,
        )

    def test_background_execute_failure_marks_cell_error_and_records_client_error_event(self) -> None:
        runtime = ExplodingExecuteRuntime()
        server = ProtocolServer(runtime=runtime)
        self.addCleanup(server.close)
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
                + '", "cell": {"id": 12, "kind": "magic", "syntax": "sql", "main_lines": ["%%sql test_sqlite", "select 1"]}}}'
            )
        )
        execute_envelopes = [parse_envelope(message) for message in execute_messages]
        self.assertTrue(execute_envelopes[0].ok)

        time.sleep(0.05)

        pending = [parse_envelope(message) for message in server.drain_pending_messages()]
        cell_updates = [env for env in pending if env.type == "cell_updated"]
        self.assertTrue(cell_updates)
        self.assertEqual("error", cell_updates[-1].payload["cell"]["status"])

        session = server._store.get_by_notebook("nb-1")
        self.assertIsNotNone(session)
        client_view = runtime.read_client_view(session, "client-1")
        self.assertEqual("error", client_view["execution_status"])
        runtime_client = runtime.get_client(session_id, "client-1")
        self.assertIsNotNone(runtime_client)
        self.assertEqual("error", runtime_client.handle.transcript[-1]["type"])
        self.assertIn("RuntimeError: boom", runtime_client.handle.transcript[-1]["message"])

    def test_follow_up_cell_does_not_block_later_execution(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        session_id, _client_id = self.start_and_bind(server)

        followup_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "magic", "syntax": "python", "main_lines": ["%%vd", "pods"]}}}'
            )
        )
        followup_envelopes = [parse_envelope(message) for message in followup_messages]
        self.assertEqual("follow-up", followup_envelopes[3].payload["cell"]["status"])
        self.assertEqual("handler", followup_envelopes[3].payload["cell"]["owner"]["kind"])
        self.assertEqual("handler", followup_envelopes[3].payload["cell"]["runtime_mode"])

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
        self.assertEqual("busy", next_envelopes[2].payload["cell"]["status"])
        self.assertEqual("done", next_envelopes[3].payload["cell"]["status"])

    def test_handler_handoff_cell_update_includes_presentation(self) -> None:
        registry = DisplayHandlerRegistry(
            (
                DisplayHandlerSpec(
                    handler_id="vd",
                    factory=object,  # type: ignore[arg-type]
                    magic_commands=(MagicCommand("vd"),),
                    presentation={"syntax": "python", "indent": "python", "followup": True, "completion": False},
                ),
            )
        )
        server = ProtocolServer(runtime=InMemoryKernelRuntime(), display_handlers=registry)
        session_id, _client_id = self.start_and_bind(server)

        messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "magic", "syntax": "python", "main_lines": ["%%vd", "pods"]}}}'
            )
        )
        envelopes = [parse_envelope(message) for message in messages]

        self.assertEqual("follow-up", envelopes[3].payload["cell"]["status"])
        self.assertEqual("handler", envelopes[3].payload["cell"]["runtime_mode"])
        self.assertEqual(
            {"syntax": "python", "indent": "python", "followup": True, "completion": False},
            envelopes[3].payload["cell"]["presentation"],
        )

    def test_interrupt_finished_execution_fails_explicitly(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        session_id, _client_id = self.start_and_bind(server)

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

    def test_interrupt_execution_with_mismatched_runtime_mode_fails_explicitly(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        session_id, _client_id = self.start_and_bind(server)

        execute_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "keep_running": true, "main_lines": ["while True: pass"]}}}'
            )
        )
        _ = [parse_envelope(message) for message in execute_messages]
        execution = server._store.get_execution("nb-1", 12)
        self.assertIsNotNone(execution)
        execution.runtime_mode = "handler"  # type: ignore[union-attr]
        server._store.save_execution("nb-1", execution)  # type: ignore[arg-type]

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

    def test_shutdown_cell_client_transitions_client_state_to_shutdown(self) -> None:
        runtime = InMemoryKernelRuntime()
        server = ProtocolServer(runtime=runtime)
        session_id, _client_id = self.start_and_bind(server)

        followup_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "magic", "syntax": "sql", "main_lines": ["%%sql", "select 1"]}}}'
            )
        )
        followup_envelopes = [parse_envelope(message) for message in followup_messages]
        active_client_id = followup_envelopes[3].payload["cell"]["client_id"]

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
        self.assertEqual("shutting_down", shutdown_envelopes[1].payload["cell"]["client_state"])
        self.assertEqual("done", shutdown_envelopes[1].payload["cell"]["status"])
        self.assertEqual("unknown", shutdown_envelopes[1].payload["cell"]["owner"]["kind"])
        self.assertNotIn("client_id", shutdown_envelopes[1].payload["cell"])
        self.assertNotIn("transport", shutdown_envelopes[1].payload["cell"])
        self.assertEqual("shutdown", shutdown_envelopes[2].payload["cell"]["client_state"])
        self.assertEqual("done", shutdown_envelopes[2].payload["cell"]["status"])
        self.assertEqual("unknown", shutdown_envelopes[2].payload["cell"]["owner"]["kind"])
        self.assertNotIn("client_id", shutdown_envelopes[2].payload["cell"])
        self.assertNotIn("transport", shutdown_envelopes[2].payload["cell"])
        self.assertNotIn("client_bufnr", shutdown_envelopes[2].payload["cell"])
        self.assertIsNone(runtime.get_client(session_id, active_client_id))

    def test_shutdown_busy_kernel_client_interrupts_and_clears_runtime_identity(self) -> None:
        runtime = InMemoryKernelRuntime()
        server = ProtocolServer(runtime=runtime)
        session_id, _client_id = self.start_and_bind(server)

        execute_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "keep_running": true, "main_lines": ["while True: pass"]}}}'
            )
        )
        execute_envelopes = [parse_envelope(message) for message in execute_messages]
        active_client_id = execute_envelopes[3].payload["cell"]["client_id"]

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
        self.assertEqual("shutting_down", shutdown_envelopes[1].payload["cell"]["client_state"])
        self.assertEqual("interrupted", shutdown_envelopes[1].payload["cell"]["status"])
        self.assertEqual("unknown", shutdown_envelopes[1].payload["cell"]["owner"]["kind"])
        self.assertNotIn("client_id", shutdown_envelopes[1].payload["cell"])
        self.assertNotIn("transport", shutdown_envelopes[1].payload["cell"])
        self.assertEqual("shutdown", shutdown_envelopes[2].payload["cell"]["client_state"])
        self.assertEqual("interrupted", shutdown_envelopes[2].payload["cell"]["status"])
        self.assertEqual("unknown", shutdown_envelopes[2].payload["cell"]["owner"]["kind"])
        self.assertNotIn("client_id", shutdown_envelopes[2].payload["cell"])
        self.assertNotIn("transport", shutdown_envelopes[2].payload["cell"])
        self.assertIsNone(runtime.get_client(session_id, active_client_id))

    def test_shutdown_client_with_mismatched_runtime_mode_fails_explicitly(self) -> None:
        runtime = InMemoryKernelRuntime()
        server = ProtocolServer(runtime=runtime)
        session_id, _client_id = self.start_and_bind(server)

        execute_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "keep_running": true, "main_lines": ["while True: pass"]}}}'
            )
        )
        execute_envelopes = [parse_envelope(message) for message in execute_messages]
        client_id = execute_envelopes[3].payload["cell"]["client_id"]
        execution = server._store.get_execution("nb-1", 12)
        self.assertIsNotNone(execution)
        execution.runtime_mode = "handler"  # type: ignore[union-attr]
        server._store.save_execution("nb-1", execution)  # type: ignore[arg-type]

        shutdown_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "shutdown_client", '
                '"request_id": "req-shutdown", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell_id": 12, "client_id": "'
                + client_id
                + '", "reason": "user_close"}}'
            )
        )
        shutdown_envelopes = [parse_envelope(message) for message in shutdown_messages]
        self.assertFalse(shutdown_envelopes[0].ok)
        self.assertEqual("invalid_state", shutdown_envelopes[0].error["code"])

    def test_inspect_client_returns_runtime_view_snapshot(self) -> None:
        runtime = InMemoryKernelRuntime()
        server = ProtocolServer(runtime=runtime)
        session_id, _client_id = self.start_and_bind(server)

        execute_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "main_lines": ["print(1)"]}}}'
            )
        )
        execute_envelopes = [parse_envelope(message) for message in execute_messages]
        client_id = execute_envelopes[3].payload["cell"]["client_id"]

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
        inspect_envelopes = [parse_envelope(message) for message in inspect_messages]
        self.assertTrue(inspect_envelopes[0].ok)
        inspect_client = inspect_envelopes[0].payload["client"]
        self.assertEqual("native_terminal", inspect_client["transport"]["kind"])
        self.assertEqual(
            {
                "title": "cell 12: done",
                "lines": [
                    f"meta> client={client_id} session={session_id} bufnr=unbound",
                    "started cell 12 [code:python]",
                    "finished: done",
                ],
                "execution_status": "done",
                "active_cell_id": 12,
                "revision": 6,
                "runtime_mode": "transcript",
                "transport": inspect_client["transport"],
            },
            inspect_client,
        )

    def test_inspect_client_does_not_demote_transcript_client_on_plugin_liveness_probe(self) -> None:
        runtime = MisleadingPluginLivenessRuntime()
        server = ProtocolServer(runtime=runtime)
        session_id, _client_id = self.start_and_bind(server)

        execute_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "main_lines": ["print(1)"]}}}'
            )
        )
        execute_envelopes = [parse_envelope(message) for message in execute_messages]
        client_id = execute_envelopes[3].payload["cell"]["client_id"]

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
        inspect_envelopes = [parse_envelope(message) for message in inspect_messages]
        self.assertTrue(inspect_envelopes[0].ok)
        self.assertEqual("transcript", inspect_envelopes[0].payload["client"]["runtime_mode"])
        pending = [parse_envelope(message) for message in server.drain_pending_messages()]
        self.assertEqual([], [env for env in pending if env.type == "cell_updated"])
        self.assertIsNotNone(runtime.get_client(session_id, client_id))

    def test_inspect_client_returns_handler_runtime_mode_for_live_followup(self) -> None:
        runtime = InMemoryKernelRuntime()
        server = ProtocolServer(runtime=runtime)
        session_id, _client_id = self.start_and_bind(server)

        followup_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "magic", "syntax": "python", "main_lines": ["%%vd", "pods"]}}}'
            )
        )
        followup_envelopes = [parse_envelope(message) for message in followup_messages]
        client_id = followup_envelopes[3].payload["cell"]["client_id"]

        inspect_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "inspect_client", '
                '"request_id": "req-inspect-handler", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "client_id": "'
                + client_id
                + '"}}'
            )
        )
        inspect_envelopes = [parse_envelope(message) for message in inspect_messages]
        self.assertTrue(inspect_envelopes[0].ok)
        self.assertEqual("handler", inspect_envelopes[0].payload["client"]["runtime_mode"])

    def test_inspect_client_demotes_dead_handler_followup_session(self) -> None:
        runtime = DeadPluginRuntimeRuntime()
        server = ProtocolServer(runtime=runtime)
        session_id, _client_id = self.start_and_bind(server)

        followup_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "magic", "syntax": "python", "main_lines": ["%%vd", "pods"]}}}'
            )
        )
        followup_envelopes = [parse_envelope(message) for message in followup_messages]
        client_id = followup_envelopes[3].payload["cell"]["client_id"]

        inspect_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "inspect_client", '
                '"request_id": "req-inspect-dead", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "client_id": "'
                + client_id
                + '"}}'
            )
        )
        inspect_envelopes = [parse_envelope(message) for message in inspect_messages]
        self.assertTrue(inspect_envelopes[0].ok)
        self.assertNotEqual("handler", inspect_envelopes[0].payload["client"].get("runtime_mode"))

        pending = [parse_envelope(message) for message in server.drain_pending_messages()]
        cell_updates = [env for env in pending if env.type == "cell_updated"]
        self.assertTrue(cell_updates)
        self.assertEqual("done", cell_updates[-1].payload["cell"]["status"])
        self.assertEqual("unknown", cell_updates[-1].payload["cell"]["owner"]["kind"])
        self.assertEqual("shutdown", cell_updates[-1].payload["cell"]["client_state"])
        self.assertNotIn("runtime_mode", cell_updates[-1].payload["cell"])
        self.assertNotIn("client_id", cell_updates[-1].payload["cell"])
        self.assertNotIn("transport", cell_updates[-1].payload["cell"])
        self.assertIsNone(runtime.get_client(session_id, client_id))

    def test_poll_client_updates_demotes_dead_handler_followup_session(self) -> None:
        runtime = DeadPluginRuntimeRuntime()
        server = ProtocolServer(runtime=runtime)
        session_id, _client_id = self.start_and_bind(server)

        followup_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "magic", "syntax": "python", "main_lines": ["%%vd", "pods"]}}}'
            )
        )
        followup_envelopes = [parse_envelope(message) for message in followup_messages]
        client_id = followup_envelopes[3].payload["cell"]["client_id"]

        server.poll_client_updates()
        pending = [parse_envelope(message) for message in server.drain_pending_messages()]
        cell_updates = [env for env in pending if env.type == "cell_updated"]
        self.assertTrue(cell_updates)
        self.assertEqual("done", cell_updates[-1].payload["cell"]["status"])
        self.assertEqual("unknown", cell_updates[-1].payload["cell"]["owner"]["kind"])
        self.assertEqual("shutdown", cell_updates[-1].payload["cell"]["client_state"])
        self.assertNotIn("runtime_mode", cell_updates[-1].payload["cell"])
        self.assertNotIn("client_id", cell_updates[-1].payload["cell"])
        self.assertNotIn("transport", cell_updates[-1].payload["cell"])
        self.assertIsNone(runtime.get_client(session_id, client_id))

    def test_poll_client_updates_emits_frontend_action_request(self) -> None:
        runtime = FrontendActionRuntime()
        server = ProtocolServer(runtime=runtime)
        session_id, client_id = self.start_and_bind(server)
        server._active_client_controllers.register(
            session_id,
            client_id,
            ActiveClientController(
                client_id=client_id,
                controller=HandlerMessageProbe(),
                runtime_mode="handler",
                handler_id="shell",
            ),
        )
        session = server._store.get_by_notebook("nb-1")
        self.assertIsNotNone(session)
        runtime.set_client_transport(
            session,
            client_id,
            ClientTransport(
                kind="native_terminal",
                attach_cmd=["/bin/sh"],
                attach_env={},
                session_id=session_id,
                client_id=client_id,
                handler_id="shell",
            ),
        )
        runtime_client = runtime.get_client(session_id, client_id)
        self.assertIsNotNone(runtime_client)
        handle = runtime_client.handle
        self.assertIsInstance(handle, FrontendActionHandle)
        handle.pending_actions.append(
            {
                "action_type": "open_path",
                "payload": {
                    "path": "/tmp/example.txt",
                    "open_in": "tab",
                    "line": 12,
                    "column": 3,
                },
            }
        )

        server.poll_client_updates()

        pending = [parse_envelope(message) for message in server.drain_pending_messages()]
        action_messages = [env for env in pending if env.type == "handler_message"]
        self.assertEqual(1, len(action_messages))
        self.assertEqual("action_request", action_messages[0].payload["message_type"])
        self.assertEqual("shell", action_messages[0].payload["handler_id"])
        self.assertEqual(
            {
                "action_type": "open_path",
                "payload": {
                    "path": "/tmp/example.txt",
                    "open_in": "tab",
                    "line": 12,
                    "column": 3,
                },
            },
            action_messages[0].payload["payload"],
        )

    def test_poll_client_updates_ignores_frontend_actions_for_transcript_runtime(self) -> None:
        runtime = FrontendActionRuntime()
        server = ProtocolServer(runtime=runtime)
        session_id, client_id = self.start_and_bind(server)
        session = server._store.get_by_notebook("nb-1")
        self.assertIsNotNone(session)
        runtime.set_client_transport(
            session,
            client_id,
            ClientTransport(
                kind="native_terminal",
                attach_cmd=["/bin/sh"],
                attach_env={},
                session_id=session_id,
                client_id=client_id,
                handler_id="shell",
            ),
        )
        runtime_client = runtime.get_client(session_id, client_id)
        self.assertIsNotNone(runtime_client)
        handle = runtime_client.handle
        self.assertIsInstance(handle, FrontendActionHandle)
        handle.pending_actions.append(
            {
                "action_type": "open_path",
                "payload": {
                    "path": "/tmp/example.txt",
                },
            }
        )

        server.poll_client_updates()

        pending = [parse_envelope(message) for message in server.drain_pending_messages()]
        action_messages = [env for env in pending if env.type == "handler_message"]
        self.assertEqual([], action_messages)

    def test_inspect_client_renders_busy_execution_state(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        session_id, _client_id = self.start_and_bind(server)

        execute_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "keep_running": true, "main_lines": ["while True: pass"]}}}'
            )
        )
        execute_envelopes = [parse_envelope(message) for message in execute_messages]
        client_id = execute_envelopes[3].payload["cell"]["client_id"]

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
        inspect_envelopes = [parse_envelope(message) for message in inspect_messages]
        self.assertTrue(inspect_envelopes[0].ok)
        inspect_client = inspect_envelopes[0].payload["client"]
        self.assertEqual("native_terminal", inspect_client["transport"]["kind"])
        self.assertEqual(
            {
                "title": "cell 12: busy",
                "lines": [
                    f"meta> client={client_id} session={session_id} bufnr=unbound",
                    "started cell 12 [code:python]",
                    "state: busy",
                ],
                "execution_status": "busy",
                "active_cell_id": 12,
                "revision": 5,
                "runtime_mode": "transcript",
                "transport": inspect_client["transport"],
            },
            inspect_client,
        )

    def test_inspect_client_fails_for_unknown_client(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        session_id, _client_id = self.start_and_bind(server)

        inspect_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "inspect_client", '
                '"request_id": "req-inspect", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "client_id": "client-missing"}}'
            )
        )
        inspect_envelopes = [parse_envelope(message) for message in inspect_messages]
        self.assertFalse(inspect_envelopes[0].ok)
        self.assertEqual("invalid_state", inspect_envelopes[0].error["code"])

    def test_inspect_client_fails_for_unknown_session(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())

        inspect_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "inspect_client", '
                '"request_id": "req-inspect", "payload": {"notebook_id": "nb-1", "session_id": "sess-missing", "client_id": "client-1"}}'
            )
        )
        inspect_envelopes = [parse_envelope(message) for message in inspect_messages]
        self.assertFalse(inspect_envelopes[0].ok)
        self.assertEqual("session_not_found", inspect_envelopes[0].error["code"])

    def test_interrupt_handler_owned_execution(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        session_id, _client_id = self.start_and_bind(server)

        server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "magic", "syntax": "python", "main_lines": ["%%vd", "pods"]}}}'
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
        session_id, _client_id = self.start_and_bind(server)

        execute_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "keep_running": true, "main_lines": ["while True: pass"]}}}'
            )
        )
        execute_envelopes = [parse_envelope(message) for message in execute_messages]
        self.assertEqual("busy", execute_envelopes[3].payload["cell"]["status"])
        self.assertEqual("kernel", execute_envelopes[3].payload["cell"]["owner"]["kind"])

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

    def test_disconnect_marks_active_execution_owner_unknown_and_reconnect_restores_binding_state(self) -> None:
        runtime = InMemoryKernelRuntime()
        registry = DisplayHandlerRegistry(
            (
                DisplayHandlerSpec(
                    handler_id="vd",
                    factory=object,  # type: ignore[arg-type]
                    magic_commands=(MagicCommand("vd"),),
                    presentation={"syntax": "python", "indent": "python", "followup": True, "completion": False},
                ),
                DisplayHandlerSpec(
                    handler_id="sqlite",
                    factory=object,  # type: ignore[arg-type]
                    magic_commands=(MagicCommand("sql"),),
                    family_presentation={"syntax": "sql", "indent": "sql", "followup": True, "completion": True},
                    presentation={"syntax": "sqlite", "indent": "sql", "followup": True, "completion": True},
                ),
            )
        )
        server = ProtocolServer(runtime=runtime, display_handlers=registry)
        session_id, _client_id = self.start_and_bind(server)

        start_session = server._store.get_by_notebook("nb-1")
        self.assertIsNotNone(start_session)
        self.assertEqual(
            {
                "vd": {"entries": []},
                "sql": {"entries": []},
            },
            start_session.palette,
        )

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
        self.assertGreater(disconnect_envelopes[1].payload["session"]["expires_at"], 0)
        self.assertEqual("unknown", disconnect_envelopes[2].payload["cell"]["owner"]["kind"])
        self.assertEqual("follow-up", disconnect_envelopes[2].payload["cell"]["status"])
        self.assertEqual("transcript", disconnect_envelopes[2].payload["cell"]["runtime_mode"])
        self.assertEqual([], runtime.list_clients(session_id))

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
        self.assertIsNone(reconnect_envelopes[1].payload["session"]["expires_at"])
        self.assertEqual(
            {
                "vd": {"syntax": "python", "indent": "python", "followup": True, "completion": False},
                "sql": {"syntax": "sql", "indent": "sql", "followup": True, "completion": True},
            },
            reconnect_envelopes[1].payload["session"]["plugin_specs"],
        )
        self.assertEqual(
            {
                "vd": {"entries": []},
                "sql": {"entries": []},
            },
            reconnect_envelopes[1].payload["session"]["palette"],
        )
        self.assertEqual("connected", reconnect_envelopes[2].payload["session"]["state"])
        self.assertIsNone(reconnect_envelopes[2].payload["session"]["expires_at"])
        self.assertEqual(reconnect_envelopes[1].payload["session"]["plugin_specs"], reconnect_envelopes[2].payload["session"]["plugin_specs"])
        self.assertEqual(reconnect_envelopes[1].payload["session"]["palette"], reconnect_envelopes[2].payload["session"]["palette"])

    def test_finished_plain_kernel_cell_releases_managed_controller(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        self.addCleanup(server.close)

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
        client_id = execute_envelopes[3].payload["cell"]["client_id"]

        self.assertIsNone(server._active_client_controllers.get(session_id, client_id))

    def test_busy_plain_kernel_cell_does_not_register_live_controller(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        self.addCleanup(server.close)

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
                + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "keep_running": true, "main_lines": ["print(1)"]}}}'
            )
        )
        execute_envelopes = [parse_envelope(message) for message in execute_messages]
        client_id = execute_envelopes[3].payload["cell"]["client_id"]

        self.assertIsNone(server._active_client_controllers.get(session_id, client_id))

    def test_disconnect_marks_started_session_disconnected(self) -> None:
        runtime = InMemoryKernelRuntime()
        server = ProtocolServer(runtime=runtime)
        session_id, _client_id = self.start_and_bind(server)

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
        self.assertGreater(disconnect_envelopes[1].payload["session"]["expires_at"], 0)
        self.assertEqual([], disconnect_envelopes[2:])
        self.assertEqual([], runtime.list_clients(session_id))

    def test_interrupt_unknown_owner_fails_after_disconnect(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        session_id, _client_id = self.start_and_bind(server)

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

    def test_reconnect_unknown_session_returns_session_not_found(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())

        reconnect_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "reconnect_session", '
                '"request_id": "req-missing", "payload": {"notebook_id": "nb-1", "session_id": "sess-missing"}}'
            )
        )
        reconnect_envelopes = [parse_envelope(message) for message in reconnect_messages]
        self.assertFalse(reconnect_envelopes[0].ok)
        self.assertEqual("session_not_found", reconnect_envelopes[0].error["code"])

    def test_reconnect_stopped_session_returns_session_stopped(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        session_id, _client_id = self.start_and_bind(server)

        server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "stop_session", '
                '"request_id": "req-stop", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '"}}'
            )
        )
        server.drain_pending_messages()

        reconnect_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "reconnect_session", '
                '"request_id": "req-reconnect", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '"}}'
            )
        )
        reconnect_envelopes = [parse_envelope(message) for message in reconnect_messages]
        self.assertFalse(reconnect_envelopes[0].ok)
        self.assertEqual("session_stopped", reconnect_envelopes[0].error["code"])

    def test_reconnect_expired_session_returns_session_expired(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        session_id, _client_id = self.start_and_bind(server)

        server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "disconnect_session", '
                '"request_id": "req-disconnect", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "reason": "transport_lost"}}'
            )
        )
        session = server._store.get_by_notebook("nb-1")
        self.assertIsNotNone(session)
        session.expires_at = 0

        reconnect_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "reconnect_session", '
                '"request_id": "req-reconnect", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '"}}'
            )
        )
        reconnect_envelopes = [parse_envelope(message) for message in reconnect_messages]
        self.assertFalse(reconnect_envelopes[0].ok)
        self.assertEqual("session_expired", reconnect_envelopes[0].error["code"])

    def test_poll_session_timeouts_stops_disconnected_session(self) -> None:
        runtime = InMemoryKernelRuntime()
        server = ProtocolServer(runtime=runtime)
        session_id, _client_id = self.start_and_bind(server)

        server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "disconnect_session", '
                '"request_id": "req-disconnect", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "reason": "transport_lost"}}'
            )
        )
        session = server._store.get_by_notebook("nb-1")
        self.assertIsNotNone(session)
        session.expires_at = 0

        should_exit = server.poll_session_timeouts()

        self.assertTrue(should_exit)
        self.assertEqual([], runtime.list_clients(session_id))
        self.assertEqual("stopped", session.state)
        self.assertEqual("timeout", session.last_action)

    def test_poll_frontend_health_emits_healthcheck_for_connected_session(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        session_id, _client_id = self.start_and_bind(server)
        session = server._store.get_by_notebook("nb-1")
        self.assertIsNotNone(session)
        session.frontend_last_ack_at = 0

        server.poll_frontend_health()

        messages = server.drain_pending_messages()
        envelopes = [parse_envelope(message) for message in messages]
        self.assertEqual(["healthcheck"], [envelope.type for envelope in envelopes])
        self.assertEqual("nb-1", envelopes[0].payload["notebook_id"])
        self.assertEqual(session_id, envelopes[0].payload["session_id"])
        self.assertTrue(envelopes[0].payload["healthcheck_id"].startswith("hc-"))
        self.assertEqual(envelopes[0].payload["healthcheck_id"], session.frontend_healthcheck_id)
        self.assertGreater(session.frontend_healthcheck_deadline, time.time())

    def test_healthcheck_reply_clears_outstanding_check(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        session_id, _client_id = self.start_and_bind(server)
        session = server._store.get_by_notebook("nb-1")
        self.assertIsNotNone(session)
        session.frontend_last_ack_at = 0

        server.poll_frontend_health()
        healthcheck = parse_envelope(server.drain_pending_messages()[0])

        reply_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "healthcheck_reply", '
                '"request_id": "req-healthcheck", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "healthcheck_id": "'
                + healthcheck.payload["healthcheck_id"]
                + '"}}'
            )
        )
        reply = [parse_envelope(message) for message in reply_messages][0]
        self.assertTrue(reply.ok)
        self.assertEqual("", session.frontend_healthcheck_id)
        self.assertIsNone(session.frontend_healthcheck_deadline)
        self.assertGreater(session.frontend_last_ack_at, 0)

    def test_poll_frontend_health_disconnects_when_reply_times_out(self) -> None:
        runtime = InMemoryKernelRuntime()
        server = ProtocolServer(runtime=runtime)
        session_id, _client_id = self.start_and_bind(server)
        session = server._store.get_by_notebook("nb-1")
        self.assertIsNotNone(session)
        session.frontend_healthcheck_id = "hc-stale"
        session.frontend_healthcheck_deadline = 0

        server.poll_frontend_health()

        messages = server.drain_pending_messages()
        envelopes = [parse_envelope(message) for message in messages]
        self.assertEqual(["session_updated"], [envelope.type for envelope in envelopes])
        self.assertEqual("disconnected", session.state)
        self.assertEqual("frontend_unreachable", session.last_error)
        self.assertIsNotNone(session.expires_at)
        self.assertEqual([], runtime.list_clients(session_id))

    def test_poll_frontend_health_disconnect_timeout_removes_active_client_controllers(self) -> None:
        runtime = InMemoryKernelRuntime()
        server = ProtocolServer(runtime=runtime)
        session_id, _client_id = self.start_and_bind(server)
        session = server._store.get_by_notebook("nb-1")
        self.assertIsNotNone(session)
        session.frontend_healthcheck_id = "hc-stale"
        session.frontend_healthcheck_deadline = 0

        with patch.object(server._active_client_controllers, "remove_session") as remove_session:
            server.poll_frontend_health()

        remove_session.assert_called_once_with(session_id)

    def test_poll_client_updates_emits_revision_invalidation_event(self) -> None:
        runtime = InMemoryKernelRuntime()
        server = ProtocolServer(runtime=runtime)
        session_id, client_id = self.start_and_bind(server)

        server.poll_client_updates()
        self.assertEqual([], server.drain_pending_messages())

        session = server._store.get_by_notebook("nb-1")
        self.assertIsNotNone(session)
        runtime.append_client_execution_event(
            session,
            client_id,
            {"type": "execution_state", "status": "busy"},
        )

        server.poll_client_updates()

        messages = server.drain_pending_messages()
        envelopes = [parse_envelope(message) for message in messages]
        self.assertEqual(["client_updated"], [envelope.type for envelope in envelopes])
        self.assertEqual("nb-1", envelopes[0].payload["notebook_id"])
        self.assertEqual(session_id, envelopes[0].payload["session_id"])
        self.assertEqual(client_id, envelopes[0].payload["client_id"])
        self.assertGreater(envelopes[0].payload["revision"], 0)

    def test_stop_session_interrupts_active_cells(self) -> None:
        runtime = InMemoryKernelRuntime()
        server = ProtocolServer(runtime=runtime)
        session_id, _client_id = self.start_and_bind(server)

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
        self.assertEqual("interrupted", stop_envelopes[2].payload["cell"]["status"])
        self.assertEqual("unknown", stop_envelopes[2].payload["cell"]["owner"]["kind"])
        self.assertEqual("shutdown", stop_envelopes[2].payload["cell"]["client_state"])
        self.assertNotIn("runtime_mode", stop_envelopes[2].payload["cell"])
        self.assertNotIn("client_id", stop_envelopes[2].payload["cell"])
        pending_messages = server.drain_pending_messages()
        pending_envelopes = [parse_envelope(message) for message in pending_messages]
        self.assertEqual("stopped", pending_envelopes[0].payload["session"]["state"])
        self.assertEqual([], runtime.list_clients(session_id))

    def test_stop_session_clears_handler_runtime_identity_for_followup_cell(self) -> None:
        runtime = InMemoryKernelRuntime()
        server = ProtocolServer(runtime=runtime)
        session_id, _client_id = self.start_and_bind(server)

        server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "magic", "syntax": "python", "main_lines": ["%%vd", "pods"]}}}'
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
        self.assertEqual("interrupted", stop_envelopes[2].payload["cell"]["status"])
        self.assertEqual("unknown", stop_envelopes[2].payload["cell"]["owner"]["kind"])
        self.assertEqual("shutdown", stop_envelopes[2].payload["cell"]["client_state"])
        self.assertNotIn("runtime_mode", stop_envelopes[2].payload["cell"])
        self.assertNotIn("client_id", stop_envelopes[2].payload["cell"])

    def test_reused_notebook_target_switch_does_not_leak_plugin_attach_env_into_plain_code_client(self) -> None:
        runtime = InMemoryKernelRuntime()
        server = ProtocolServer(runtime=runtime)
        first_session_id, _client_id = self.start_and_bind(server, notebook_id="nb-1")

        server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-plugin", "payload": {"notebook_id": "nb-1", "session_id": "'
                + first_session_id
                + '", "cell": {"id": 12, "kind": "magic", "syntax": "python", "main_lines": ["%%vd", "pods"]}}}'
            )
        )
        server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "stop_session", '
                '"request_id": "req-stop", "payload": {"notebook_id": "nb-1", "session_id": "'
                + first_session_id
                + '"}}'
            )
        )
        server.drain_pending_messages()

        second_session_id, _client_id = self.start_and_bind(server, notebook_id="nb-1")
        execute_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-code", "payload": {"notebook_id": "nb-1", "session_id": "'
                + second_session_id
                + '", "cell": {"id": 13, "kind": "code", "syntax": "python", "keep_running": true, "main_lines": ["while True: pass"]}}}'
            )
        )
        execute_envelopes = [parse_envelope(message) for message in execute_messages]
        plain_client_id = execute_envelopes[3].payload["cell"]["client_id"]

        inspect_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "inspect_client", '
                '"request_id": "req-inspect-plain", "payload": {"notebook_id": "nb-1", "session_id": "'
                + second_session_id
                + '", "client_id": "'
                + plain_client_id
                + '"}}'
            )
        )
        inspect_envelopes = [parse_envelope(message) for message in inspect_messages]
        self.assertTrue(inspect_envelopes[0].ok)
        transport_env = inspect_envelopes[0].payload["client"]["transport"]["attach_env"]

        self.assertEqual("transcript", transport_env["JUSI_TERMINAL_MODE"])
        self.assertNotIn("JUSI_PLUGIN_RUNTIME_CONTROL_SOCKET", transport_env)
        self.assertNotIn("JUSI_PLUGIN_RUNTIME_PID_FILE", transport_env)
        self.assertNotIn("JUSI_PLUGIN_FRONTEND_ACTIONS_FILE", transport_env)

    def test_stop_session_returns_error_response_for_unexpected_backend_failure(self) -> None:
        runtime = InMemoryKernelRuntime()
        server = ProtocolServer(runtime=runtime)
        session_id, _client_id = self.start_and_bind(server)

        with patch("jusi.application.use_cases.StopSession.begin_stop", side_effect=RuntimeError("stop exploded")):
            stop_messages = server.handle_message(
                (
                    '{"version": 1, "kind": "request", "type": "stop_session", '
                    '"request_id": "req-3", "payload": {"notebook_id": "nb-1", "session_id": "'
                    + session_id
                    + '"}}'
                )
            )

        stop_envelopes = [parse_envelope(message) for message in stop_messages]
        self.assertFalse(stop_envelopes[0].ok)
        self.assertEqual("internal_error", stop_envelopes[0].error["code"])
        self.assertEqual("stop exploded", stop_envelopes[0].error["message"])

    def test_handle_message_ignores_malformed_json(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        self.addCleanup(server.close)
        self.assertEqual([], server.handle_message("not json"))

    def test_handle_message_returns_invalid_request_for_bad_version(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        self.addCleanup(server.close)

        messages = server.handle_message(
            '{"version": "bad", "kind": "request", "type": "start_session", "request_id": "req-1", "payload": {}}'
        )
        envelope = parse_envelope(messages[0])
        self.assertFalse(envelope.ok)
        self.assertEqual("invalid_request", envelope.error["code"])

    def test_stop_session_marks_failed_when_background_stop_raises(self) -> None:
        runtime = ExplodingStopRuntime()
        server = ProtocolServer(runtime=runtime)
        session_id, _client_id = self.start_and_bind(server)

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

        pending_messages: list[str] = []
        deadline = time.time() + 1.0
        while time.time() < deadline and not pending_messages:
            pending_messages = server.drain_pending_messages()
            if not pending_messages:
                time.sleep(0.02)
        self.assertTrue(pending_messages)
        pending_envelopes = [parse_envelope(message) for message in pending_messages]
        self.assertEqual("failed", pending_envelopes[0].payload["session"]["state"])
        self.assertEqual("stop failed", pending_envelopes[0].payload["session"]["last_error"])

    def test_background_execute_does_not_overwrite_interrupted_cell_after_stop(self) -> None:
        runtime = BlockingBackgroundExecuteRuntime()
        server = ProtocolServer(runtime=runtime)
        session_id, _client_id = self.start_and_bind(server)

        execute_messages = server.handle_message(
            (
                '{"version": 1, "kind": "request", "type": "execute_cell", '
                '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "'
                + session_id
                + '", "cell": {"id": 12, "kind": "code", "syntax": "python", "main_lines": ["print(1)"]}}}'
            )
        )
        execute_envelopes = [parse_envelope(message) for message in execute_messages]
        self.assertTrue(runtime.started.wait(timeout=1.0))
        active_client_id = execute_envelopes[2].payload["cell"]["client_id"]

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
        self.assertEqual("interrupted", stop_envelopes[2].payload["cell"]["status"])

        runtime.release.set()
        deadline = time.time() + 1.0
        while time.time() < deadline:
            server.drain_pending_messages()
            if runtime.seen_client_ids:
                break
            execution = server._store.get_execution("nb-1", 12)
            if execution is not None and execution.status == "interrupted":
                time.sleep(0.02)
                continue
            time.sleep(0.02)

        execution = server._store.get_execution("nb-1", 12)
        self.assertIsNotNone(execution)
        self.assertEqual("interrupted", execution.status)  # type: ignore[union-attr]
        self.assertEqual("shutdown", execution.client_state)  # type: ignore[union-attr]
        self.assertEqual([active_client_id], runtime.seen_client_ids)


if __name__ == "__main__":
    unittest.main()
