from __future__ import annotations

import re
import struct
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
    "interrupt": ("kernel_id", "execution_id"),
    "submit_input": ("kernel_id", "execution_id", "input_request_id", "value"),
    "stop_kernel": ("kernel_id",),
    "complete": ("kernel_id", "notebook_id", "cell_id", "body"),
    "followup": ("client_id", "body"),
    "close_client": ("client_id",),
    "restart_notebook": ("runtime_id", "kernel_id", "notebook_id", "next_notebook_id", "kernel_name"),
}
LAYERS = {"protocol", "frontend_transport", "service", "supervisor", "kernel", "execution", "client", "plugin_discovery", "plugin_worker"}
OPERATIONS = {"service_start", "start_kernel", "stop_kernel", "complete", "followup", "close_client", "restart_notebook", "execute", "run_terminal_surface", "interrupt", "submit_input", "cleanup", "inspect", "connect_events"}
EVENT_KINDS = {"service.ready", "operation.started", "operation.completed", "kernel.state_changed", "execution.started", "execution.output", "execution.input_requested", "execution.input_replied", "execution.completed", "client.created", "client.closed", "surface.created", "surface.closed", "failure.occurred"}
RESOURCE_KINDS = {"supervisor", "notebook_runtime", "kernel", "execution", "client", "surface", "plugin_discovery", "plugin_worker", "transport", "notebook", "cell"}
OUTCOMES = {"pending", "running", "succeeded", "failed", "interrupted", "cancelled"}
FAILURE_REASONS = {"invalid_request", "unsupported", "not_found", "conflict", "unreachable", "timeout", "cancelled", "spawn_failed", "readiness_failed", "process_exited", "process_signalled", "channel_closed", "protocol_violation", "kernel_died", "execution_error", "interrupted", "plugin_error", "cleanup_incomplete", "capacity_exceeded", "internal_error"}
FAILURE_SCOPES = {"request", "transport", "execution", "cell", "client", "plugin_discovery", "plugin_worker", "kernel", "supervisor"}
WORKER_OPERATIONS = {"execute", "followup", "complete", "editor_action"}
WORKER_FAILURE_REASONS = {"invalid_request", "unsupported", "timeout", "cancelled", "plugin_error", "internal_error"}
TERMINAL_STREAM_KINDS = {"attach", "attached", "resize", "resized", "failure"}
TERMINAL_STREAM_FAILURE_REASONS = {"busy", "cursor_expired", "not_found", "protocol_violation", "channel_closed"}
TERMINAL_STREAM_FAILURE_OPERATIONS = {"attach", "resize", "stream"}
TERMINAL_OUTPUT_FRAME_VERSION = 1
TERMINAL_OUTPUT_FRAME_TYPE = 1
TERMINAL_OUTPUT_HEADER_SIZE = 10
UINT64_MAX = (1 << 64) - 1


def _required_strings(value: dict[str, Any], fields: tuple[str, ...], context: str) -> None:
    for field in fields:
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ProtocolValidationError(f"{context}.{field} must be a non-empty string")


def _bounded_string(value: object, minimum: int, maximum: int) -> bool:
    return isinstance(value, str) and minimum <= len(value) <= maximum


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


def _validate_client(client: object, context: str = "client") -> dict[str, Any]:
    fields = {
        "client_id", "runtime_id", "kernel_id", "notebook_id", "cell_id",
        "execution_id", "plugin_worker_id", "plugin_id", "plugin_version",
        "family_id", "capabilities", "interaction", "created_at",
    }
    if not isinstance(client, dict) or set(client) != fields:
        raise ProtocolValidationError(f"{context} has invalid fields")
    _required_strings(
        client,
        (
            "client_id", "runtime_id", "kernel_id", "notebook_id", "cell_id",
            "execution_id", "plugin_worker_id", "plugin_id", "plugin_version",
            "family_id", "created_at",
        ),
        context,
    )
    capabilities = client["capabilities"]
    allowed_capabilities = {"execute", "followup", "complete", "interrupt", "editor_actions"}
    if not isinstance(capabilities, list) or len(capabilities) != len(set(capabilities)) or any(
        capability not in allowed_capabilities for capability in capabilities
    ):
        raise ProtocolValidationError(f"{context}.capabilities are invalid")
    if client["interaction"] not in {"noninteractive", "request_response", "terminal_interactive"}:
        raise ProtocolValidationError(f"{context}.interaction is invalid")
    return client


