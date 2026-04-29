from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from jusi.domain.models import ExecutableCell
from jusi.infrastructure.client_runtime_handshake import ClientRuntimeReadyMessage
from jusi.infrastructure.client_runtime_constants import JUSI_CLIENT_RUNTIME_MODE_ENV
from jusi.infrastructure.client_runtime_startup import load_transcript_runtime_startup_from_env
from jusi.infrastructure.client_runtime_session import ClientRuntimeSession
from jusi.infrastructure.client_runtime_updates import ClientRuntimeUpdate
from jusi.infrastructure.runtime_runner_base import RuntimeRunnerBase
from jusi.infrastructure.transcript_runtime_session import TranscriptRuntimeSession


class ClientRuntimeHost(Protocol):
    def run(self) -> int:
        ...


ClientRuntimeHostFactory = Callable[[], ClientRuntimeHost]
SessionFactory = Callable[["ConfiguredSessionRuntimeHost"], ClientRuntimeSession]
Hook = Callable[["ConfiguredSessionRuntimeHost"], None]


@dataclass(frozen=True)
class RuntimeTransitionRequest:
    notebook_id: str
    session_id: str
    client_id: str
    cell_id: int
    handler_id: str
    magic_name: str
    cell: ExecutableCell
    content: str
    meta: dict[str, object]
    handle_runtime_update: Callable[[ClientRuntimeUpdate], None]
    invoke_backend_action: Callable[[str, dict[str, Any]], dict[str, Any]]
    on_exit: Callable[[str], None]

RuntimeTransitionFactory = Callable[[RuntimeTransitionRequest], object]


class SessionRuntimeHost(RuntimeRunnerBase):
    def _prepare_run(self) -> None:
        return None

    def _build_session(self) -> ClientRuntimeSession:
        raise NotImplementedError

    def _finalize_run(self) -> None:
        return None

    def run(self) -> int:
        self._prepare_run()
        try:
            return self._build_session().run()
        finally:
            self._finalize_run()


@dataclass(frozen=True)
class RuntimeModeSpec:
    mode_name: str
    session_factory: SessionFactory
    prepare_run: Hook | None = None
    finalize_run: Hook | None = None


class ConfiguredSessionRuntimeHost(SessionRuntimeHost):
    def __init__(self, *, spec: RuntimeModeSpec) -> None:
        super().__init__()
        self._spec = spec

    def _build_session(self) -> ClientRuntimeSession:
        return self._spec.session_factory(self)

    def _prepare_run(self) -> None:
        if self._spec.prepare_run is not None:
            self._spec.prepare_run(self)

    def _finalize_run(self) -> None:
        if self._spec.finalize_run is not None:
            self._spec.finalize_run(self)


class TranscriptSessionRuntimeHost(ConfiguredSessionRuntimeHost):
    def __init__(self) -> None:
        super().__init__(
            spec=RuntimeModeSpec(
                mode_name="transcript",
                session_factory=lambda host: host._session,
                prepare_run=lambda host: host._prepare_transcript_run(),
                finalize_run=lambda host: host._finalize_transcript_run(),
            )
        )
        startup = load_transcript_runtime_startup_from_env()
        self._supervisor_pid = startup.supervisor_pid
        self._session = TranscriptRuntimeSession(
            client_id=startup.client_id,
            notebook_id=startup.notebook_id,
            session_id=startup.session_id,
            control_dir=startup.control_dir,
            keep_running=lambda: self._running,
            supervisor_is_alive=self._runtime_supervisor_is_alive,
            request_shutdown=self._mark_shutdown,
            poll_interval_seconds=0.01,
        )

    def _runtime_supervisor_is_alive(self) -> bool:
        return self._supervisor_is_alive(self._supervisor_pid, require_same_parent=True)

    def _mark_shutdown(self, reason: str) -> None:
        self._session.mark_shutdown(reason)
        self._running = False

    def _prepare_transcript_run(self) -> None:
        self._install_signal_handlers()
        sys.stdout.write(ClientRuntimeReadyMessage(mode="transcript").to_wire() + "\n")
        sys.stdout.flush()

    def _finalize_transcript_run(self) -> None:
        if self._session.state.shutdown_reason:
            self._running = False


def build_transcript_runtime_host_factory() -> ClientRuntimeHostFactory:
    return TranscriptSessionRuntimeHost


def build_handler_runtime_transition_factory() -> RuntimeTransitionFactory:
    def _factory(request: RuntimeTransitionRequest) -> object:
        from jusi.infrastructure.handler_worker_startup import HandlerWorkerStartup
        from jusi.infrastructure.inprocess_handler_controller import InProcessHandlerController

        return InProcessHandlerController(
            startup=HandlerWorkerStartup(
                notebook_id=request.notebook_id,
                session_id=request.session_id,
                client_id=request.client_id,
                cell_id=request.cell_id,
                handler_id=request.handler_id,
                magic_name=request.magic_name,
                cell=request.cell,
                content=request.content,
                meta=dict(request.meta),
            ),
            handle_runtime_update=request.handle_runtime_update,
            invoke_backend_action=request.invoke_backend_action,
            on_exit=request.on_exit,
        )

    return _factory


