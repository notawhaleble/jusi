import json
from pathlib import Path

import pytest
from jusi.protocol import ProtocolValidationError, validate_command, validate_plugin_catalog

ROOT = Path(__file__).resolve().parents[2] / 'protocol/fixtures/v1'


def test_shared_bootstrap_contract():
    command = json.loads((ROOT / 'valid/bootstrap.json').read_text())
    assert validate_command(command, 'execute')['bootstrap'] is True
    validate_plugin_catalog(json.loads((ROOT / 'valid/bootstrap-catalog.json').read_text()))
    with pytest.raises(ProtocolValidationError):
        validate_command(json.loads((ROOT / 'invalid/bootstrap-nonboolean.json').read_text()), 'execute')