def _validate_execution(execution: object, context: str = "execution") -> dict[str, Any]:
    fields = {"execution_id", "kernel_id", "notebook_id", "cell_id", "client_id", "outcome", "started_at", "completed_at"}
    if not isinstance(execution, dict) or set(execution) != fields:
        raise ProtocolValidationError(f"{context} has invalid fields")
    _required_strings(execution, ("execution_id", "kernel_id", "notebook_id", "cell_id", "started_at"), context)
    if execution["client_id"] is not None and not _bounded_string(execution["client_id"], 3, 128):
        raise ProtocolValidationError(f"{context}.client_id is invalid")
    if execution["outcome"] not in OUTCOMES:
        raise ProtocolValidationError(f"{context}.outcome is invalid")
    if execution["outcome"] == "running":
        if execution["completed_at"] is not None:
            raise ProtocolValidationError(f"{context} running execution cannot be complete")
    elif not isinstance(execution["completed_at"], str):
        raise ProtocolValidationError(f"{context} completed execution requires a completion time")
    return execution


def _validate_surface(surface: object, context: str = "surface") -> dict[str, Any]:
    fields = {"surface_id", "client_id", "runtime_id", "kind", "capabilities", "transport", "created_at"}
    if not isinstance(surface, dict) or set(surface) != fields:
        raise ProtocolValidationError(f"{context} has invalid fields")
    _required_strings(surface, ("surface_id", "client_id", "runtime_id", "created_at"), context)
    if surface["kind"] != "terminal":
        raise ProtocolValidationError(f"{context}.kind is invalid")
    capabilities = surface["capabilities"]
    if not isinstance(capabilities, list) or len(capabilities) != len(set(capabilities)) or not {"input", "resize"} <= set(capabilities) or not set(capabilities) <= {"input", "resize", "signal"}:
        raise ProtocolValidationError(f"{context}.capabilities are invalid")
    transport = surface["transport"]
    if not isinstance(transport, dict) or set(transport) != {"kind", "endpoint", "subprotocol"}:
        raise ProtocolValidationError(f"{context}.transport has invalid fields")
    if transport["kind"] != "websocket" or transport["subprotocol"] != "jusi.terminal.v1":
        raise ProtocolValidationError(f"{context}.transport is invalid")
    expected_endpoint = f"/v1/surfaces/{surface['surface_id']}/terminal"
    if transport["endpoint"] != expected_endpoint:
        raise ProtocolValidationError(f"{context}.transport endpoint identity mismatch")
    return surface


