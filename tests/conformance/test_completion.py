import json
from pathlib import Path

import pytest
from jusi.protocol import ProtocolValidationError, validate_command, validate_completion, validate_event

ROOT = Path(__file__).resolve().parents[2] / "protocol/fixtures/v1"


def test_shared_completion_contract():
    scenario = json.loads((ROOT / "scenarios/completion.json").read_text())
    command = validate_command(scenario["command"], "complete")
    for event in scenario["events"]:
        validate_event(event)
    result = validate_completion(scenario["completion"], command["cursor_pos"])
    item = result["items"][0]
    assert command["body"][:item["start"]] + item["text"] + command["body"][item["end"]:] == scenario["expected_body"]
    validate_command({**command, "body": "", "cursor_pos": 0, "client_id": "client_test"}, "complete")
    validate_completion({"items": [{"text": "schema", "start": 0, "end": 0}]}, 0)
    for name in ["suffix-range", "reversed-range", "fractional-range"]:
        with pytest.raises(ProtocolValidationError):
            validate_completion(json.loads((ROOT / f"invalid/completion-{name}.json").read_text()), 3)
    with pytest.raises(ProtocolValidationError):
        validate_command(json.loads((ROOT / "invalid/completion-cursor-outside-body.json").read_text()), "complete")


@pytest.mark.parametrize("cursor", [True, -1, 3.5, "3"])
def test_completion_rejects_invalid_cursor_units(cursor):
    command = json.loads((ROOT / "valid/complete.json").read_text())
    with pytest.raises(ProtocolValidationError):
        validate_command({**command, "cursor_pos": cursor}, "complete")
