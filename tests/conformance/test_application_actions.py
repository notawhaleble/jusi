import json
from pathlib import Path
import pytest
from jusi.protocol import (ProtocolValidationError, validate_application_action, validate_command,
                           validate_event, validate_editor_action_ack, validate_editor_action_fetch, validate_terminal_stream_control, validate_health_response)
ROOT = Path(__file__).resolve().parents[2] / "protocol/fixtures/v1"
def read(name):
    return json.loads((ROOT / name).read_text())

def test_application_action_shared_contract():
    for name in ["application-editor-action", "application-editor-result", "application-editor-begin", "application-editor-chunk"]:
        validate_application_action(read(f"valid/{name}.json"))
    validate_command(read("valid/ack-editor-action.json"), "ack_editor_action")
    validate_event(read("valid/editor-action-requested.json"))
    fetched = read("valid/editor-action-fetch.json")
    validate_editor_action_fetch(fetched)
    validate_editor_action_fetch(read("valid/editor-action-fetch-chunk.json"))
    for name in ["application-action-wrong-kind", "application-action-path", "application-empty-chunk", "application-chunk-nul", "application-begin-text"]:
        with pytest.raises(ProtocolValidationError):
            validate_application_action(read(f"invalid/{name}.json"))
    with pytest.raises(ProtocolValidationError):
        validate_event(read("invalid/editor-action-wrong-owner.json"))


@pytest.mark.parametrize("validator,valid,invalids", [
    (validate_editor_action_ack, "editor-action-ack", ["editor-action-ack-outcome"]),
    (validate_editor_action_fetch, "editor-action-fetch", ["editor-action-fetch-expiry", "editor-action-fetch-kind", "editor-action-fetch-offset", "editor-action-fetch-partial"]),
    (validate_terminal_stream_control, "terminal-stream-editor-attach", ["terminal-stream-editor-id"]),
    (validate_health_response, "health-editor-action", ["health-editor-action-owner"]),
])
def test_application_delivery_boundaries(validator, valid, invalids):
    validator(read(f"valid/{valid}.json"))
    for name in invalids:
        with pytest.raises(ProtocolValidationError):
            validator(read(f"invalid/{name}.json"))


def test_application_ack_rejects_uncertain_frontend_outcome():
    with pytest.raises(ProtocolValidationError):
        validate_command(read("invalid/ack-editor-action-outcome.json"), "ack_editor_action")


def test_text_chunk_byte_bounds_do_not_limit_total_content():
    chunk = read("valid/application-editor-chunk.json")
    validate_application_action({**chunk, "text": "α" * 32768})
    with pytest.raises(ProtocolValidationError):
        validate_application_action({**chunk, "text": "α" * 32769})
    fetched = read("valid/editor-action-fetch-chunk.json")
    fetched["content"]["text"] = "x" * 65537
    fetched["next_offset"] = 65537
    with pytest.raises(ProtocolValidationError):
        validate_editor_action_fetch(fetched)
