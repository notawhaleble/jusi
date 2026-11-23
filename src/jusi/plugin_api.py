from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from hashlib import sha256
from pathlib import Path

from jusi.protocol import ProtocolValidationError, validate_plugin_catalog
from jusi.application.ports import TerminalSurfaceRequest, PluginOperationError


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
    close_on_success: bool = False,
) -> TerminalSurfaceRequest:
    capabilities = ("input", "resize", "signal") if signal else ("input", "resize")
    return TerminalSurfaceRequest(
        request_id=request_id,
        argv=argv,
        cwd=cwd,
        environment_overrides=dict(environment_overrides or {}),
        capabilities=capabilities,
        close_on_success=close_on_success,
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


class OperationRejected(PluginOperationError):
    """Raise for a recoverable request failure, never for a broken session."""

    def __init__(self, message: str, *, reason: str = "plugin_error") -> None:
        if reason not in {"invalid_request", "unsupported", "conflict", "timeout", "cancelled", "plugin_error", "internal_error"}:
            raise ValueError("Unsupported recoverable operation reason")
        super().__init__(message, reason=reason, retryable=False)


class OperationInterrupted(OperationRejected):
    def __init__(self, message: str = "Plugin operation interrupted") -> None:
        super().__init__(message, reason="cancelled")


def copy_text(text: str, *, linewise: bool = False) -> WorkerResult:
    return _editor_result({"action": "copy", "text": text, "regtype": "V" if linewise else "v"}, "copy")


def open_url(url: str, *, slot: str | None = None) -> WorkerResult:
    """Open an HTTP(S) URL; a slot replaces only this client's previous page."""
    content = {"action": "open_url", "text": "", "url": url}
    if slot is not None:
        content["slot"] = slot
    return _editor_result(content, "open_url")


def close_url(*, slot: str) -> WorkerResult:
    """Close this client's named URL slot, including a hidden page; absent is OK."""
    return _editor_result({"action": "close_url", "text": "", "slot": slot}, "close_url")


def open_text(text: str, *, name: str = "export.txt", filetype: str = "") -> WorkerResult:
    return _editor_result({"action": "open", "text": text, "name": name, "filetype": filetype}, "open")



def show_diff(before: str, after: str, *, before_name: str = "before.txt",
              after_name: str = "after.txt", filetype: str = "") -> WorkerResult:
    if not isinstance(before, str) or not isinstance(after, str):
        raise OperationRejected("Diff snapshots must be text", reason="invalid_request")
    return _editor_result({"action": "show_diff", "text": before + after,
                          "before_bytes": len(before.encode("utf-8")), "before_name": before_name,
                          "after_name": after_name, "filetype": filetype}, "show_diff")

def _editor_result(result: dict[str, Any], action: str) -> WorkerResult:
    from jusi.protocol import validate_editor_action
    try:
        return WorkerResult(validate_editor_action(result, action))
    except (ProtocolValidationError, UnicodeError) as exc:
        raise OperationRejected(str(exc), reason="invalid_request") from exc


def retain_input_images(payload: dict[str, Any], directory: Path) -> list[str]:
    """Copy request-scoped PNGs into a worker-owned private client directory."""
    paths = []
    for file in payload.get("attachments", ()):
        if file["media_type"] != "image/png":
            raise OperationRejected("Expected PNG input", reason="unsupported")
        data = Path(file["path"]).read_bytes()
        target = directory / (sha256(data).hexdigest() + ".png")
        if not target.exists():
            target.write_bytes(data)
        paths.append(str(target))
    return paths
