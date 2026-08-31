from __future__ import annotations

import pytest

from jusi.plugin_api import validate_catalog_claims
from jusi.protocol import ProtocolValidationError


def plugin(plugin_id: str, family_id: str, magic_name: str, *, syntax: str = "sql") -> dict:
    return {
        "plugin_id": plugin_id,
        "plugin_version": "1.0.0",
        "distribution": f"jusi-{plugin_id}",
        "families": [
            {
                "family_id": family_id,
                "magic_name": magic_name,
                "capabilities": ["execute", "complete"],
                "presentation": {"syntax": syntax, "indent": syntax},
            }
        ],
        "kernel_extensions": [],
        "worker_entry_point": None,
        "media_types": ["text/plain"],
        "interaction": "noninteractive",
    }


def catalog(*plugins: dict) -> dict:
    return {
        "protocol_version": 1,
        "catalog_version": 1,
        "discovery_id": "discovery_test",
        "plugins": list(plugins),
    }


def test_shared_family_claims_require_the_same_descriptor() -> None:
    validated = validate_catalog_claims(catalog(plugin("sqlite", "sql", "sql"), plugin("postgres", "sql", "sql")))
    assert [item["plugin_id"] for item in validated["plugins"]] == ["sqlite", "postgres"]

    with pytest.raises(ProtocolValidationError, match="Conflicting family"):
        validate_catalog_claims(catalog(plugin("sqlite", "sql", "sql"), plugin("postgres", "sql", "sql", syntax="pgsql")))


def test_one_magic_cannot_name_incompatible_families() -> None:
    with pytest.raises(ProtocolValidationError, match="incompatible families"):
        validate_catalog_claims(catalog(plugin("one", "sql", "query"), plugin("two", "warehouse", "query")))