def _validate_input_request(value: object) -> dict[str, Any]:
    fields = {"input_request_id", "execution_id", "kernel_id", "notebook_id", "cell_id", "prompt", "password"}
    if not isinstance(value, dict):
        raise ProtocolValidationError("input request must be an object")
    _exact_fields(value, fields, set(), "input request")
    _required_strings(value, ("input_request_id", "execution_id", "kernel_id", "notebook_id", "cell_id"), "input request")
    if not isinstance(value["prompt"], str) or not isinstance(value["password"], bool):
        raise ProtocolValidationError("input prompt or password flag is invalid")
    return value


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
        _required_strings(payload, ("execution_id", "kernel_id", "notebook_id", "cell_id", "started_at"), "payload")
        if payload["client_id"] is not None and not _bounded_string(payload["client_id"], 3, 128):
            raise ProtocolValidationError("execution client identity is invalid")
        if payload["outcome"] not in OUTCOMES:
            raise ProtocolValidationError("execution outcome is invalid")
        if kind == "execution.started" and (payload["outcome"] != "running" or payload["completed_at"] is not None):
            raise ProtocolValidationError("execution.started must be running and incomplete")
        if kind == "execution.completed" and (payload["outcome"] not in OUTCOMES - {"pending", "running"} or not isinstance(payload["completed_at"], str)):
            raise ProtocolValidationError("execution.completed has an invalid outcome or completion time")
        if data["resource"] != {"kind": "execution", "id": payload["execution_id"]}:
            raise ProtocolValidationError("execution event resource mismatch")
    elif kind in {"execution.input_requested", "execution.input_replied"}:
        if kind == "execution.input_requested":
            _validate_input_request(payload)
        else:
            _exact_fields(payload, {"execution_id", "input_request_id"}, set(), "input reply")
            _required_strings(payload, ("execution_id", "input_request_id"), "input reply")
        if data["resource"] != {"kind": "execution", "id": payload["execution_id"]}:
            raise ProtocolValidationError("input event resource mismatch")
    elif kind == "execution.output":
        fields = {"execution_id", "client_id", "output_kind", "media_type", "data"}
        _exact_fields(payload, fields, set(), "payload")
        _required_strings(payload, ("execution_id", "media_type"), "payload")
        if payload["client_id"] is not None and not _bounded_string(payload["client_id"], 3, 128):
            raise ProtocolValidationError("execution output client identity is invalid")
        if payload["output_kind"] not in {"stdout", "stderr", "result", "display"} or not isinstance(payload["data"], str):
            raise ProtocolValidationError("execution output shape is invalid")
        if data["resource"] != {"kind": "execution", "id": payload["execution_id"]}:
            raise ProtocolValidationError("execution output resource mismatch")
    elif kind == "client.created":
        _validate_client(payload, "payload")
        if data["resource"] != {"kind": "client", "id": payload["client_id"]}:
            raise ProtocolValidationError("client event resource mismatch")
    elif kind == "client.closed":
        required = {"client_id", "plugin_worker_id", "reason", "closed_at"}
        _exact_fields(payload, required, {"failure_id"}, "payload")
        _required_strings(payload, ("client_id", "plugin_worker_id", "reason", "closed_at"), "payload")
        if payload["reason"] not in {"explicit_close", "fatal_failure", "runtime_cleanup"}:
            raise ProtocolValidationError("client close reason is invalid")
        if "failure_id" in payload and not _bounded_string(payload["failure_id"], 3, 128):
            raise ProtocolValidationError("client close failure identity is invalid")
        if data["resource"] != {"kind": "client", "id": payload["client_id"]}:
            raise ProtocolValidationError("client event resource mismatch")
    elif kind == "surface.created":
        _validate_surface(payload, "payload")
        if data["resource"] != {"kind": "surface", "id": payload["surface_id"]}:
            raise ProtocolValidationError("surface event resource mismatch")
    elif kind == "surface.closed":
        _exact_fields(payload, {"surface_id", "client_id", "reason", "closed_at"}, {"failure_id"}, "payload")
        _required_strings(payload, ("surface_id", "client_id", "reason", "closed_at"), "payload")
        if payload["reason"] not in {"client_cleanup", "fatal_failure"}:
            raise ProtocolValidationError("surface close reason is invalid")
        if "failure_id" in payload and not _bounded_string(payload["failure_id"], 3, 128):
            raise ProtocolValidationError("surface close failure identity is invalid")
        if data["resource"] != {"kind": "surface", "id": payload["surface_id"]}:
            raise ProtocolValidationError("surface event resource mismatch")
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


def _validate_terminal_cursor(value: object, context: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"0|[1-9][0-9]{0,19}", value) is None:
        raise ProtocolValidationError(f"{context} must be a canonical unsigned decimal string")
    if int(value) > UINT64_MAX:
        raise ProtocolValidationError(f"{context} exceeds uint64")
    return value


def _validate_terminal_geometry(data: dict[str, Any], context: str) -> None:
    for field in ("rows", "columns"):
        value = data[field]
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65535:
            raise ProtocolValidationError(f"{context}.{field} must be an integer from 1 through 65535")


