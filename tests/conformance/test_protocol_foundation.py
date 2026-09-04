from __future__ import annotations

import json
from pathlib import Path

import pytest

from jusi.protocol import (
    ProtocolValidationError,
    decode_terminal_output_frame,
    encode_terminal_output_frame,
    validate_command,
    validate_event,
    validate_health_response,
    validate_plugin_catalog,
    validate_plugin_kernel_message,
    validate_terminal_stream_control,
    validate_plugin_worker_message,
)


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "protocol" / "schema" / "v1"
SCENARIO_PATH = ROOT / "protocol" / "fixtures" / "v1" / "scenarios" / "walking-skeleton.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_protocol_foundation_documents_are_valid_json() -> None:
    paths = sorted((ROOT / "protocol").rglob("*.json"))
    assert paths
    for path in paths:
        assert isinstance(load_json(path), dict), path


def test_walking_skeleton_uses_only_declared_command_and_event_kinds() -> None:
    commands_schema = load_json(SCHEMA_DIR / "commands.schema.json")
    events_schema = load_json(SCHEMA_DIR / "events.schema.json")
    scenario = load_json(SCENARIO_PATH)

    declared_commands = {
        definition["allOf"][1]["properties"]["kind"]["const"]
        for definition in commands_schema["$defs"].values()
        if isinstance(definition, dict) and "allOf" in definition
    }
    declared_events = set(events_schema["properties"]["kind"]["enum"])

    assert {command["kind"] for command in scenario["commands"]} <= declared_commands
    assert set(scenario["expected_event_kinds"]) <= declared_events
    assert scenario["assertions"]["kernel_states"] == ["on", "off"]
    assert scenario["assertions"]["result"] == {"media_type": "text/plain", "data": "2"}

    restart = load_json(ROOT / "protocol" / "fixtures" / "v1" / "scenarios" / "restart-notebook.json")
    assert restart["command"]["kind"] in declared_commands
    assert set(restart["expected_event_kinds"]) <= declared_events


def test_python_consumes_shared_event_fixtures() -> None:
    fixture_root = ROOT / "protocol" / "fixtures" / "v1"
    event = load_json(fixture_root / "valid" / "execution-output.json")
    assert validate_event(event)["payload"]["data"] == "\x1b[31mred\x1b[0m"

    invalid = load_json(fixture_root / "invalid" / "event-missing-sequence.json")
    with pytest.raises(ProtocolValidationError, match="sequence"):
        validate_event(invalid)

    scenario = load_json(fixture_root / "scenarios" / "event-payloads.json")
    assert {validate_event(item)["kind"] for item in scenario["events"]} == {
        "service.ready",
        "operation.started",
        "operation.completed",
        "kernel.state_changed",
        "execution.started",
        "execution.output",
        "execution.completed",
        "client.created",
        "client.closed",
        "surface.created",
        "surface.closed",
        "failure.occurred",
    }

    malformed_payload = load_json(fixture_root / "invalid" / "event-output-missing-data.json")
    with pytest.raises(ProtocolValidationError, match="data"):
        validate_event(malformed_payload)

    assert validate_event(load_json(fixture_root / "valid" / "client-created.json"))["kind"] == "client.created"
    assert validate_event(load_json(fixture_root / "valid" / "client-closed.json"))["kind"] == "client.closed"
    terminal_failure = validate_event(
        load_json(fixture_root / "valid" / "failure-terminal-surface.json")
    )
    assert terminal_failure["operation"] == "run_terminal_surface"
    invalid_client = load_json(fixture_root / "invalid" / "client-created-missing-worker.json")
    with pytest.raises(ProtocolValidationError, match="fields"):
        validate_event(invalid_client)

    surface = validate_event(load_json(fixture_root / "valid" / "surface-created.json"))
    assert surface["payload"]["transport"] == {
        "kind": "websocket",
        "endpoint": "/v1/surfaces/surf_fixture/terminal",
        "subprotocol": "jusi.terminal.v1",
    }
    assert validate_event(load_json(fixture_root / "valid" / "surface-closed.json"))["kind"] == "surface.closed"
    invalid_surface = load_json(fixture_root / "invalid" / "surface-created-missing-transport.json")
    with pytest.raises(ProtocolValidationError, match="fields"):
        validate_event(invalid_surface)


