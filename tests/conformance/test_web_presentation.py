import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from jusi.protocol import ProtocolValidationError, validate_editor_action, validate_event

ROOT = Path(__file__).resolve().parents[2] / 'protocol'

def read(name):
    return json.loads((ROOT / 'fixtures/v1' / (name + '.json')).read_text())

@pytest.mark.parametrize('name,validate,schema,bad', [
    ('open-url', lambda v: validate_editor_action(v, 'open_url'), 'responses.schema.json#/$defs/editor_action',
     ['open-url-scheme', 'open-url-newline', 'open-url-empty-host']),
    ('javascript-bundle', validate_event, 'events.schema.json', []),
    ('mime-bundle', validate_event, 'events.schema.json', ['mime-bundle-boundary', 'mime-bundle-media']),
])
def test_shared_web_contract(name, validate, schema, bad):
    resources = [Resource.from_contents(json.loads(p.read_text())) for p in (ROOT / 'schema/v1').glob('*.json')]
    registry = Registry().with_resources((r.id(), r) for r in resources)
    checker = Draft202012Validator({'$ref': 'https://jusi.dev/protocol/v1/' + schema}, registry=registry)
    value = read('valid/' + name)
    validate(value)
    checker.validate(value)
    for name in bad:
        value = read('invalid/' + name)
        with pytest.raises(ProtocolValidationError): validate(value)
        assert list(checker.iter_errors(value))