def validate_terminal_stream_control(data: object) -> dict[str, Any]:
    """Validate one JSON text control from either side of jusi.terminal.v1."""
    if not isinstance(data, dict) or data.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolValidationError("Terminal stream control has an invalid envelope")
    kind = data.get("kind")
    if kind not in TERMINAL_STREAM_KINDS:
        raise ProtocolValidationError("Terminal stream control kind is unsupported")
    common = {"protocol_version", "kind", "surface_id", "attachment_id"}
    for field in ("surface_id", "attachment_id"):
        if not _bounded_string(data.get(field), 3, 128):
            raise ProtocolValidationError(f"{field} length is invalid")
    if kind in {"attach", "attached"}:
        if set(data) != common | {"cursor", "rows", "columns"}:
            raise ProtocolValidationError(f"Terminal stream {kind} fields are invalid")
        _validate_terminal_cursor(data["cursor"], "cursor")
        _validate_terminal_geometry(data, kind)
    elif kind in {"resize", "resized"}:
        if set(data) != common | {"resize_id", "rows", "columns"}:
            raise ProtocolValidationError(f"Terminal stream {kind} fields are invalid")
        if not _bounded_string(data["resize_id"], 3, 128):
            raise ProtocolValidationError("resize_id length is invalid")
        _validate_terminal_geometry(data, kind)
    else:
        fields = common | {"operation", "reason", "message", "retryable"}
        if set(data) != fields:
            raise ProtocolValidationError("Terminal stream failure fields are invalid")
        if data["operation"] not in TERMINAL_STREAM_FAILURE_OPERATIONS:
            raise ProtocolValidationError("Terminal stream failure operation is unsupported")
        if data["reason"] not in TERMINAL_STREAM_FAILURE_REASONS:
            raise ProtocolValidationError("Terminal stream failure reason is unsupported")
        if not _bounded_string(data["message"], 1, 1000) or not isinstance(data["retryable"], bool):
            raise ProtocolValidationError("Terminal stream failure body is invalid")
    return dict(data)


def encode_terminal_output_frame(starting_cursor: int, payload: bytes) -> bytes:
    if not isinstance(starting_cursor, int) or isinstance(starting_cursor, bool) or not 0 <= starting_cursor <= UINT64_MAX:
        raise ProtocolValidationError("Terminal output starting cursor must fit uint64")
    if not isinstance(payload, bytes):
        raise ProtocolValidationError("Terminal output payload must be bytes")
    return struct.pack(">BBQ", TERMINAL_OUTPUT_FRAME_VERSION, TERMINAL_OUTPUT_FRAME_TYPE, starting_cursor) + payload


