"""Public, generation-local command names derived from private target config."""
from collections.abc import Mapping
from typing import Any


def build_palette(catalog: dict[str, Any], configuration: dict[str, Any]) -> dict[str, Any]:
    palette = {}
    for plugin in catalog["plugins"]:
        for family in plugin["families"]:
            magic = family["magic_name"]
            section = configuration.get(magic, {})
            # The legacy alias convention is [magic.alias]. Scalar settings are
            # not aliases; no configuration values cross the public boundary.
            entries = sorted(
                name for name, value in section.items()
                if isinstance(name, str) and name and not any(c.isspace() for c in name)
                and isinstance(value, Mapping)
            ) if isinstance(section, Mapping) else []
            palette[magic] = {"entries": entries}
    return palette
