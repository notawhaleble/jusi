import json
from pathlib import Path

import pytest

from jusi.protocol import (ProtocolValidationError, validate_editor_action,
                           validate_application_action, validate_editor_action_fetch,
                           validate_command, validate_event)
from jusi.plugin_api import show_diff

ROOT = Path(__file__).resolve().parents[2] / "protocol/fixtures/v1"


def read(name):
    return json.loads((ROOT / (name + ".json")).read_text())


def test_shared_diff_contract_and_worker_helper():
    content = read("valid/editor-show-diff")
    validate_editor_action(content, "show_diff")
    validate_application_action(read("valid/application-diff-begin"))
    validate_editor_action_fetch(read("valid/editor-diff-fetch"))
    validate_command(read("valid/editor-diff-command"), "editor_action")
    validate_event(read("valid/editor-diff-requested"))
    assert show_diff("α\n", "β\n", before_name="before.py", after_name="after.py", filetype="python").result == content


@pytest.mark.parametrize("name", ["boundary", "path", "nul", "accept"])
def test_invalid_diff_contract(name):
    with pytest.raises(ProtocolValidationError):
        validate_editor_action(read("invalid/editor-diff-" + name), "show_diff")