def decode_terminal_output_frame(frame: object) -> tuple[int, bytes]:
    if not isinstance(frame, bytes) or len(frame) < TERMINAL_OUTPUT_HEADER_SIZE:
        raise ProtocolValidationError("Terminal output frame is shorter than its header")
    version, frame_type, cursor = struct.unpack(">BBQ", frame[:TERMINAL_OUTPUT_HEADER_SIZE])
    if version != TERMINAL_OUTPUT_FRAME_VERSION or frame_type != TERMINAL_OUTPUT_FRAME_TYPE:
        raise ProtocolValidationError("Terminal output frame version or type is unsupported")
    return cursor, frame[TERMINAL_OUTPUT_HEADER_SIZE:]


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
        if field not in {"code", "value", "body"} and not value.strip():
            raise ProtocolValidationError(f"{field} must be a non-empty string")
    idempotency_key = data.get("idempotency_key")
    if idempotency_key is not None and (not isinstance(idempotency_key, str) or not idempotency_key):
        raise ProtocolValidationError("idempotency_key must be a non-empty string when provided")
    allowed = {"protocol_version", "command_id", "trace_id", "kind", "idempotency_key", *COMMAND_FIELDS[expected_kind]}
    if expected_kind == "complete":
        allowed.update({"cursor_pos", "client_id"})
        cursor = data.get("cursor_pos")
        if type(cursor) is not int or not 0 <= cursor <= len(data["body"]):
            raise ProtocolValidationError("cursor_pos must be a Unicode character offset within body")
        if "client_id" in data:
            _required_strings(data, ("client_id",), "complete")
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
    if "runtime" not in data:
        raise ProtocolValidationError("runtime must be present in health response")
    runtime = data["runtime"]
    if runtime is not None:
        fields = {"runtime_id", "notebook_id", "discovery_id", "kernel_id", "plugin_catalog"}
        if not isinstance(runtime, dict) or set(runtime) != fields:
            raise ProtocolValidationError("runtime has invalid fields")
        _required_strings(runtime, ("runtime_id", "notebook_id", "discovery_id", "kernel_id"), "runtime")
        catalog = validate_plugin_catalog(runtime["plugin_catalog"])
        if catalog["discovery_id"] != runtime["discovery_id"]:
            raise ProtocolValidationError("runtime discovery identity mismatch")
        if kernel is None or runtime["kernel_id"] != kernel["kernel_id"] or runtime["notebook_id"] != kernel.get("notebook_id"):
            raise ProtocolValidationError("runtime kernel ownership mismatch")
    elif kernel is not None and kernel.get("state") == "on":
        raise ProtocolValidationError("an on kernel requires an authoritative runtime")
    clients = data.get("clients")
    if not isinstance(clients, list):
        raise ProtocolValidationError("clients must be present as an array")
    client_ids: set[str] = set()
    worker_ids: set[str] = set()
    for index, candidate in enumerate(clients):
        client = _validate_client(candidate, f"clients[{index}]")
        if client["client_id"] in client_ids or client["plugin_worker_id"] in worker_ids:
            raise ProtocolValidationError("client or plugin worker identity is duplicated")
        client_ids.add(client["client_id"])
        worker_ids.add(client["plugin_worker_id"])
        if runtime is None or client["runtime_id"] != runtime["runtime_id"] or client["kernel_id"] != runtime["kernel_id"] or client["notebook_id"] != runtime["notebook_id"]:
            raise ProtocolValidationError("client runtime ownership mismatch")
        plugin = next(
            (
                item for item in runtime["plugin_catalog"]["plugins"]
                if item["plugin_id"] == client["plugin_id"]
            ),
            None,
        )
        if plugin is None or plugin["plugin_version"] != client["plugin_version"] or plugin["interaction"] != client["interaction"]:
            raise ProtocolValidationError("client exact plugin identity mismatch")
        family = next(
            (item for item in plugin["families"] if item["family_id"] == client["family_id"]),
            None,
        )
        if family is None or set(family["capabilities"]) != set(client["capabilities"]):
            raise ProtocolValidationError("client family capabilities mismatch")
    executions = data.get("executions")
    if not isinstance(executions, list) or len(executions) > 1:
        raise ProtocolValidationError("executions must be present with at most one active execution")
    for index, candidate in enumerate(executions):
        execution = _validate_execution(candidate, f"executions[{index}]")
        if execution["outcome"] != "running":
            raise ProtocolValidationError("health exposes only active executions")
        if runtime is None or execution["kernel_id"] != runtime["kernel_id"] or execution["notebook_id"] != runtime["notebook_id"]:
            raise ProtocolValidationError("execution runtime ownership mismatch")
    pending_input = data.get("pending_input")
    if pending_input is not None:
        _validate_input_request(pending_input)
        if len(executions) != 1 or any(pending_input[field] != executions[0][field]
                for field in ("kernel_id", "execution_id", "notebook_id", "cell_id")):
            raise ProtocolValidationError("input request execution ownership mismatch")
    surfaces = data.get("surfaces")
    if not isinstance(surfaces, list):
        raise ProtocolValidationError("surfaces must be present as an array")
    surface_ids: set[str] = set()
    clients_by_id = {client["client_id"]: client for client in clients}
    for index, candidate in enumerate(surfaces):
        surface = _validate_surface(candidate, f"surfaces[{index}]")
        if surface["surface_id"] in surface_ids:
            raise ProtocolValidationError("surface identity is duplicated")
        surface_ids.add(surface["surface_id"])
        owner = clients_by_id.get(surface["client_id"])
        if owner is None or surface["runtime_id"] != owner["runtime_id"]:
            raise ProtocolValidationError("surface client ownership mismatch")
        if owner["interaction"] != "terminal_interactive":
            raise ProtocolValidationError("terminal surface owner is not interactive")
    return dict(data)


