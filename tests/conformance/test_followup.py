import json
from pathlib import Path

import pytest

from jusi.protocol import ProtocolValidationError, validate_command, validate_event

ROOT = Path(__file__).resolve().parents[2] / "protocol/fixtures/v1"


def test_followup_shared_contract():
    scenario = json.loads((ROOT / "scenarios/client-followup.json").read_text())
    for body in ["", "  literal body  \nα"]:
        validate_command({**scenario["command"], "body": body}, "followup")
    for event in scenario["events"]:
        validate_event(event)
    for name in ["followup-missing-client", "followup-nonstring-body"]:
        with pytest.raises(ProtocolValidationError):
            validate_command(json.loads((ROOT / f"invalid/{name}.json").read_text()), "followup")
