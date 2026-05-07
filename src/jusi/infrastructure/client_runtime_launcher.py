from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from jusi.domain.models import ExecutableCell
from jusi.infrastructure.client_runtime_controller import ActiveClientController, LiveClientControllerRegistry
from jusi.infrastructure.client_runtime_host import (
    default_transition_target_runtime_mode,
    require_registered_runtime_mode,
)
from jusi.infrastructure.client_runtime_updates import ClientRuntimeUpdate


class RuntimeClientStarter(Protocol):
    def __call__(self, notebook_id: str, cell_id: int, initial_status: str) -> str:
        ...


@dataclass(frozen=True)
class HandlerClientCallbacks:
    handle_runtime_update: Callable[[ClientRuntimeUpdate], None]
    invoke_backend_action: Callable[[str, dict[str, Any]], dict[str, Any]]
    on_exit: Callable[[str], None]


@dataclass(frozen=True)
class RuntimeClientOperationResult:
    client_id: str
    status: str = ""


@dataclass(frozen=True)
class RuntimeClientRegistration:
    runtime_mode: str
    session_id: str
    client_id: str
    controller: object
    handler_id: str = ""


@dataclass(frozen=True)
class RuntimeClientLaunchOperation:
    runtime_mode: str
    notebook_id: str
    session_id: str
    cell_id: int
    start_client: RuntimeClientStarter
    initial_status: str


@dataclass(frozen=True)
class RuntimeClientTransitionOperation:
    source_runtime_mode: str
    notebook_id: str
    session_id: str
    client_id: str
    cell_id: int
    handler_id: str
    magic_name: str
    cell: ExecutableCell
    content: str
    meta: dict[str, object]
    callbacks: HandlerClientCallbacks


class ClientRuntimeLauncher:
    def __init__(
        self,
        registry: LiveClientControllerRegistry,
    ) -> None:
        self._registry = registry

    def _register_runtime_client(self, registration: RuntimeClientRegistration) -> ActiveClientController:
        mode = require_registered_runtime_mode(registration.runtime_mode)
        normalized_mode = mode.name
        normalized_handler_id = str(registration.handler_id).strip()
        if mode.requires_handler_id and not normalized_handler_id:
            raise ValueError(f"Runtime mode {normalized_mode} requires handler_id")
        if not mode.requires_handler_id and normalized_handler_id:
            raise ValueError(f"Runtime mode {normalized_mode} does not allow handler_id")
        active = ActiveClientController(
            client_id=registration.client_id,
            controller=registration.controller,
            runtime_mode=normalized_mode,
            handler_id=normalized_handler_id,
        )
        if self._registry.get(registration.session_id, registration.client_id) is None:
            self._registry.register(registration.session_id, registration.client_id, active)
        else:
            self._registry.replace(registration.session_id, registration.client_id, active)
        return active

    def launch_runtime_client(self, operation: RuntimeClientLaunchOperation) -> RuntimeClientOperationResult:
        mode = require_registered_runtime_mode(operation.runtime_mode)
        if not mode.supports_direct_launch():
            raise ValueError(f"Runtime mode {mode.name} does not support direct launch")
        client_id = operation.start_client(
            operation.notebook_id,
            operation.cell_id,
            operation.initial_status,
        )
        return RuntimeClientOperationResult(client_id=client_id)

    def transition_runtime_client(self, operation: RuntimeClientTransitionOperation) -> RuntimeClientOperationResult:
        active = self._registry.get(operation.session_id, operation.client_id)
        source_mode = require_registered_runtime_mode(operation.source_runtime_mode)
        if active is None:
            if source_mode.uses_live_controller:
                raise ValueError("No active runtime client is registered for transition")
        elif active.runtime_mode != source_mode.name:
            raise ValueError("Active runtime client does not match expected source runtime mode")
        target_mode = default_transition_target_runtime_mode(source_mode.name)
        if source_mode.name == target_mode.name:
            raise ValueError("Runtime transition requires different source and target modes")
        if source_mode.name not in target_mode.allowed_transition_sources:
            raise ValueError(
                f"Runtime mode {target_mode.name} does not allow transition from {source_mode.name}"
            )
        normalized_handler_id = str(operation.handler_id).strip()
        if target_mode.requires_handler_id and not normalized_handler_id:
            raise ValueError(f"Runtime mode {target_mode.name} requires handler_id")
        if not target_mode.requires_handler_id and normalized_handler_id:
            raise ValueError(f"Runtime mode {target_mode.name} does not allow handler_id")
        controller = target_mode.build_transition_controller(
            notebook_id=operation.notebook_id,
            session_id=operation.session_id,
            client_id=operation.client_id,
            cell_id=operation.cell_id,
            handler_id=normalized_handler_id,
            magic_name=operation.magic_name,
            cell=operation.cell,
            content=operation.content,
            meta=operation.meta,
            handle_runtime_update=operation.callbacks.handle_runtime_update,
            invoke_backend_action=operation.callbacks.invoke_backend_action,
            on_exit=operation.callbacks.on_exit,
        )
        self._register_runtime_client(
            RuntimeClientRegistration(
                runtime_mode=target_mode.name,
                session_id=operation.session_id,
                client_id=operation.client_id,
                controller=controller,
                handler_id=normalized_handler_id,
            )
        )
        wait_started = getattr(controller, "wait_started", None)
        if not callable(wait_started):
            raise ValueError(
                f"Runtime mode {target_mode.name} transition controller does not support wait_started()"
            )
        return RuntimeClientOperationResult(client_id=operation.client_id, status=wait_started())
