import json
from pathlib import Path

import pytest
from jusi.domain.palette import build_palette
from jusi.protocol import ProtocolValidationError, validate_health_response

ROOT = Path(__file__).resolve().parents[2] / 'protocol/fixtures/v1'


def test_public_palette_contains_only_discovered_magic_alias_names():
    health = json.loads((ROOT / 'valid/health-palette.json').read_text())
    validate_health_response(health)
    catalog = health['runtime']['plugin_catalog']
    magic = next(iter(health['runtime']['palette']))
    result = build_palette(catalog, {magic: {'main': {'token': 'secret'}, 'setting': 'value'}, 'unknown': {'private': {}}})
    assert result[magic] == {'entries': ['main']}
    assert 'secret' not in json.dumps(result) and 'unknown' not in result
    scenario = json.loads((ROOT / 'scenarios/palette.json').read_text())
    assert scenario['commands'][-1].startswith('J! ')


@pytest.mark.parametrize('suffix', ['duplicate', 'value', 'fields'])
def test_invalid_palette(suffix):
    with pytest.raises(ProtocolValidationError):
        validate_health_response(json.loads((ROOT / f'invalid/health-palette-{suffix}.json').read_text()))
