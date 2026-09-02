from __future__ import annotations

from typing import Any

from IPython.core.error import UsageError
from IPython.display import display


HANDOFF_MIME = "application/vnd.jusi.handoff.v1+json"
_runtime_configuration: dict[str, Any] = {}


def jusi_kernel_adapter_v1() -> dict[str, Any]:
    return {
        "plugin_id": "sqlite",
        "plugin_version": "1.0.0",
        "families": [{"family_id": "sql", "magic_name": "sql"}],
    }


def configure_jusi_runtime_v1(configuration: dict[str, Any]) -> None:
    global _runtime_configuration
    _runtime_configuration = dict(configuration)


def load_ipython_extension(ipython) -> None:  # type: ignore[no-untyped-def]
    def sql_magic(line: str, cell: str) -> None:
        alias = line.strip()
        if not alias:
            raise UsageError("%%sql requires a target alias")
        sql = _runtime_configuration.get("sql")
        target = sql.get(alias) if isinstance(sql, dict) else None
        if not isinstance(target, dict):
            raise UsageError(f"Unknown SQL target alias {alias!r}")
        provider = str(target.get("provider", "")).strip()
        if provider != "sqlite":
            raise UsageError(f"SQL target {alias!r} does not select the sqlite provider")
        display({HANDOFF_MIME: {
            "protocol_version": 1,
            "kind": "plugin.handoff",
            "plugin_id": "sqlite",
            "plugin_version": "1.0.0",
            "family_id": "sql",
            "magic_name": "sql",
            "payload": {
                "alias": alias,
                "query": cell,
                "target": dict(target),
            },
        }}, raw=True)

    ipython.register_magic_function(sql_magic, "cell", "sql")
