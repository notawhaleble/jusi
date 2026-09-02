from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


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
    if len(sys.argv) != 2:
        raise SystemExit("fixture application requires one private payload path")
    raise SystemExit(run_application(Path(sys.argv[1])))