def validate_plugin_catalog(data: object) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != {"protocol_version", "catalog_version", "discovery_id", "plugins"}:
        raise ProtocolValidationError("Plugin catalog has invalid top-level fields")
    if data["protocol_version"] != 1 or data["catalog_version"] != 1:
        raise ProtocolValidationError("Plugin catalog version must be 1")
    if not _bounded_string(data["discovery_id"], 3, 128):
        raise ProtocolValidationError("discovery_id length is invalid")
    if not isinstance(data["plugins"], list):
        raise ProtocolValidationError("plugins must be an array")
    plugin_ids: set[str] = set()
    capabilities = {"execute", "followup", "complete", "interrupt", "editor_actions"}
    interactions = {"noninteractive", "request_response", "terminal_interactive"}
    fields = {"plugin_id", "plugin_version", "distribution", "families", "kernel_extensions", "worker_entry_point", "media_types", "interaction"}
    family_by_id: dict[str, tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]]] = {}
    family_by_magic: dict[str, str] = {}
    kernel_modules: set[str] = set()
    for plugin in data["plugins"]:
        if not isinstance(plugin, dict) or set(plugin) != fields:
            raise ProtocolValidationError("Plugin catalog entry fields are invalid")
        _required_strings(plugin, ("plugin_id", "plugin_version", "distribution"), "plugin")
        if not _bounded_string(plugin["plugin_id"], 3, 128) or not _bounded_string(plugin["plugin_version"], 1, 128) or not _bounded_string(plugin["distribution"], 1, 256):
            raise ProtocolValidationError("Plugin identity field length is invalid")
        if plugin["plugin_id"] in plugin_ids:
            raise ProtocolValidationError(f"Duplicate plugin_id: {plugin['plugin_id']}")
        plugin_ids.add(plugin["plugin_id"])
        if plugin["interaction"] not in interactions:
            raise ProtocolValidationError("Plugin interaction is invalid")
        if plugin["worker_entry_point"] is not None and (not isinstance(plugin["worker_entry_point"], str) or not plugin["worker_entry_point"]):
            raise ProtocolValidationError("worker_entry_point must be a string or null")
        for field in ("kernel_extensions", "media_types"):
            values = plugin[field]
            if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values) or len(values) != len(set(values)):
                raise ProtocolValidationError(f"plugin.{field} must contain unique non-empty strings")
        for module in plugin["kernel_extensions"]:
            if module in kernel_modules:
                raise ProtocolValidationError(f"Kernel extension module is claimed more than once: {module}")
            kernel_modules.add(module)
        if not isinstance(plugin["families"], list) or not plugin["families"]:
            raise ProtocolValidationError("plugin.families must be a non-empty array")
        family_claims: set[tuple[str, str]] = set()
        for family in plugin["families"]:
            required = {"family_id", "magic_name", "capabilities"}
            if not isinstance(family, dict) or not required <= set(family) or set(family) - required - {"presentation", "provider_presentation"}:
                raise ProtocolValidationError("Plugin family fields are invalid")
            _required_strings(family, ("family_id", "magic_name"), "family")
            if not _bounded_string(family["family_id"], 1, 128):
                raise ProtocolValidationError("Plugin family_id length is invalid")
            claim = (family["family_id"], family["magic_name"])
            if claim in family_claims or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", family["magic_name"]) is None:
                raise ProtocolValidationError("Plugin family claim is duplicate or invalid")
            family_claims.add(claim)
            if not isinstance(family["capabilities"], list) or len(family["capabilities"]) != len(set(family["capabilities"])) or not set(family["capabilities"]) <= capabilities:
                raise ProtocolValidationError("Plugin family capabilities are invalid")
            for field in ("presentation", "provider_presentation"):
                value = family.get(field)
                if field in family and (not isinstance(value, dict) or set(value) - {"syntax", "indent"}
                        or any(not isinstance(item, str) or len(item) > 64
                               or re.fullmatch(r"(?:[a-z][a-z0-9_]*|)", item) is None for item in value.values())):
                    raise ProtocolValidationError("Plugin family editing profile is invalid")
            presentation = family.get("presentation")
            descriptor = (
                family["magic_name"],
                tuple(sorted(family["capabilities"])),
                tuple(sorted((presentation or {}).items())),
            )
            existing = family_by_id.get(family["family_id"])
            if existing is not None and existing != descriptor:
                raise ProtocolValidationError(f"Conflicting family claim: {family['family_id']}")
            claimed_family = family_by_magic.get(family["magic_name"])
            if claimed_family is not None and claimed_family != family["family_id"]:
                raise ProtocolValidationError(f"Magic {family['magic_name']} is claimed by incompatible families")
            family_by_id[family["family_id"]] = descriptor
            family_by_magic[family["magic_name"]] = family["family_id"]
    return dict(data)