def test_python_consumes_shared_close_client_command() -> None:
    command = load_json(ROOT / "protocol" / "fixtures" / "v1" / "valid" / "close-client.json")
    assert validate_command(command, "close_client")["client_id"] == "client_123"


def test_python_consumes_shared_interrupt_command() -> None:
    fixture_root = ROOT / "protocol" / "fixtures" / "v1"
    command = load_json(fixture_root / "valid" / "interrupt-execution.json")
    assert validate_command(command, "interrupt")["execution_id"] == "execution_123"
    with pytest.raises(ProtocolValidationError, match="execution_id"):
        validate_command(
            load_json(fixture_root / "invalid" / "interrupt-missing-execution-id.json"),
            "interrupt",
        )


def test_python_consumes_shared_terminal_stream_controls() -> None:
    fixture_root = ROOT / "protocol" / "fixtures" / "v1"
    for kind in ("attach", "attached", "resize", "resized", "failure"):
        message = load_json(fixture_root / "valid" / f"terminal-stream-{kind}.json")
        assert validate_terminal_stream_control(message)["kind"] == kind

    for name, error in (
        ("terminal-stream-cursor-overflow.json", "uint64"),
        ("terminal-stream-invalid-geometry.json", "rows"),
        ("terminal-stream-invalid-reason.json", "reason"),
    ):
        with pytest.raises(ProtocolValidationError, match=error):
            validate_terminal_stream_control(load_json(fixture_root / "invalid" / name))


def test_python_consumes_shared_terminal_binary_framing_vector() -> None:
    vector = load_json(ROOT / "protocol" / "fixtures" / "v1" / "scenarios" / "terminal-stream-framing.json")
    output = vector["server_output"]
    payload = bytes.fromhex(output["payload_hex"])
    frame = bytes.fromhex(output["frame_hex"])
    assert encode_terminal_output_frame(int(output["starting_cursor"]), payload) == frame
    assert decode_terminal_output_frame(frame) == (int(output["starting_cursor"]), payload)
    assert bytes.fromhex(vector["client_input"]["frame_hex"]) == bytes.fromhex(vector["client_input"]["payload_hex"])

    with pytest.raises(ProtocolValidationError, match="shorter"):
        decode_terminal_output_frame(b"\x01\x01")
    with pytest.raises(ProtocolValidationError, match="unsupported"):
        decode_terminal_output_frame(b"\x02\x01" + b"\x00" * 8)


def test_python_consumes_shared_health_fixtures() -> None:
    fixture_root = ROOT / "protocol" / "fixtures" / "v1"
    response = load_json(fixture_root / "valid" / "health-response.json")
    assert validate_health_response(response)["kernel"]["state"] == "on"
    assert validate_health_response(response)["runtime"]["plugin_catalog"]["plugins"] == []
    assert validate_health_response(response)["surfaces"] == []
    assert validate_health_response(response)["executions"] == []
    active = validate_health_response(load_json(fixture_root / "valid" / "health-active-execution.json"))
    assert active["executions"][0]["outcome"] == "running"

    invalid = load_json(fixture_root / "invalid" / "health-invalid-window.json")
    with pytest.raises(ProtocolValidationError, match="window"):
        validate_health_response(invalid)

    mismatch = load_json(fixture_root / "invalid" / "health-runtime-mismatch.json")
    with pytest.raises(ProtocolValidationError, match="ownership"):
        validate_health_response(mismatch)

    missing_surfaces = load_json(fixture_root / "invalid" / "health-missing-surfaces.json")
    with pytest.raises(ProtocolValidationError, match="surfaces"):
        validate_health_response(missing_surfaces)

    missing_executions = load_json(fixture_root / "invalid" / "health-missing-executions.json")
    with pytest.raises(ProtocolValidationError, match="executions"):
        validate_health_response(missing_executions)


