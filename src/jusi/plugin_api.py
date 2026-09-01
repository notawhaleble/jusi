from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jusi.protocol import ProtocolValidationError, validate_plugin_catalog
from jusi.application.ports import TerminalSurfaceRequest


ENTRY_POINT_GROUP = "jusi.plugins.v1"


@dataclass(frozen=True)
class WorkerContext:
    plugin_worker_id: str
    runtime_id: str
    plugin_id: str
    family_id: str
    client_id: str
    execution_id: str


@dataclass(frozen=True)
class WorkerResult:
    result: dict[str, Any]
    core_requests: tuple[TerminalSurfaceRequest, ...] = ()


def terminal_surface(
    request_id: str,
    argv: tuple[str, ...],
    *,
    cwd: str | None = None,
    environment_overrides: dict[str, str] | None = None,
    signal: bool = False,
) -> TerminalSurfaceRequest:
    capabilities = ("input", "resize", "signal") if signal else ("input", "resize")
    return TerminalSurfaceRequest(
        request_id=request_id,
        argv=argv,
        cwd=cwd,
        environment_overrides=dict(environment_overrides or {}),
        capabilities=capabilities,
    )


def validate_discovered_entry(
    value: object,
    *,
    entry_point_name: str,
    distribution: str,
    distribution_version: str,
) -> dict[str, Any]:
    """Validate one provider result without importing any runtime references."""
    if not isinstance(value, dict):
        raise ProtocolValidationError("Plugin catalog provider must return an object")
    catalog = {
        "protocol_version": 1,
        "catalog_version": 1,
        "discovery_id": "entry_validation",
        "plugins": [value],
    }
    plugin = validate_plugin_catalog(catalog)["plugins"][0]
    if plugin["plugin_id"] != entry_point_name:
        raise ProtocolValidationError("plugin_id must equal the entry-point name")
    if plugin["distribution"] != distribution:
        raise ProtocolValidationError("distribution must match installed distribution metadata")
    if plugin["plugin_version"] != distribution_version:
        raise ProtocolValidationError("plugin_version must match installed distribution metadata")
    return dict(plugin)


def validate_catalog_claims(catalog: object) -> dict[str, Any]:
    """Validate cross-provider family semantics not expressible in JSON Schema."""
    return validate_plugin_catalog(catalog)
