from __future__ import annotations

import json
from pathlib import Path

import pytest

from jusi.protocol import ProtocolValidationError, validate_command, validate_event, validate_health_response

ROOT = Path(__file__).resolve().parents[2] / 'protocol' / 'fixtures' / 'v1'


def fixture(name):
    return json.loads((ROOT / name).read_text())


def test_kernel_input_shared_contract():
    scenario = fixture('scenarios/kernel-input.json')
    validate_command(scenario['command'], 'submit_input')
    for value in ['', '  ololo  ', 'α\nβ']:
        validate_command({**scenario['command'], 'value': value}, 'submit_input')
    for event in scenario['events']:
        validate_event(event)
    validate_health_response(fixture('valid/health-pending-input.json'))
    with pytest.raises(ProtocolValidationError):
        validate_health_response(fixture('invalid/health-input-wrong-execution.json'))
    with pytest.raises(ProtocolValidationError):
        validate_command(fixture('invalid/input-command-missing-identity.json'), 'submit_input')


@pytest.mark.parametrize('name', ['input-request-missing-identity', 'input-request-invalid-password', 'input-reply-exposes-value'])
def test_invalid_kernel_input_events(name):
    with pytest.raises(ProtocolValidationError):
        validate_event(fixture(f'invalid/{name}.json'))
