from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

from IPython.core.error import UsageError
from IPython.display import display

from jusi.plugin_api import WorkerResult, terminal_surface


HANDOFF_MIME = "application/vnd.jusi.handoff.v1+json"
_runtime_configuration: dict[str, Any] = {}


def catalog_entry() -> dict[str, Any]:
    return {
        "plugin_id": "sqlite",
        "plugin_version": "1.0.0",
        "distribution": "jusi-sqlite-fixture",
        "families": [{
            "family_id": "sql",
            "magic_name": "sql",
            "capabilities": ["execute"],
        }],
        "kernel_extensions": ["jusi_sqlite_fixture"],
        "worker_entry_point": "jusi_sqlite_fixture:create_worker",
        "media_types": ["text/x-ansi"],
        "interaction": "terminal_interactive",
    }


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
                (sys.executable, "-m", "jusi_sqlite_fixture", "--application", str(self.payload_path)),
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


def _read_payload(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    if not isinstance(value, dict):
        raise ValueError("invalid SQLite terminal payload")
    return value


def _query_rows(payload: dict[str, Any]) -> tuple[list[str], list[list[Any]], str | None]:
    target = payload.get("target")
    if not isinstance(target, dict):
        return ["error"], [["invalid SQLite target configuration"]], "invalid target"
    database = str(target.get("path", "")).strip()
    if not database:
        return ["error"], [["SQLite target requires path"]], "missing path"
    uri = Path(database).resolve().as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        try:
            cursor = connection.execute(str(payload.get("query", "")))
            description = cursor.description
            if description is None:
                return ["result"], [["done"]], None
            columns = [str(item[0]) for item in description]
            return columns, [list(row) for row in cursor.fetchall()], None
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return ["error"], [[f"{type(exc).__name__}: {exc}"]], str(exc)


def run_application(payload_path: Path) -> int:
    from visidata import ItemColumn, SequenceSheet, vd

    payload = _read_payload(payload_path)
    alias = str(payload.get("alias", "sqlite"))
    columns, rows, error = _query_rows(payload)
    sheet = SequenceSheet(f"jusi-sql:{alias}", rows=rows)
    sheet.addColumn(*(ItemColumn(name, index) for index, name in enumerate(columns)))
    # This central fixture must not read or persist user VisiData state. The
    # production SQL plugin will own its target-side VisiData configuration.
    vd.options.nothing = True
    vd.options.disp_menu = False
    vd.options.disp_status_fmt = "Jusi SQL {sheet.name}"
    if error:
        vd.status(f"SQL error: {error}")
    vd.run(sheet)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--application":
        raise SystemExit("fixture module is not a user command")
    raise SystemExit(run_application(Path(sys.argv[2])))
