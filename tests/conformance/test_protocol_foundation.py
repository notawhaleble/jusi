from __future__ import annotations

import json
from pathlib import Path

import pytest

from jusi.protocol import (
    ProtocolValidationError,
    validate_command,
    validate_event,
    validate_health_response,
    validate_plugin_catalog,
    validate_plugin_kernel_message,
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
        "failure.occurred",
    }

    malformed_payload = load_json(fixture_root / "invalid" / "event-output-missing-data.json")
    with pytest.raises(ProtocolValidationError, match="data"):
        validate_event(malformed_payload)

    assert validate_event(load_json(fixture_root / "valid" / "client-created.json"))["kind"] == "client.created"
    assert validate_event(load_json(fixture_root / "valid" / "client-closed.json"))["kind"] == "client.closed"
    invalid_client = load_json(fixture_root / "invalid" / "client-created-missing-worker.json")
    with pytest.raises(ProtocolValidationError, match="fields"):
        validate_event(invalid_client)


def test_python_consumes_shared_close_client_command() -> None:
    command = load_json(ROOT / "protocol" / "fixtures" / "v1" / "valid" / "close-client.json")
    assert validate_command(command, "close_client")["client_id"] == "client_123"


def test_python_consumes_shared_health_fixtures() -> None:
    fixture_root = ROOT / "protocol" / "fixtures" / "v1"
    response = load_json(fixture_root / "valid" / "health-response.json")
    assert validate_health_response(response)["kernel"]["state"] == "on"
    assert validate_health_response(response)["runtime"]["plugin_catalog"]["plugins"] == []

    invalid = load_json(fixture_root / "invalid" / "health-invalid-window.json")
    with pytest.raises(ProtocolValidationError, match="window"):
        validate_health_response(invalid)

    mismatch = load_json(fixture_root / "invalid" / "health-runtime-mismatch.json")
    with pytest.raises(ProtocolValidationError, match="ownership"):
        validate_health_response(mismatch)


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