def test_python_consumes_shared_plugin_catalog_fixtures() -> None:
    fixture_root = ROOT / "protocol" / "fixtures" / "v1"
    catalog = load_json(fixture_root / "valid" / "plugin-catalog.json")
    validated = validate_plugin_catalog(catalog)
    assert [plugin["plugin_id"] for plugin in validated["plugins"]] == ["sqlite_provider", "postgres_provider"]
    assert {family["magic_name"] for plugin in validated["plugins"] for family in plugin["families"]} == {"sql"}

    invalid = load_json(fixture_root / "invalid" / "plugin-catalog-duplicate-id.json")
    with pytest.raises(ProtocolValidationError, match="Duplicate"):
        validate_plugin_catalog(invalid)

    conflict = load_json(fixture_root / "invalid" / "plugin-catalog-conflicting-family.json")
    with pytest.raises(ProtocolValidationError, match="Conflicting family"):
        validate_plugin_catalog(conflict)

    short_identity = load_json(fixture_root / "invalid" / "plugin-catalog-short-identity.json")
    with pytest.raises(ProtocolValidationError, match="length"):
        validate_plugin_catalog(short_identity)

    duplicate_module = load_json(fixture_root / "invalid" / "plugin-catalog-duplicate-kernel-module.json")
    with pytest.raises(ProtocolValidationError, match="Kernel extension"):
        validate_plugin_catalog(duplicate_module)


def test_python_consumes_shared_plugin_worker_fixtures() -> None:
    fixture_root = ROOT / "protocol" / "fixtures" / "v1"
    request = load_json(fixture_root / "valid" / "plugin-worker-request.json")
    result = load_json(fixture_root / "valid" / "plugin-worker-result.json")
    assert validate_plugin_worker_message(request)["operation"] == "complete"
    assert validate_plugin_worker_message(result)["result"] == {"items": ["select"]}
    assert validate_plugin_worker_message(result)["core_requests"] == []

    surface_request = validate_plugin_worker_message(
        load_json(fixture_root / "valid" / "plugin-worker-terminal-surface-request.json")
    )
    assert surface_request["core_requests"][0] == {
        "request_id": "core_req_fixture_1",
        "kind": "terminal_surface.create",
        "argv": ["visidata", "--play", "/tmp/jusi-query.vd"],
        "cwd": "/tmp",
        "environment_overrides": {"TERM": "xterm-256color"},
        "capabilities": ["input", "resize", "signal"],
    }

    invalid_surface_request = load_json(
        fixture_root / "invalid" / "plugin-worker-terminal-surface-missing-resize.json"
    )
    with pytest.raises(ProtocolValidationError, match="capabilities"):
        validate_plugin_worker_message(invalid_surface_request)

    missing_core_requests = load_json(
        fixture_root / "invalid" / "plugin-worker-result-missing-core-requests.json"
    )
    with pytest.raises(ProtocolValidationError, match="fields"):
        validate_plugin_worker_message(missing_core_requests)

    invalid = load_json(fixture_root / "invalid" / "plugin-worker-identity-missing.json")
    with pytest.raises(ProtocolValidationError, match="plugin_worker_id"):
        validate_plugin_worker_message(invalid)


def test_python_consumes_shared_plugin_kernel_fixtures() -> None:
    fixture_root = ROOT / "protocol" / "fixtures" / "v1"
    adapters = load_json(fixture_root / "valid" / "plugin-adapters-ready.json")
    handoff = load_json(fixture_root / "valid" / "plugin-handoff.json")
    assert validate_plugin_kernel_message(adapters)["adapters"][0]["module"] == "jusi_sqlite.kernel"
    assert validate_plugin_kernel_message(handoff)["plugin_id"] == "sqlite_provider"
    invalid = load_json(fixture_root / "invalid" / "plugin-handoff-missing-provider.json")
    with pytest.raises(ProtocolValidationError, match="fields"):
        validate_plugin_kernel_message(invalid)
