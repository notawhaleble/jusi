import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from jusi.protocol import (ProtocolValidationError, validate_attention, validate_attention_request,
    validate_application_action, validate_attention_ack, validate_command, validate_event, validate_health_response)

ROOT = Path(__file__).resolve().parents[2] / 'protocol'

def read(name):
    return json.loads((ROOT / 'fixtures/v1' / (name + '.json')).read_text())

CASES = [
    (validate_attention, 'attention', 'attention.schema.json#/$defs/record', ['attention-control-message', 'attention-kind', 'attention-revision']),
    (validate_attention_request, 'attention-request', 'attention.schema.json#/$defs/request', ['attention-request-owner', 'attention-update-missing-id']),
    (validate_attention_request, 'attention-update', 'attention.schema.json#/$defs/request', []),
    (validate_attention_request, 'attention-clear', 'attention.schema.json#/$defs/request', []),
    (validate_application_action, 'application-attention', 'editor-actions.schema.json', []),
    (validate_application_action, 'application-attention-result', 'editor-actions.schema.json', []),
    (validate_attention_ack, 'attention-ack', 'attention.schema.json#/$defs/ack', []),
    (lambda value: validate_command(value, 'dismiss_attention'), 'dismiss-attention', 'commands.schema.json', ['dismiss-attention-revision']),
    (validate_event, 'attention-event', 'events.schema.json', ['attention-event-owner']),
    (validate_health_response, 'health-attention', 'responses.schema.json', ['health-attention-owner', 'health-attention-duplicate']),
]

@pytest.mark.parametrize('validator,name,schema,invalid', CASES)
def test_shared_attention_contract(validator, name, schema, invalid):
    value = read('valid/' + name)
    validator(value)
    resources = [(data['$id'], Resource.from_contents(data)) for data in
        (json.loads(p.read_text()) for p in (ROOT / 'schema/v1').glob('*.json'))]
    registry = Registry().with_resources(resources)
    checker = Draft202012Validator({'$ref': 'https://jusi.dev/protocol/v1/' + schema}, registry=registry)
    checker.validate(value)
    for bad in invalid:
        with pytest.raises(ProtocolValidationError): validator(read('invalid/' + bad))
        # Cross-resource identity equality/uniqueness is enforced by the two
        # language validators rather than JSON Schema's structural checks.
        if bad not in {'attention-event-owner', 'health-attention-owner', 'health-attention-duplicate'}:
            assert list(checker.iter_errors(read('invalid/' + bad)))


def test_shared_attention_lifecycle():
    scenario = read('scenarios/attention-lifecycle')
    validate_attention_request(scenario['request'])
    for field in ('pending', 'update', 'clear'): validate_attention(scenario[field])
