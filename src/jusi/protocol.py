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
LAYERS = {"protocol", "frontend_transport", "service", "supervisor", "kernel", "execution", "client", "plugin_worker"}
OPERATIONS = {"service_start", "start_kernel", "stop_kernel", "execute", "interrupt", "cleanup", "inspect", "connect_events"}
EVENT_KINDS = {"service.ready", "operation.started", "operation.completed", "kernel.state_changed", "execution.started", "execution.output", "execution.completed", "failure.occurred"}
RESOURCE_KINDS = {"supervisor", "kernel", "execution", "client", "plugin_worker", "transport", "notebook", "cell"}
OUTCOMES = {"pending", "running", "succeeded", "failed", "interrupted", "cancelled"}
FAILURE_REASONS = {"invalid_request", "unsupported", "not_found", "conflict", "unreachable", "timeout", "cancelled", "spawn_failed", "readiness_failed", "process_exited", "process_signalled", "channel_closed", "protocol_violation", "kernel_died", "execution_error", "interrupted", "plugin_error", "cleanup_incomplete", "capacity_exceeded", "internal_error"}
FAILURE_SCOPES = {"request", "transport", "execution", "cell", "client", "plugin_worker", "kernel", "supervisor"}


def _required_strings(value: dict[str, Any], fields: tuple[str, ...], context: str) -> None:
    for field in fields:
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ProtocolValidationError(f"{context}.{field} must be a non-empty string")


