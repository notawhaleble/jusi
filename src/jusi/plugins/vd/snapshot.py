"""Data-only snapshots: no pickle, imports by payload, or remote evaluation."""
from __future__ import annotations

import base64
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import json
import math

MAX_DEPTH = 64


class SnapshotError(ValueError):
    pass


def dumps(value) -> str:
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return encoded


def capture(value):
    active = set()

    def visit(obj, depth=0):
        if depth > MAX_DEPTH:
            raise SnapshotError("%%vd value is too deeply nested")
        kind = type(obj)
        if obj is None or kind in (bool, int, str):
            return obj
        if kind is float:
            return obj if math.isfinite(obj) else {"type": "float", "value": str(obj)}
        if kind is bytes:
            return {"type": "bytes", "value": base64.b64encode(obj).decode("ascii")}
        if kind in (date, datetime, time, Decimal):
            return {"type": kind.__name__, "value": str(obj) if kind is Decimal else obj.isoformat()}
        if kind is timedelta:
            return {"type": "timedelta", "value": [obj.days, obj.seconds, obj.microseconds]}
        identity = id(obj)
        if identity in active:
            raise SnapshotError("%%vd cannot snapshot cyclic values")
        active.add(identity)
        try:
            module = kind.__module__.split(".")[0]
            if module == "pandas":
                if kind.__name__ in ("NAType", "NaTType"):
                    return None
                if kind.__name__ == "Timestamp":
                    return visit(obj.isoformat(), depth + 1)
                if kind.__name__ == "Timedelta":
                    return visit(str(obj), depth + 1)
                if kind.__name__ in ("DataFrame", "Series"):
                    frame = obj.to_frame() if kind.__name__ == "Series" else obj
                    return {"type": "table", "columns": [visit(str(col), depth + 1) for col in frame.columns],
                            "index_name": str(frame.index.name or "index"),
                            "rows": [visit([index, *row], depth + 1) for index, row in
                                     zip(frame.index, frame.itertuples(index=False, name=None))]}
            if module == "numpy":
                if getattr(obj, "ndim", 0) == 0:
                    return visit(obj.item(), depth + 1)
                return visit(obj.tolist(), depth + 1)
            if kind in (list, tuple, set, frozenset):
                return {"type": kind.__name__, "items": [visit(item, depth + 1) for item in obj]}
            if kind is dict:
                return {"type": "dict", "items": [[visit(k, depth + 1), visit(v, depth + 1)] for k, v in obj.items()]}
            raise SnapshotError(f"%%vd cannot snapshot {kind.__module__}.{kind.__name__}; convert it to a table or Python containers")
        finally:
            active.remove(identity)

    result = {"snapshot_version": 1, "value": visit(value)}
    return result


def restore(snapshot):
    """Validate and reconstruct only the explicitly supported data types."""
    if not isinstance(snapshot, dict) or set(snapshot) != {"snapshot_version", "value"} or snapshot["snapshot_version"] != 1:
        raise SnapshotError("Invalid %%vd snapshot envelope")

    def visit(node, depth=0):
        if depth > MAX_DEPTH:
            raise SnapshotError("Invalid %%vd snapshot bounds")
        if node is None or type(node) in (bool, int, str):
            return node
        if type(node) is float and math.isfinite(node):
            return node
        if not isinstance(node, dict):
            raise SnapshotError("Invalid %%vd snapshot node")
        kind = node.get("type")
        if kind in ("list", "tuple", "set", "frozenset", "dict"):
            if set(node) != {"type", "items"} or not isinstance(node["items"], list):
                raise SnapshotError("Invalid %%vd container")
            if kind == "dict":
                if any(not isinstance(pair, list) or len(pair) != 2 for pair in node["items"]):
                    raise SnapshotError("Invalid %%vd mapping")
                return {visit(k, depth + 1): visit(v, depth + 1) for k, v in node["items"]}
            return {"list": list, "tuple": tuple, "set": set, "frozenset": frozenset}[kind](visit(v, depth + 1) for v in node["items"])
        if kind == "table":
            if (set(node) != {"type", "columns", "index_name", "rows"}
                    or not isinstance(node["columns"], list) or not all(isinstance(c, str) for c in node["columns"])
                    or not isinstance(node["index_name"], str) or not isinstance(node["rows"], list)):
                raise SnapshotError("Invalid %%vd table")
            rows = [visit(row, depth + 1) for row in node["rows"]]
            if any(not isinstance(row, list) or len(row) != len(node["columns"]) + 1 for row in rows):
                raise SnapshotError("Invalid %%vd table row")
            return Table([node["index_name"], *node["columns"]], rows)
        if set(node) != {"type", "value"}:
            raise SnapshotError("Invalid %%vd scalar")
        value = node["value"]
        if kind == "timedelta" and isinstance(value, list) and len(value) == 3 and all(type(n) is int for n in value):
            return timedelta(days=value[0], seconds=value[1], microseconds=value[2])
        if not isinstance(value, str):
            raise SnapshotError("Invalid %%vd scalar value")
        if kind == "float" and value in ("nan", "inf", "-inf"):
            return float(value)
        if kind == "bytes":
            return base64.b64decode(value, validate=True)
        if kind in ("date", "datetime", "time"):
            return {"date": date, "datetime": datetime, "time": time}[kind].fromisoformat(value)
        if kind == "Decimal":
            return Decimal(value)
        raise SnapshotError("Unknown %%vd snapshot type")

    try:
        return visit(snapshot["value"])
    except (TypeError, ValueError, OverflowError) as exc:
        raise SnapshotError(str(exc)) from exc


class Table:
    def __init__(self, columns, rows):
        self.columns, self.rows = columns, rows
