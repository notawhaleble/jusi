from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

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
