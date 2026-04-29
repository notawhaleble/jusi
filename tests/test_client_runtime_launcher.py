import unittest
from unittest.mock import patch

from jusi.infrastructure.client_runtime_controller import ActiveClientController, LiveClientControllerRegistry
from jusi.infrastructure.client_runtime_launcher import (
    ClientRuntimeLauncher,
    HandlerClientCallbacks,
    RuntimeClientLaunchOperation,
    RuntimeClientTransitionOperation,
)
from jusi.infrastructure.client_runtime_host import RegisteredRuntimeMode, build_registered_runtime_modes
from jusi.domain.models import ExecutableCell


class _FakeWorker:
    def __init__(self) -> None:
        self.interrupted = False
        self.stopped = False

    def wait_started(self) -> str:
        return "follow-up"

    def on_frontend_message(self, _context, _message_type, _payload) -> None:
        return None

    def interrupt(self) -> None:
        self.interrupted = True

    def stop(self) -> None:
        self.stopped = True


class ClientRuntimeLauncherTest(unittest.TestCase):
    def test_launch_runtime_client_starts_and_registers_client(self) -> None:
        registry = LiveClientControllerRegistry()
        launcher = ClientRuntimeLauncher(registry)
        allocation_calls = []
        activation_calls = []
        status_calls = []

        launch = launcher.launch_runtime_client(
            RuntimeClientLaunchOperation(
                runtime_mode="transcript",
                notebook_id="nb-1",
                session_id="sess-1",
                cell_id=12,
                initial_status="busy",
                start_client=lambda notebook_id, cell_id, initial_status: (
                    allocation_calls.append((notebook_id, "sess-1"))
                    or activation_calls.append(("client-1", cell_id))
                    or status_calls.append(("client-1", initial_status))
                    or "client-1"
                ),
            )
        )
        client_id = launch.client_id

        self.assertEqual([("nb-1", "sess-1")], allocation_calls)
        self.assertEqual([("client-1", 12)], activation_calls)
        self.assertEqual([("client-1", "busy")], status_calls)
        self.assertEqual("client-1", client_id)
        self.assertIsNone(registry.get("sess-1", "client-1"))

    def test_launch_runtime_client_rejects_handler_mode_without_direct_launch(self) -> None:
        registry = LiveClientControllerRegistry()
        launcher = ClientRuntimeLauncher(registry)

        with self.assertRaisesRegex(ValueError, "does not support direct launch"):
            launcher.launch_runtime_client(
                RuntimeClientLaunchOperation(
                    runtime_mode="handler",
                    notebook_id="nb-1",
                    session_id="sess-1",
                    cell_id=12,
                    initial_status="busy",
                    start_client=lambda _notebook_id, _cell_id, _initial_status: "client-1",
                )
            )

    def test_transition_runtime_client_replaces_managed_controller_and_returns_status(self) -> None:
        registry = LiveClientControllerRegistry()
        fake_worker = _FakeWorker()

        patched_modes = []
        for mode in build_registered_runtime_modes():
            if mode.name == "handler":
                patched_modes.append(
                    RegisteredRuntimeMode(
                        name=mode.name,
                        owner_kind=mode.owner_kind,
                        accepts_frontend_messages=mode.accepts_frontend_messages,
                        requires_handler_id=mode.requires_handler_id,
                        uses_live_controller=mode.uses_live_controller,
                        allowed_transition_sources=mode.allowed_transition_sources,
                        host_factory=mode.host_factory,
                        transition_factory=lambda _request: fake_worker,
                    )
                )
            else:
                patched_modes.append(mode)

        with patch(
            "jusi.infrastructure.client_runtime_host.build_registered_runtime_modes",
            return_value=tuple(patched_modes),
        ):
            launcher = ClientRuntimeLauncher(registry)
            launcher.launch_runtime_client(
                RuntimeClientLaunchOperation(
                    runtime_mode="transcript",
                    notebook_id="nb-1",
                    session_id="sess-1",
                    cell_id=12,
                    initial_status="busy",
                    start_client=lambda _notebook_id, _cell_id, _initial_status: "client-1",
                )
            )
            takeover = launcher.transition_runtime_client(
                RuntimeClientTransitionOperation(
                    source_runtime_mode="transcript",
                    notebook_id="nb-1",
                    session_id="sess-1",
                    client_id="client-1",
                    cell_id=12,
                    handler_id="vd",
                    magic_name="vd",
                    cell=ExecutableCell(cell_id=12, kind="magic", syntax="python", main_lines=["%%vd"]),
                    content="",
                    meta={},
                    callbacks=HandlerClientCallbacks(
                        handle_runtime_update=lambda _update: None,
                        invoke_backend_action=lambda _name, _payload: {},
                        on_exit=lambda _status: None,
                    ),
                )
            )

        self.assertEqual("follow-up", takeover.status)
        active = registry.get("sess-1", "client-1")
        self.assertIsNotNone(active)
        self.assertEqual("handler", active.runtime_mode if active is not None else "")
        self.assertEqual("vd", active.handler_id if active is not None else "")

    def test_transition_runtime_client_requires_handler_id_for_handler_mode(self) -> None:
        registry = LiveClientControllerRegistry()
        launcher = ClientRuntimeLauncher(registry)
        launcher.launch_runtime_client(
            RuntimeClientLaunchOperation(
                runtime_mode="transcript",
                notebook_id="nb-1",
                session_id="sess-1",
                cell_id=12,
                initial_status="busy",
                start_client=lambda _notebook_id, _cell_id, _initial_status: "client-1",
            )
        )

        with self.assertRaisesRegex(ValueError, "requires handler_id"):
            launcher.transition_runtime_client(
                RuntimeClientTransitionOperation(
                    source_runtime_mode="transcript",
                    notebook_id="nb-1",
                    session_id="sess-1",
                    client_id="client-1",
                    cell_id=12,
                    handler_id="",
                    magic_name="vd",
                    cell=ExecutableCell(cell_id=12, kind="magic", syntax="python", main_lines=["%%vd"]),
                    content="",
                    meta={},
                    callbacks=HandlerClientCallbacks(
                        handle_runtime_update=lambda _update: None,
                        invoke_backend_action=lambda _name, _payload: {},
                        on_exit=lambda _status: None,
                    ),
                )
            )

    def test_transition_runtime_client_rejects_missing_transition_target(self) -> None:
        registry = LiveClientControllerRegistry()
        launcher = ClientRuntimeLauncher(registry)
        registry.register(
            "sess-1",
            "client-1",
            ActiveClientController(
                client_id="client-1",
                controller=object(),
                runtime_mode="handler",
                handler_id="vd",
            ),
        )

        with self.assertRaisesRegex(ValueError, "No registered runtime mode allows transition from handler"):
            launcher.transition_runtime_client(
                RuntimeClientTransitionOperation(
                    source_runtime_mode="handler",
                    notebook_id="nb-1",
                    session_id="sess-1",
                    client_id="client-1",
                    cell_id=12,
                    handler_id="vd",
                    magic_name="python",
                    cell=ExecutableCell(cell_id=12, kind="code", syntax="python", main_lines=["print(1)"]),
                    content="",
                    meta={},
                    callbacks=HandlerClientCallbacks(
                        handle_runtime_update=lambda _update: None,
                        invoke_backend_action=lambda _name, _payload: {},
                        on_exit=lambda _status: None,
                    ),
                )
            )

    def test_transition_runtime_client_rejects_mode_without_transition_factory(self) -> None:
        registry = LiveClientControllerRegistry()
        patched_modes = []
        for mode in build_registered_runtime_modes():
            if mode.name == "handler":
                patched_modes.append(
                    RegisteredRuntimeMode(
                        name=mode.name,
                        owner_kind=mode.owner_kind,
                        accepts_frontend_messages=mode.accepts_frontend_messages,
                        requires_handler_id=mode.requires_handler_id,
                        uses_live_controller=mode.uses_live_controller,
                        allowed_transition_sources=mode.allowed_transition_sources,
                        host_factory=mode.host_factory,
                        transition_factory=None,
                    )
                )
            else:
                patched_modes.append(mode)

        with patch(
            "jusi.infrastructure.client_runtime_host.build_registered_runtime_modes",
            return_value=tuple(patched_modes),
        ):
            launcher = ClientRuntimeLauncher(registry)
            launcher.launch_runtime_client(
                RuntimeClientLaunchOperation(
                    runtime_mode="transcript",
                    notebook_id="nb-1",
                    session_id="sess-1",
                    cell_id=12,
                    initial_status="busy",
                    start_client=lambda _notebook_id, _cell_id, _initial_status: "client-1",
                )
            )
            with self.assertRaisesRegex(ValueError, "does not provide a transition factory"):
                launcher.transition_runtime_client(
                    RuntimeClientTransitionOperation(
                        source_runtime_mode="transcript",
                        notebook_id="nb-1",
                        session_id="sess-1",
                        client_id="client-1",
                        cell_id=12,
                        handler_id="vd",
                        magic_name="vd",
                        cell=ExecutableCell(cell_id=12, kind="magic", syntax="python", main_lines=["%%vd"]),
                        content="",
                        meta={},
                        callbacks=HandlerClientCallbacks(
                            handle_runtime_update=lambda _update: None,
                            invoke_backend_action=lambda _name, _payload: {},
                            on_exit=lambda _status: None,
                        ),
                    )
                )

    def test_transition_runtime_client_requires_active_controller_for_handler_source(self) -> None:
        registry = LiveClientControllerRegistry()
        launcher = ClientRuntimeLauncher(registry)

        with self.assertRaisesRegex(ValueError, "No active runtime client is registered for transition"):
            launcher.transition_runtime_client(
                RuntimeClientTransitionOperation(
                    source_runtime_mode="handler",
                    notebook_id="nb-1",
                    session_id="sess-1",
                    client_id="client-1",
                    cell_id=12,
                    handler_id="vd",
                    magic_name="vd",
                    cell=ExecutableCell(cell_id=12, kind="magic", syntax="python", main_lines=["%%vd"]),
                    content="",
                    meta={},
                    callbacks=HandlerClientCallbacks(
                        handle_runtime_update=lambda _update: None,
                        invoke_backend_action=lambda _name, _payload: {},
                        on_exit=lambda _status: None,
                    ),
                )
            )

    def test_transition_runtime_client_rejects_disallowed_transition_edge(self) -> None:
        registry = LiveClientControllerRegistry()
        fake_worker = _FakeWorker()

        patched_modes = []
        for mode in build_registered_runtime_modes():
            if mode.name == "handler":
                patched_modes.append(
                    RegisteredRuntimeMode(
                        name=mode.name,
                        owner_kind=mode.owner_kind,
                        accepts_frontend_messages=mode.accepts_frontend_messages,
                        requires_handler_id=mode.requires_handler_id,
                        uses_live_controller=mode.uses_live_controller,
                        allowed_transition_sources=(),
                        host_factory=mode.host_factory,
                        transition_factory=lambda _request: fake_worker,
                    )
                )
            else:
                patched_modes.append(mode)

        with patch(
            "jusi.infrastructure.client_runtime_host.build_registered_runtime_modes",
            return_value=tuple(patched_modes),
        ):
            launcher = ClientRuntimeLauncher(registry)
            launcher.launch_runtime_client(
                RuntimeClientLaunchOperation(
                    runtime_mode="transcript",
                    notebook_id="nb-1",
                    session_id="sess-1",
                    cell_id=12,
                    initial_status="busy",
                    start_client=lambda _notebook_id, _cell_id, _initial_status: "client-1",
                )
            )
            with self.assertRaisesRegex(ValueError, "No registered runtime mode allows transition from transcript"):
                launcher.transition_runtime_client(
                    RuntimeClientTransitionOperation(
                        source_runtime_mode="transcript",
                        notebook_id="nb-1",
                        session_id="sess-1",
                        client_id="client-1",
                        cell_id=12,
                        handler_id="vd",
                        magic_name="vd",
                        cell=ExecutableCell(cell_id=12, kind="magic", syntax="python", main_lines=["%%vd"]),
                        content="",
                        meta={},
                        callbacks=HandlerClientCallbacks(
                            handle_runtime_update=lambda _update: None,
                            invoke_backend_action=lambda _name, _payload: {},
                            on_exit=lambda _status: None,
                        ),
                    )
                )

    def test_transition_runtime_client_uses_active_runtime_mode(self) -> None:
        registry = LiveClientControllerRegistry()
        fake_worker = _FakeWorker()

        patched_modes = []
        for mode in build_registered_runtime_modes():
            if mode.name == "handler":
                patched_modes.append(
                    RegisteredRuntimeMode(
                        name=mode.name,
                        owner_kind=mode.owner_kind,
                        accepts_frontend_messages=mode.accepts_frontend_messages,
                        requires_handler_id=mode.requires_handler_id,
                        uses_live_controller=mode.uses_live_controller,
                        allowed_transition_sources=mode.allowed_transition_sources,
                        host_factory=mode.host_factory,
                        transition_factory=lambda _request: fake_worker,
                    )
                )
            else:
                patched_modes.append(mode)

        with patch(
            "jusi.infrastructure.client_runtime_host.build_registered_runtime_modes",
            return_value=tuple(patched_modes),
        ):
            launcher = ClientRuntimeLauncher(registry)
            launcher.launch_runtime_client(
                RuntimeClientLaunchOperation(
                    runtime_mode="transcript",
                    notebook_id="nb-1",
                    session_id="sess-1",
                    cell_id=12,
                    initial_status="busy",
                    start_client=lambda _notebook_id, _cell_id, _initial_status: "client-1",
                )
            )
            takeover = launcher.transition_runtime_client(
                RuntimeClientTransitionOperation(
                    source_runtime_mode="transcript",
                    notebook_id="nb-1",
                    session_id="sess-1",
                    client_id="client-1",
                    cell_id=12,
                    handler_id="vd",
                    magic_name="vd",
                    cell=ExecutableCell(cell_id=12, kind="magic", syntax="python", main_lines=["%%vd"]),
                    content="",
                    meta={},
                    callbacks=HandlerClientCallbacks(
                        handle_runtime_update=lambda _update: None,
                        invoke_backend_action=lambda _name, _payload: {},
                        on_exit=lambda _status: None,
                    ),
                )
            )
            self.assertEqual("follow-up", takeover.status)


if __name__ == "__main__":
    unittest.main()