def _exact_fields(value: dict[str, Any], required: set[str], optional: set[str], context: str) -> None:
    missing = required - set(value)
    unknown = set(value) - required - optional
    if missing:
        raise ProtocolValidationError(f"{context} missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ProtocolValidationError(f"{context} unknown fields: {', '.join(sorted(unknown))}")


def _validate_resource(resource: object, context: str = "resource") -> dict[str, Any]:
    if not isinstance(resource, dict) or set(resource) != {"kind", "id"}:
        raise ProtocolValidationError(f"{context} must contain exactly kind and id")
    if resource.get("kind") not in RESOURCE_KINDS:
        raise ProtocolValidationError(f"{context}.kind is unsupported")
    _required_strings(resource, ("id",), context)
    return resource


def _validate_event_payload(data: dict[str, Any]) -> None:
    kind = data["kind"]
    payload = data["payload"]
    if kind == "service.ready":
        _exact_fields(payload, {"supervisor_id"}, set(), "payload")
        _required_strings(payload, ("supervisor_id",), "payload")
        if payload["supervisor_id"] != data["supervisor_id"]:
            raise ProtocolValidationError("service.ready supervisor identity mismatch")
        if data["resource"] != {"kind": "supervisor", "id": data["supervisor_id"]}:
            raise ProtocolValidationError("service.ready resource mismatch")
    elif kind.startswith("operation."):
        base = {"operation_id", "trace_id", "kind", "outcome", "started_at", "completed_at"}
        optional = {"failure_id", "cleanup"} if kind == "operation.completed" else set()
        _exact_fields(payload, base, optional, "payload")
        _required_strings(payload, ("operation_id", "trace_id", "kind", "started_at"), "payload")
        if payload["kind"] not in OPERATIONS or payload["trace_id"] != data["trace_id"]:
            raise ProtocolValidationError("operation payload identity is invalid")
        if kind == "operation.started":
            if payload["outcome"] != "running" or payload["completed_at"] is not None:
                raise ProtocolValidationError("operation.started must be running and incomplete")
        elif payload["outcome"] not in {"succeeded", "failed", "cancelled"} or not isinstance(payload["completed_at"], str):
            raise ProtocolValidationError("operation.completed has an invalid outcome or completion time")
        if "failure_id" in payload and (not isinstance(payload["failure_id"], str) or not payload["failure_id"]):
            raise ProtocolValidationError("payload.failure_id must be a non-empty string")
        if "cleanup" in payload and not isinstance(payload["cleanup"], dict):
            raise ProtocolValidationError("payload.cleanup must be an object")
    elif kind == "kernel.state_changed":
        _exact_fields(payload, {"kernel_id", "previous_state", "state"}, set(), "payload")
        _required_strings(payload, ("kernel_id",), "payload")
        if payload["previous_state"] not in {"off", "on"} or payload["state"] not in {"off", "on"}:
            raise ProtocolValidationError("kernel states must be off or on")
        if data["resource"] != {"kind": "kernel", "id": payload["kernel_id"]}:
            raise ProtocolValidationError("kernel event resource mismatch")
    elif kind in {"execution.started", "execution.completed"}:
        fields = {"execution_id", "kernel_id", "notebook_id", "cell_id", "client_id", "outcome", "started_at", "completed_at"}
        _exact_fields(payload, fields, set(), "payload")
        _required_strings(payload, ("execution_id", "kernel_id", "notebook_id", "cell_id", "client_id", "started_at"), "payload")
        if payload["outcome"] not in OUTCOMES:
            raise ProtocolValidationError("execution outcome is invalid")
        if kind == "execution.started" and (payload["outcome"] != "running" or payload["completed_at"] is not None):
            raise ProtocolValidationError("execution.started must be running and incomplete")
        if kind == "execution.completed" and (payload["outcome"] not in OUTCOMES - {"pending", "running"} or not isinstance(payload["completed_at"], str)):
            raise ProtocolValidationError("execution.completed has an invalid outcome or completion time")
        if data["resource"] != {"kind": "execution", "id": payload["execution_id"]}:
            raise ProtocolValidationError("execution event resource mismatch")
    elif kind == "execution.output":
        fields = {"execution_id", "client_id", "output_kind", "media_type", "data"}
        _exact_fields(payload, fields, set(), "payload")
        _required_strings(payload, ("execution_id", "client_id", "media_type"), "payload")
        if payload["output_kind"] not in {"stdout", "stderr", "result", "display"} or not isinstance(payload["data"], str):
            raise ProtocolValidationError("execution output shape is invalid")
        if data["resource"] != {"kind": "execution", "id": payload["execution_id"]}:
            raise ProtocolValidationError("execution output resource mismatch")
    elif kind == "failure.occurred":
        required = {"failure_id", "trace_id", "layer", "operation", "reason", "message", "retryable", "scope", "resource", "occurred_at"}
        optional = {"process", "caused_by_failure_id", "details"}
        _exact_fields(payload, required, optional, "payload")
        _required_strings(payload, ("failure_id", "trace_id", "reason", "message", "scope", "occurred_at"), "payload")
        if payload["trace_id"] != data["trace_id"] or payload["layer"] not in LAYERS or payload["operation"] not in OPERATIONS:
            raise ProtocolValidationError("failure origin or trace is invalid")
        if payload["reason"] not in FAILURE_REASONS or payload["scope"] not in FAILURE_SCOPES:
            raise ProtocolValidationError("failure reason or scope is unsupported")
        if not isinstance(payload["retryable"], bool) or _validate_resource(payload["resource"], "payload.resource") != data["resource"]:
            raise ProtocolValidationError("failure retryability or resource is invalid")
        if "details" in payload and not isinstance(payload["details"], dict):
            raise ProtocolValidationError("failure details must be an object")
        if "caused_by_failure_id" in payload and (not isinstance(payload["caused_by_failure_id"], str) or not payload["caused_by_failure_id"]):
            raise ProtocolValidationError("caused_by_failure_id must be a non-empty string")
        if "process" in payload and not isinstance(payload["process"], dict):
            raise ProtocolValidationError("failure process must be an object")


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
    if data["layer"] not in LAYERS or data["operation"] not in OPERATIONS or data["kind"] not in EVENT_KINDS:
        raise ProtocolValidationError("event layer, operation, or kind is unsupported")
    _validate_resource(data["resource"])
    if not isinstance(data["payload"], dict):
        raise ProtocolValidationError("payload must be a JSON object")
    _validate_event_payload(data)
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
