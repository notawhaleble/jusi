from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from jusi.domain.models import ProcessDiagnostics


@dataclass(frozen=True)
class KernelOutput:
    output_kind: str
    media_type: str
    data: str


@dataclass(frozen=True)
class KernelExecutionResult:
    outcome: str
    outputs: tuple[KernelOutput, ...] = field(default_factory=tuple)
    error_name: str = ""
    error_value: str = ""
    handoffs: tuple[PluginHandoff, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PluginHandoff:
    plugin_id: str
    plugin_version: str
    family_id: str
    magic_name: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": 1,
            "kind": "plugin.handoff",
            "plugin_id": self.plugin_id,
            "plugin_version": self.plugin_version,
            "family_id": self.family_id,
            "magic_name": self.magic_name,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class KernelAdapterSpec:
    plugin_id: str
    plugin_version: str
    module: str
    families: tuple[tuple[str, str], ...]


class KernelAdapterError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        layer: str,
        reason: str,
        retryable: bool,
        diagnostics: ProcessDiagnostics | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.layer = layer
        self.reason = reason
        self.retryable = retryable
        self.diagnostics = diagnostics
        self.details = details or {}


class KernelHandle(Protocol):
    @property
    def pid(self) -> int | None: ...

    def execute(self, code: str, *, timeout: float) -> KernelExecutionResult: ...

    def stop(self, *, timeout: float) -> None: ...


class KernelFactory(Protocol):
    def start(
        self,
        kernel_name: str,
        *,
        timeout: float,
        adapters: tuple[KernelAdapterSpec, ...] = (),
    ) -> KernelHandle: ...


@dataclass(frozen=True)
class PluginCatalogDiscoveryResult:
    catalog: dict[str, Any]
    process: ProcessDiagnostics


class PluginCatalogDiscoveryError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        reason: str,
        retryable: bool,
        diagnostics: ProcessDiagnostics | None = None,
        entry_point: str = "",
        distribution: str = "",
    ) -> None:
        super().__init__(message)
        self.layer = "plugin_discovery"
        self.operation = "discover_plugins"
        self.reason = reason
        self.retryable = retryable
        self.diagnostics = diagnostics
        self.entry_point = entry_point
        self.distribution = distribution


class PluginCatalogDiscovery(Protocol):
    def discover(self, *, discovery_id: str, timeout: float) -> PluginCatalogDiscoveryResult: ...


@dataclass(frozen=True)
class PluginWorkerSpec:
    plugin_worker_id: str
    runtime_id: str
    plugin_id: str
    family_id: str
    client_id: str
    execution_id: str
    entry_point: str


class PluginWorkerError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        reason: str,
        retryable: bool,
        diagnostics: ProcessDiagnostics | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.layer = "plugin_worker"
        self.reason = reason
        self.retryable = retryable
        self.diagnostics = diagnostics
        self.details = details or {}


@dataclass(frozen=True)
class TerminalSurfaceRequest:
    request_id: str
    argv: tuple[str, ...]
    cwd: str | None = None
    environment_overrides: dict[str, str] = field(default_factory=dict)
    capabilities: tuple[str, ...] = ("input", "resize")

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "kind": "terminal_surface.create",
            "argv": list(self.argv),
            "cwd": self.cwd,
            "environment_overrides": dict(self.environment_overrides),
            "capabilities": list(self.capabilities),
        }


@dataclass(frozen=True)
class PluginWorkerOperationResult:
    result: dict[str, Any]
    core_requests: tuple[TerminalSurfaceRequest, ...] = field(default_factory=tuple)


class PluginWorkerHandle(Protocol):
    @property
    def pid(self) -> int | None: ...

    def request(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        trace_id: str,
        timeout: float,
    ) -> PluginWorkerOperationResult: ...

    def stop(self, *, trace_id: str, timeout: float) -> str: ...


class PluginWorkerFactory(Protocol):
    def start(self, spec: PluginWorkerSpec, *, timeout: float) -> PluginWorkerHandle: ...


@dataclass(frozen=True)
class TerminalLaunchSpec:
    argv: tuple[str, ...]
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)


class TerminalBrokerError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        reason: str,
        retryable: bool,
        diagnostics: ProcessDiagnostics | None = None,
    ) -> None:
        super().__init__(message)
        self.layer = "client"
        self.reason = reason
        self.retryable = retryable
        self.diagnostics = diagnostics


class TerminalHandle(Protocol):
    @property
    def pid(self) -> int | None: ...

    @property
    def running(self) -> bool: ...

    @property
    def diagnostics(self) -> ProcessDiagnostics: ...

    def read(self, *, timeout: float, maximum: int = 65536) -> bytes: ...

    def write(self, data: bytes) -> None: ...

    def resize(self, *, rows: int, cols: int) -> None: ...

    def stop(self, *, timeout: float) -> str: ...


class TerminalBroker(Protocol):
    def start(
        self,
        spec: TerminalLaunchSpec,
        *,
        rows: int,
        cols: int,
    ) -> TerminalHandle: ...
