import json
from pathlib import Path
import pytest
from jusi.protocol import (ProtocolValidationError, validate_command, validate_editor_action,
                           validate_plugin_worker_message, validate_event, validate_health_response)
ROOT = Path(__file__).resolve().parents[2] / "protocol/fixtures/v1"
def read(name):
    return json.loads((ROOT / name).read_text())

def test_shared_client_action_contract():
    validate_health_response(read("valid/health-client-operation.json"))
    with pytest.raises(ProtocolValidationError):
        validate_health_response(read("invalid/health-operation-wrong-client.json"))
    with pytest.raises(ProtocolValidationError):
        validate_plugin_worker_message(read("invalid/worker-interrupt-missing-target.json"))
    for name, kind in [("editor-action", "editor_action"), ("interrupt-client", "interrupt_client")]:
        validate_command(read(f"valid/{name}.json"), kind)
    for name in ["worker-rejected", "worker-interrupt"]:
        validate_plugin_worker_message(read(f"valid/{name}.json"))
    for action in ["copy", "open"]:
        validate_editor_action(read(f"valid/editor-{action}.json"), action)
    for name, action in [("editor-open-path", "open"), ("editor-copy-nul", "copy")]:
        with pytest.raises(ProtocolValidationError):
            validate_editor_action(read(f"invalid/{name}.json"), action)
    for name, kind in [("editor-action-path", "editor_action"), ("interrupt-client-missing-operation", "interrupt_client")]:
        with pytest.raises(ProtocolValidationError):
            validate_command(read(f"invalid/{name}.json"), kind)
    for text in ["", "\n", "  α\t\n\n", "x" * 524288]:
        validate_editor_action({"action": "copy", "text": text, "regtype": "v"}, "copy")
    validate_editor_action({"action": "copy", "text": "α" * 262145, "regtype": "v"}, "copy")