def validate_plugin_worker_message(data: object) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ProtocolValidationError("Plugin worker message must be an object")
    if data.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolValidationError(f"protocol_version must be {PROTOCOL_VERSION}")
    kind = data.get("kind")
    if kind not in {
        "worker.ready",
        "worker.request",
        "worker.result",
        "worker.failure",
        "worker.shutdown",
        "worker.stopped",
    }:
        raise ProtocolValidationError("Plugin worker message kind is unsupported")
    if not _bounded_string(data.get("plugin_worker_id"), 3, 128):
        raise ProtocolValidationError("plugin_worker_id length is invalid")

    if kind == "worker.ready":
        fields = {
            "protocol_version", "kind", "plugin_worker_id", "runtime_id", "plugin_id",
            "family_id", "client_id", "execution_id", "pid",
        }
        if set(data) != fields:
            raise ProtocolValidationError("Plugin worker ready fields are invalid")
        for field in ("runtime_id", "plugin_id", "client_id", "execution_id"):
            if not _bounded_string(data[field], 3, 128):
                raise ProtocolValidationError(f"{field} length is invalid")
        if not _bounded_string(data["family_id"], 1, 128):
            raise ProtocolValidationError("family_id length is invalid")
        if not isinstance(data["pid"], int) or isinstance(data["pid"], bool) or data["pid"] < 1:
            raise ProtocolValidationError("pid must be a positive integer")
        return dict(data)

    base_fields = {"protocol_version", "kind", "plugin_worker_id", "request_id", "trace_id"}
    for field in ("request_id", "trace_id"):
        if not _bounded_string(data.get(field), 3, 128):
            raise ProtocolValidationError(f"{field} length is invalid")
    if kind in {"worker.shutdown", "worker.stopped"}:
        if set(data) != base_fields:
            raise ProtocolValidationError(f"Plugin worker {kind} fields are invalid")
        return dict(data)

    common_fields = base_fields | {"operation"}
    if data.get("operation") not in WORKER_OPERATIONS:
        raise ProtocolValidationError("Plugin worker operation is unsupported")
    if kind == "worker.request":
        if set(data) != common_fields | {"payload"} or not isinstance(data.get("payload"), dict):
            raise ProtocolValidationError("Plugin worker request fields are invalid")
    elif kind == "worker.result":
        if set(data) != common_fields | {"result", "core_requests"} or not isinstance(data.get("result"), dict) or not isinstance(data.get("core_requests"), list):
            raise ProtocolValidationError("Plugin worker result fields are invalid")
        request_ids: set[str] = set()
        for request in data["core_requests"]:
            if not isinstance(request, dict) or set(request) != {"request_id", "kind", "argv", "cwd", "environment_overrides", "capabilities"}:
                raise ProtocolValidationError("Plugin worker core request fields are invalid")
            if not _bounded_string(request["request_id"], 3, 128) or request["request_id"] in request_ids:
                raise ProtocolValidationError("Plugin worker core request identity is invalid")
            request_ids.add(request["request_id"])
            if request["kind"] != "terminal_surface.create":
                raise ProtocolValidationError("Plugin worker core request kind is unsupported")
            argv = request["argv"]
            if not isinstance(argv, list) or not 1 <= len(argv) <= 128 or any(not _bounded_string(value, 1, 4096) for value in argv):
                raise ProtocolValidationError("Terminal surface argv is invalid")
            cwd = request["cwd"]
            if cwd is not None and (not _bounded_string(cwd, 1, 4096) or not cwd.startswith("/")):
                raise ProtocolValidationError("Terminal surface cwd must be null or absolute")
            environment = request["environment_overrides"]
            if not isinstance(environment, dict) or len(environment) > 128 or any(
                not _bounded_string(key, 1, 256) or not isinstance(value, str) or len(value) > 8192
                for key, value in environment.items()
            ):
                raise ProtocolValidationError("Terminal surface environment is invalid")
            capabilities = request["capabilities"]
            if not isinstance(capabilities, list) or len(capabilities) != len(set(capabilities)) or not {"input", "resize"} <= set(capabilities) or not set(capabilities) <= {"input", "resize", "signal"}:
                raise ProtocolValidationError("Terminal surface capabilities are invalid")
    else:
        if set(data) != common_fields | {"failure"} or not isinstance(data.get("failure"), dict):
            raise ProtocolValidationError("Plugin worker failure fields are invalid")
        failure = data["failure"]
        if set(failure) - {"reason", "message", "retryable", "details"} or not {"reason", "message", "retryable"} <= set(failure):
            raise ProtocolValidationError("Plugin worker failure body fields are invalid")
        if failure["reason"] not in WORKER_FAILURE_REASONS:
            raise ProtocolValidationError("Plugin worker failure reason is unsupported")
        if not _bounded_string(failure["message"], 1, 1000) or not isinstance(failure["retryable"], bool):
            raise ProtocolValidationError("Plugin worker failure body is invalid")
        if "details" in failure and not isinstance(failure["details"], dict):
            raise ProtocolValidationError("Plugin worker failure details must be an object")
    return dict(data)


