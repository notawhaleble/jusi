from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import sys
from typing import Any

from jusi.plugin_api import ENTRY_POINT_GROUP, validate_catalog_claims, validate_discovered_entry
from jusi.protocol import ProtocolValidationError


MAX_MESSAGE_CHARS = 1000


class DiscoveryFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        reason: str,
        entry_point: str = "",
        distribution: str = "",
    ) -> None:
        super().__init__(message[:MAX_MESSAGE_CHARS])
        self.reason = reason
        self.entry_point = entry_point
        self.distribution = distribution

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"reason": self.reason, "message": str(self)}
        if self.entry_point:
            result["entry_point"] = self.entry_point
        if self.distribution:
            result["distribution"] = self.distribution
        return result


def _distribution_identity(
    entry_point: importlib.metadata.EntryPoint,
    distribution: importlib.metadata.Distribution,
) -> tuple[str, str]:
    name = distribution.metadata.get("Name", "")
    version = distribution.version
    if not name or not version:
        raise DiscoveryFailure(
            "Entry-point distribution metadata is incomplete",
            reason="plugin_error",
            entry_point=entry_point.name,
            distribution=name,
        )
    return name, version


def _entry_points(
    search_paths: list[str],
) -> list[tuple[importlib.metadata.EntryPoint, importlib.metadata.Distribution]]:
    distributions = importlib.metadata.distributions(path=search_paths or None)
    discovered = [
        (entry_point, distribution)
        for distribution in distributions
        for entry_point in distribution.entry_points
        if entry_point.group == ENTRY_POINT_GROUP
    ]
    return sorted(
        discovered,
        key=lambda item: (
            item[0].name,
            item[1].metadata.get("Name", ""),
            item[0].value,
        ),
    )


def discover(discovery_id: str, search_paths: list[str]) -> dict[str, Any]:
    for search_path in reversed(search_paths):
        sys.path.insert(0, search_path)

    plugins: list[dict[str, Any]] = []
    for entry_point, entry_point_distribution in _entry_points(search_paths):
        distribution, version = _distribution_identity(entry_point, entry_point_distribution)
        try:
            provider = entry_point.load()
            if not callable(provider):
                raise TypeError("entry point must resolve to a zero-argument callable")
            value = provider()
            plugin = validate_discovered_entry(
                value,
                entry_point_name=entry_point.name,
                distribution=distribution,
                distribution_version=version,
            )
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
                raise
            raise DiscoveryFailure(
                f"{type(exc).__name__}: {exc}",
                reason="plugin_error",
                entry_point=entry_point.name,
                distribution=distribution,
            ) from exc
        plugins.append(plugin)

    catalog = {
        "protocol_version": 1,
        "catalog_version": 1,
        "discovery_id": discovery_id,
        "plugins": sorted(plugins, key=lambda plugin: (plugin["plugin_id"], plugin["distribution"])),
    }
    try:
        return validate_catalog_claims(catalog)
    except ProtocolValidationError as exc:
        raise DiscoveryFailure(str(exc), reason="conflict") from exc


def _write_result(path: Path, result: dict[str, Any]) -> None:
    temporary_path = path.with_name(path.name + ".tmp")
    temporary_path.write_text(json.dumps(result, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary_path, path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--discovery-id", required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--search-path", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        catalog = discover(args.discovery_id, args.search_path)
        result = {"ok": True, "child_pid": os.getpid(), "catalog": catalog}
    except DiscoveryFailure as exc:
        result = {"ok": False, "child_pid": os.getpid(), "failure": exc.to_dict()}
    _write_result(args.result, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
