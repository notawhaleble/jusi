from __future__ import annotations

from typing import Any


def catalog_entry() -> dict[str, Any]:
    return {
        "plugin_id": "sqlite",
        "plugin_version": "1.0.0",
        "distribution": "jusi-sqlite-fixture",
        "families": [{
            "family_id": "sql",
            "presentation": {"syntax": "sql", "indent": "sql"},
            "provider_presentation": {"syntax": "sql", "indent": "sql"},
            "magic_name": "sql",
            "capabilities": ["execute"],
        }],
        "kernel_extensions": ["jusi_sqlite_kernel"],
        "worker_entry_point": "jusi_sqlite_worker:create_worker",
        "media_types": ["text/x-ansi"],
        "interaction": "terminal_interactive",
    }
