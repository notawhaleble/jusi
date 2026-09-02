from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

from jusi.plugin_api import WorkerResult, terminal_surface


class SqliteWorker:
    def __init__(self, context) -> None:  # type: ignore[no-untyped-def]
        self.context = context
        self.payload_path: Path | None = None
        self.runtime_directory: Path | None = None

    def handle(self, operation: str, payload: dict[str, Any]) -> WorkerResult:
        if operation != "execute":
            raise ValueError(f"unsupported SQLite fixture operation: {operation}")
        alias = str(payload.get("alias", "")).strip()
        query = str(payload.get("query", "")).strip()
        target = payload.get("target")
        if not alias or not query or not isinstance(target, dict):
            raise ValueError("invalid SQLite fixture handoff")
        self.runtime_directory = Path(tempfile.mkdtemp(prefix="jusi-sqlite-"))
        self.runtime_directory.chmod(0o700)
        self.payload_path = self.runtime_directory / "payload.json"
        try:
            fd = os.open(self.payload_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"alias": alias, "query": query, "target": target}, stream)
        except BaseException:
            self.close()
            raise
        return WorkerResult(
            {"accepted": True},
            (terminal_surface(
                "sqlite_visidata",
                (sys.executable, "-m", "jusi_sqlite_application", str(self.payload_path)),
                environment_overrides={
                    "TERM": "xterm-256color",
                    "HOME": str(self.runtime_directory),
                    "XDG_CONFIG_HOME": str(self.runtime_directory / "config"),
                    "XDG_CACHE_HOME": str(self.runtime_directory / "cache"),
                    "VD_DIR": str(self.runtime_directory / "visidata"),
                },
                signal=True,
            ),),
        )

    def close(self) -> None:
        if self.runtime_directory is not None:
            shutil.rmtree(self.runtime_directory, ignore_errors=True)
        self.payload_path = None
        self.runtime_directory = None


def create_worker(context) -> SqliteWorker:  # type: ignore[no-untyped-def]
    return SqliteWorker(context)
