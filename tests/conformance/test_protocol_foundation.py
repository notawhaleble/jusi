from __future__ import annotations

import json
from pathlib import Path

import pytest

from jusi.protocol import ProtocolValidationError, validate_event, validate_health_response, validate_plugin_catalog


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
        "failure.occurred",
    }

    malformed_payload = load_json(fixture_root / "invalid" / "event-output-missing-data.json")
    with pytest.raises(ProtocolValidationError, match="data"):
        validate_event(malformed_payload)


def test_python_consumes_shared_health_fixtures() -> None:
    fixture_root = ROOT / "protocol" / "fixtures" / "v1"
    response = load_json(fixture_root / "valid" / "health-response.json")
    assert validate_health_response(response)["kernel"]["state"] == "on"

    invalid = load_json(fixture_root / "invalid" / "health-invalid-window.json")
    with pytest.raises(ProtocolValidationError, match="window"):
        validate_health_response(invalid)


def test_python_consumes_shared_plugin_catalog_fixtures() -> None:
    fixture_root = ROOT / "protocol" / "fixtures" / "v1"
    catalog = load_json(fixture_root / "valid" / "plugin-catalog.json")
    validated = validate_plugin_catalog(catalog)
    assert [plugin["plugin_id"] for plugin in validated["plugins"]] == ["sqlite_provider", "postgres_provider"]
    assert {family["magic_name"] for plugin in validated["plugins"] for family in plugin["families"]} == {"sql"}

    invalid = load_json(fixture_root / "invalid" / "plugin-catalog-duplicate-id.json")
    with pytest.raises(ProtocolValidationError, match="Duplicate"):
        validate_plugin_catalog(invalid)
