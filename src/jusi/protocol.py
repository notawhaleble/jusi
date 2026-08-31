from __future__ import annotations

from typing import Any


PROTOCOL_VERSION = 1
EVENT_FIELDS = {
    "protocol_version",
    "event_id",
    "supervisor_id",
    "sequence",
    "occurred_at",
    "trace_id",
    "layer",
    "operation",
    "kind",
    "resource",
    "payload",
}
COMMAND_FIELDS: dict[str, tuple[str, ...]] = {
    "start_kernel": ("notebook_id", "kernel_name"),
    "execute": ("kernel_id", "notebook_id", "cell_id", "code"),
    "stop_kernel": ("kernel_id",),
}


class ProtocolValidationError(ValueError):
    pass


def validate_command(data: object, expected_kind: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ProtocolValidationError("Request body must be a JSON object")
    if data.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolValidationError(f"protocol_version must be {PROTOCOL_VERSION}")
    if data.get("kind") != expected_kind:
        raise ProtocolValidationError(f"kind must be {expected_kind}")
    for field in ("command_id", "trace_id"):
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ProtocolValidationError(f"{field} must be a non-empty string")
    for field in COMMAND_FIELDS.get(expected_kind, ()):
        value = data.get(field)
        if not isinstance(value, str):
            raise ProtocolValidationError(f"{field} must be a string")
        if field != "code" and not value.strip():
            raise ProtocolValidationError(f"{field} must be a non-empty string")
    idempotency_key = data.get("idempotency_key")
    if idempotency_key is not None and (not isinstance(idempotency_key, str) or not idempotency_key):
        raise ProtocolValidationError("idempotency_key must be a non-empty string when provided")
    allowed = {"protocol_version", "command_id", "trace_id", "kind", "idempotency_key", *COMMAND_FIELDS[expected_kind]}
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ProtocolValidationError(f"Unknown command fields: {', '.join(unknown)}")
    return dict(data)


def validate_event(data: object) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ProtocolValidationError("Event must be a JSON object")
    if data.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolValidationError(f"protocol_version must be {PROTOCOL_VERSION}")
    missing = sorted(EVENT_FIELDS - set(data))
    if missing:
        raise ProtocolValidationError(f"Missing event fields: {', '.join(missing)}")
    for field in ("event_id", "supervisor_id", "occurred_at", "trace_id", "layer", "operation", "kind"):
        value = data[field]
        if not isinstance(value, str) or not value.strip():
            raise ProtocolValidationError(f"{field} must be a non-empty string")
    sequence = data["sequence"]
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise ProtocolValidationError("sequence must be a positive integer")
    resource = data["resource"]
    if not isinstance(resource, dict) or set(resource) != {"kind", "id"}:
        raise ProtocolValidationError("resource must contain exactly kind and id")
    if any(not isinstance(resource[field], str) or not resource[field].strip() for field in ("kind", "id")):
        raise ProtocolValidationError("resource kind and id must be non-empty strings")
    if not isinstance(data["payload"], dict):
        raise ProtocolValidationError("payload must be a JSON object")
    return dict(data)


def validate_health_response(data: object) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("ok") is not True or data.get("status") != "ready":
        raise ProtocolValidationError("Health response must be a ready object")
    supervisor_id = data.get("supervisor_id")
    if not isinstance(supervisor_id, str) or not supervisor_id.strip():
        raise ProtocolValidationError("supervisor_id must be a non-empty string")
    earliest = data.get("earliest_event_sequence")
    latest = data.get("event_sequence")
    if not isinstance(earliest, int) or isinstance(earliest, bool) or earliest < 1:
        raise ProtocolValidationError("earliest_event_sequence must be a positive integer")
    if not isinstance(latest, int) or isinstance(latest, bool) or latest < 0:
        raise ProtocolValidationError("event_sequence must be a non-negative integer")
    if earliest > latest + 1:
        raise ProtocolValidationError("event replay window is invalid")
    kernel = data.get("kernel")
    if kernel is not None:
        if not isinstance(kernel, dict):
            raise ProtocolValidationError("kernel must be an object or null")
        if not isinstance(kernel.get("kernel_id"), str) or not kernel["kernel_id"].strip():
            raise ProtocolValidationError("kernel.kernel_id must be a non-empty string")
        if kernel.get("state") not in {"off", "on"}:
            raise ProtocolValidationError("kernel.state must be off or on")
    return dict(data)
