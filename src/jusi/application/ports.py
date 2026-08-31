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


class KernelAdapterError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        layer: str,
        reason: str,
        retryable: bool,
        diagnostics: ProcessDiagnostics | None = None,
    ) -> None:
        super().__init__(message)
        self.layer = layer
        self.reason = reason
        self.retryable = retryable
        self.diagnostics = diagnostics


class KernelHandle(Protocol):
    @property
    def pid(self) -> int | None: ...

    def execute(self, code: str, *, timeout: float) -> KernelExecutionResult: ...

    def stop(self, *, timeout: float) -> None: ...


class KernelFactory(Protocol):
    def start(self, kernel_name: str, *, timeout: float) -> KernelHandle: ...


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
