from __future__ import annotations

from typing import Any

from jusi.protocol import ProtocolValidationError, validate_plugin_catalog


ENTRY_POINT_GROUP = "jusi.plugins.v1"


def validate_discovered_entry(
    value: object,
    *,
    entry_point_name: str,
    distribution: str,
    distribution_version: str,
) -> dict[str, Any]:
    """Validate one provider result without importing any runtime references."""
    if not isinstance(value, dict):
        raise ProtocolValidationError("Plugin catalog provider must return an object")
    catalog = {
        "protocol_version": 1,
        "catalog_version": 1,
        "discovery_id": "entry_validation",
        "plugins": [value],
    }
    plugin = validate_plugin_catalog(catalog)["plugins"][0]
    if plugin["plugin_id"] != entry_point_name:
        raise ProtocolValidationError("plugin_id must equal the entry-point name")
    if plugin["distribution"] != distribution:
        raise ProtocolValidationError("distribution must match installed distribution metadata")
    if plugin["plugin_version"] != distribution_version:
        raise ProtocolValidationError("plugin_version must match installed distribution metadata")
    return dict(plugin)


def validate_catalog_claims(catalog: object) -> dict[str, Any]:
    """Validate cross-provider family semantics not expressible in JSON Schema."""
    return validate_plugin_catalog(catalog)
