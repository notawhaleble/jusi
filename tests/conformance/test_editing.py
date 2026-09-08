import json
from pathlib import Path

import pytest
from jusi.protocol import ProtocolValidationError, validate_plugin_catalog

ROOT = Path(__file__).resolve().parents[2] / "protocol/fixtures/v1"


def test_editing_catalog_contract():
    scenario = json.loads((ROOT / "scenarios/cell-editing.json").read_text())
    catalog = scenario["catalog"]
    assert catalog == json.loads((ROOT / "valid/plugin-catalog-editing.json").read_text())
    validate_plugin_catalog(catalog)
    assert catalog["plugins"][1]["families"][0]["provider_presentation"] == scenario["after_execution"]
    for suffix in ["unsafe", "command"]:
        with pytest.raises(ProtocolValidationError):
            validate_plugin_catalog(json.loads((ROOT / f"invalid/plugin-catalog-editing-{suffix}.json").read_text()))
