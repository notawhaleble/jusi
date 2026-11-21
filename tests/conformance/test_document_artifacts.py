import json
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from jusi.protocol import (validate_editor_action, validate_editor_reply, validate_command,
                           validate_application_action, validate_document_offer_reply, ProtocolValidationError)
ROOT = Path(__file__).resolve().parents[2] / 'protocol'
CASES = [
    *[(name + '-tab', lambda v: validate_editor_action(v, v['action']), 'responses.schema.json#/$defs/editor_action', [name + '-placement'])
      for name in ('open-document', 'offer-document', 'open-shared-document')],
    ('offer-document', lambda v: validate_editor_action(v, 'offer_document'), 'responses.schema.json#/$defs/editor_action', ['document-offer-token']),
    ('open-shared-document', lambda v: validate_editor_action(v, 'open_shared_document'), 'responses.schema.json#/$defs/editor_action', ['shared-document-path', 'shared-document-no-session']),
    ('document-offer-reply', validate_document_offer_reply, 'editor-actions.schema.json#/$defs/document_offer_reply', ['document-offer-reply-path']),
    ('open-document', lambda v: validate_editor_action(v, 'open_document'), 'responses.schema.json#/$defs/editor_action', ['document-provider-command']),
    ('source-snapshot', lambda v: validate_editor_action(v, 'source_snapshot'), 'responses.schema.json#/$defs/editor_action', ['source-path-context']),
    ('close-document', lambda v: validate_editor_action(v, 'close_document'), 'responses.schema.json#/$defs/editor_action', []),
    ('editor-artifact-reply', validate_editor_reply, 'editor-actions.schema.json#/$defs/upload_reply', ['reply-negative-offset', 'reply-empty-chunk']),
    ('followup-source-context', lambda v: validate_command(v, 'followup'), 'commands.schema.json', ['followup-source-path']),
    ('application-reply-chunk', validate_application_action, 'editor-actions.schema.json', []),
]
@pytest.mark.parametrize('name,validate,schema,invalid', CASES)
def test_shared_document_contract(name, validate, schema, invalid):
    resources = [Resource.from_contents(json.loads(p.read_text())) for p in (ROOT / 'schema/v1').glob('*.json')]
    registry = Registry().with_resources((r.id(), r) for r in resources)
    checker = Draft202012Validator({'$ref': 'https://jusi.dev/protocol/v1/' + schema}, registry=registry)
    value = json.loads((ROOT / 'fixtures/v1/valid' / (name + '.json')).read_text())
    checker.validate(value); validate(value)
    for name in invalid:
        value = json.loads((ROOT / 'fixtures/v1/invalid' / (name + '.json')).read_text())
        assert not checker.is_valid(value)
        with pytest.raises(ProtocolValidationError): validate(value)


def test_shared_artifact_records(tmp_path):
    from jusi.artifacts import decode
    schema = json.loads((ROOT / 'schema/v1/artifacts.schema.json').read_text())
    checker = Draft202012Validator(schema)
    for validity, name in [('valid', 'artifact-stream'), ('valid', 'artifact-stream-unicode'), ('invalid', 'artifact-stream-path')]:
        records = json.loads((ROOT / f'fixtures/v1/{validity}/{name}.json').read_text())['records']
        wire = tmp_path / 'wire'; wire.write_text(''.join(json.dumps(r) + '\n' for r in records))
        destination = tmp_path / name; destination.mkdir()
        if validity == 'valid':
            for record in records: checker.validate(record)
            decode(wire, destination)
            assert (destination / records[0]['files'][0]['name']).read_bytes() == b'abc\0xyz'
        else:
            assert not checker.is_valid(records[0])
            with pytest.raises(ValueError): decode(wire, destination)