@dataclass(frozen=True)
class RegisteredRuntimeMode:
    name: str
    owner_kind: str
    accepts_frontend_messages: bool
    requires_handler_id: bool
    uses_live_controller: bool
    host_factory: ClientRuntimeHostFactory | None = None
    allowed_transition_sources: tuple[str, ...] = ()
    transition_factory: RuntimeTransitionFactory | None = None

    def supports_direct_launch(self) -> bool:
        return self.owner_kind == "kernel" and not self.uses_live_controller

    def build_transition_controller(
        self,
        *,
        notebook_id: str,
        session_id: str,
        client_id: str,
        cell_id: int,
        handler_id: str,
        magic_name: str,
        cell: ExecutableCell,
        content: str,
        meta: dict[str, object],
        handle_runtime_update: Callable[[ClientRuntimeUpdate], None],
        invoke_backend_action: Callable[[str, dict[str, Any]], dict[str, Any]],
        on_exit: Callable[[str], None],
    ) -> object:
        if self.transition_factory is None:
            raise ValueError(f"Runtime mode {self.name} does not provide a transition factory")
        return self.transition_factory(
            RuntimeTransitionRequest(
                notebook_id=notebook_id,
                session_id=session_id,
                client_id=client_id,
                cell_id=cell_id,
                handler_id=handler_id,
                magic_name=magic_name,
                cell=cell,
                content=content,
                meta=dict(meta),
                handle_runtime_update=handle_runtime_update,
                invoke_backend_action=invoke_backend_action,
                on_exit=on_exit,
            )
        )


def build_registered_runtime_modes() -> tuple[RegisteredRuntimeMode, ...]:
    return (
        RegisteredRuntimeMode(
            "transcript",
            owner_kind="kernel",
            accepts_frontend_messages=False,
            requires_handler_id=False,
            uses_live_controller=False,
            allowed_transition_sources=(),
            host_factory=build_transcript_runtime_host_factory(),
            transition_factory=None,
        ),
        RegisteredRuntimeMode(
            "handler",
            owner_kind="handler",
            accepts_frontend_messages=True,
            requires_handler_id=True,
            uses_live_controller=True,
            allowed_transition_sources=("transcript",),
            host_factory=None,
            transition_factory=build_handler_runtime_transition_factory(),
        ),
    )


def registered_runtime_mode_names() -> tuple[str, ...]:
    return tuple(mode.name for mode in build_registered_runtime_modes())


def get_registered_runtime_mode(name: str) -> RegisteredRuntimeMode | None:
    normalized = str(name).strip().lower()
    for mode in build_registered_runtime_modes():
        if mode.name == normalized:
            return mode
    return None


def require_registered_runtime_mode(name: str) -> RegisteredRuntimeMode:
    mode = get_registered_runtime_mode(name)
    if mode is None:
        raise ValueError(f"Unknown runtime mode: {name}")
    return mode


def default_launch_runtime_mode() -> RegisteredRuntimeMode:
    launchable = [mode for mode in build_registered_runtime_modes() if mode.supports_direct_launch()]
    if not launchable:
        raise ValueError("No registered runtime mode supports launch operations")
    if len(launchable) != 1:
        raise ValueError("Default launch runtime mode is ambiguous")
    return launchable[0]


def default_transition_target_runtime_mode(source_mode_name: str) -> RegisteredRuntimeMode:
    source_name = require_registered_runtime_mode(source_mode_name).name
    transitionable = [
        mode
        for mode in build_registered_runtime_modes()
        if source_name in mode.allowed_transition_sources
    ]
    if not transitionable:
        raise ValueError(f"No registered runtime mode allows transition from {source_name}")
    if len(transitionable) != 1:
        raise ValueError(f"Transition target runtime mode from {source_name} is ambiguous")
    return transitionable[0]


def build_runtime_host_factories(
    modes: tuple[RegisteredRuntimeMode, ...] | None = None,
) -> dict[str, ClientRuntimeHostFactory]:
    return {
        mode.name: mode.host_factory
        for mode in (modes or build_registered_runtime_modes())
        if mode.host_factory is not None
    }


def build_default_runtime_host_factories() -> dict[str, ClientRuntimeHostFactory]:
    return build_runtime_host_factories()


def run_client_runtime_mode(
    mode: str,
    *,
    host_factories: dict[str, ClientRuntimeHostFactory] | None = None,
) -> int:
    factories = host_factories or build_default_runtime_host_factories()
    factory = factories.get(str(mode).strip().lower())
    if factory is None:
        sys.stderr.write(f"unsupported {JUSI_CLIENT_RUNTIME_MODE_ENV}: {mode}\n")
        sys.stderr.flush()
        return 2
    return factory().run()
