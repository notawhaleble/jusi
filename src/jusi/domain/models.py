from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


KernelState = Literal["off", "on"]
OperationOutcome = Literal["pending", "running", "succeeded", "failed", "cancelled"]
ExecutionOutcome = Literal["pending", "running", "succeeded", "failed", "interrupted", "cancelled"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ResourceRef:
    kind: str
    resource_id: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.resource_id}


@dataclass(frozen=True)
class ProcessDiagnostics:
    pid: int | None = None
    exit_code: int | None = None
    signal: int | str | None = None
    stderr_excerpt: str = ""
    stderr_truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"stderr_truncated": self.stderr_truncated}
        if self.pid is not None:
            result["pid"] = self.pid
        if self.exit_code is not None:
            result["exit_code"] = self.exit_code
        if self.signal is not None:
            result["signal"] = self.signal
        if self.stderr_excerpt:
            result["stderr_excerpt"] = self.stderr_excerpt
        return result


@dataclass(frozen=True)
class Failure:
    failure_id: str
    trace_id: str
    layer: str
    operation: str
    reason: str
    message: str
    retryable: bool
    scope: str
    resource: ResourceRef
    occurred_at: str = field(default_factory=utc_now)
    process: ProcessDiagnostics | None = None
    caused_by_failure_id: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "failure_id": self.failure_id,
            "trace_id": self.trace_id,
            "layer": self.layer,
            "operation": self.operation,
            "reason": self.reason,
            "message": self.message,
            "retryable": self.retryable,
            "scope": self.scope,
            "resource": self.resource.to_dict(),
            "occurred_at": self.occurred_at,
        }
        if self.process is not None:
            result["process"] = self.process.to_dict()
        if self.caused_by_failure_id:
            result["caused_by_failure_id"] = self.caused_by_failure_id
        if self.details:
            result["details"] = dict(self.details)
        return result


@dataclass
class KernelResource:
    kernel_id: str
    notebook_id: str
    kernel_name: str
    state: KernelState
    pid: int | None = None
    started_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kernel_id": self.kernel_id,
            "notebook_id": self.notebook_id,
            "kernel_name": self.kernel_name,
            "state": self.state,
            "pid": self.pid,
            "started_at": self.started_at,
        }


@dataclass(frozen=True)
class NotebookRuntime:
    runtime_id: str
    notebook_id: str
    discovery_id: str
    kernel_id: str
    plugin_catalog: dict[str, Any]
    palette: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_id": self.runtime_id,
            "notebook_id": self.notebook_id,
            "discovery_id": self.discovery_id,
            "kernel_id": self.kernel_id,
            "plugin_catalog": dict(self.plugin_catalog),
            "palette": dict(self.palette),
        }


@dataclass
class Operation:
    operation_id: str
    trace_id: str
    kind: str
    outcome: OperationOutcome = "running"
    started_at: str = field(default_factory=utc_now)
    completed_at: str | None = None

    def complete(self, outcome: OperationOutcome) -> None:
        self.outcome = outcome
        self.completed_at = utc_now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "trace_id": self.trace_id,
            "kind": self.kind,
            "outcome": self.outcome,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass
class ExecutionResource:
    execution_id: str
    kernel_id: str
    notebook_id: str
    cell_id: str
    client_id: str | None = None
    outcome: ExecutionOutcome = "running"
    started_at: str = field(default_factory=utc_now)
    completed_at: str | None = None

    def complete(self, outcome: ExecutionOutcome) -> None:
        self.outcome = outcome
        self.completed_at = utc_now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "kernel_id": self.kernel_id,
            "notebook_id": self.notebook_id,
            "cell_id": self.cell_id,
            "client_id": self.client_id,
            "outcome": self.outcome,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass
class PluginWorkerResource:
    plugin_worker_id: str
    runtime_id: str
    plugin_id: str
    plugin_version: str
    family_id: str
    client_id: str
    execution_id: str
    capabilities: tuple[str, ...]
    interaction: str
    pid: int | None = None
    started_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_worker_id": self.plugin_worker_id,
            "runtime_id": self.runtime_id,
            "plugin_id": self.plugin_id,
            "plugin_version": self.plugin_version,
            "family_id": self.family_id,
            "client_id": self.client_id,
            "execution_id": self.execution_id,
            "capabilities": list(self.capabilities),
            "interaction": self.interaction,
            "pid": self.pid,
            "started_at": self.started_at,
        }


@dataclass(frozen=True)
class ClientResource:
    client_id: str
    runtime_id: str
    kernel_id: str
    notebook_id: str
    cell_id: str
    execution_id: str
    plugin_worker_id: str
    plugin_id: str
    plugin_version: str
    family_id: str
    capabilities: tuple[str, ...]
    interaction: str
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "runtime_id": self.runtime_id,
            "kernel_id": self.kernel_id,
            "notebook_id": self.notebook_id,
            "cell_id": self.cell_id,
            "execution_id": self.execution_id,
            "plugin_worker_id": self.plugin_worker_id,
            "plugin_id": self.plugin_id,
            "plugin_version": self.plugin_version,
            "family_id": self.family_id,
            "capabilities": list(self.capabilities),
            "interaction": self.interaction,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class SurfaceResource:
    surface_id: str
    client_id: str
    runtime_id: str
    kind: str
    capabilities: tuple[str, ...]
    endpoint: str
    subprotocol: str = "jusi.terminal.v1"
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface_id": self.surface_id,
            "client_id": self.client_id,
            "runtime_id": self.runtime_id,
            "kind": self.kind,
            "capabilities": list(self.capabilities),
            "transport": {
                "kind": "websocket",
                "endpoint": self.endpoint,
                "subprotocol": self.subprotocol,
            },
            "created_at": self.created_at,
        }