def validate_plugin_kernel_message(data: object) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolValidationError("Plugin kernel message has an invalid envelope")
    kind = data.get("kind")
    if kind == "plugin.adapters_ready":
        if set(data) != {"protocol_version", "kind", "adapters"} or not isinstance(data.get("adapters"), list):
            raise ProtocolValidationError("Plugin adapter attestation fields are invalid")
        modules: set[str] = set()
        for adapter in data["adapters"]:
            fields = {"plugin_id", "plugin_version", "module", "families"}
            if not isinstance(adapter, dict) or set(adapter) != fields:
                raise ProtocolValidationError("Plugin adapter fields are invalid")
            if not _bounded_string(adapter["plugin_id"], 3, 128) or not _bounded_string(adapter["plugin_version"], 1, 128) or not _bounded_string(adapter["module"], 1, 512):
                raise ProtocolValidationError("Plugin adapter identity is invalid")
            if adapter["module"] in modules:
                raise ProtocolValidationError("Plugin adapter module is duplicated")
            modules.add(adapter["module"])
            _validate_kernel_families(adapter["families"])
        return dict(data)
    if kind == "plugin.handoff":
        fields = {"protocol_version", "kind", "plugin_id", "plugin_version", "family_id", "magic_name", "payload"}
        if set(data) != fields:
            raise ProtocolValidationError("Plugin handoff fields are invalid")
        if not _bounded_string(data["plugin_id"], 3, 128) or not _bounded_string(data["plugin_version"], 1, 128):
            raise ProtocolValidationError("Plugin handoff provider identity is invalid")
        _validate_kernel_families([{"family_id": data["family_id"], "magic_name": data["magic_name"]}])
        if not isinstance(data["payload"], dict):
            raise ProtocolValidationError("Plugin handoff payload must be an object")
        return dict(data)
    raise ProtocolValidationError("Plugin kernel message kind is unsupported")


def _validate_kernel_families(families: object) -> None:
    if not isinstance(families, list) or not families:
        raise ProtocolValidationError("Plugin adapter families must be a non-empty array")
    seen: set[tuple[str, str]] = set()
    for family in families:
        if not isinstance(family, dict) or set(family) != {"family_id", "magic_name"}:
            raise ProtocolValidationError("Plugin adapter family fields are invalid")
        if not _bounded_string(family["family_id"], 1, 128) or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", family["magic_name"]) is None:
            raise ProtocolValidationError("Plugin adapter family identity is invalid")
        identity = (family["family_id"], family["magic_name"])
        if identity in seen:
            raise ProtocolValidationError("Plugin adapter family is duplicated")
        seen.add(identity)


def validate_completion(result: object, cursor_pos: int) -> dict[str, Any]:
    """Ranges are zero-based Unicode code points in the original cell body."""
    if not isinstance(result, dict) or not isinstance(result.get("items"), list):
        raise ProtocolValidationError("completion.items must be an array")
    if len(result["items"]) > 500:
        raise ProtocolValidationError("completion has too many items")
    for item in result["items"]:
        if not isinstance(item, dict):
            raise ProtocolValidationError("completion item must be an object")
        start, end = item.get("start"), item.get("end")
        if type(start) is not int or type(end) is not int or not 0 <= start <= end <= cursor_pos:
            raise ProtocolValidationError("completion range must lie before the cursor")
        if not isinstance(item.get("text"), str) or len(item["text"]) > 65536 or "\x00" in item["text"]:
            raise ProtocolValidationError("completion text must be a bounded string without NUL")
        for key in ("label", "detail", "documentation", "kind"):
            if key in item and (not isinstance(item[key], str) or len(item[key]) > 4096 or "\x00" in item[key]):
                raise ProtocolValidationError("completion metadata must be bounded text")
    return result
