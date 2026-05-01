from __future__ import annotations

import os
import tempfile
import json
from typing import Any, Sequence
from types import MethodType
import threading
import uuid


JUSI_VISIDATARC_ENV = "JUSI_VISIDATARC_CONTENT"
JUSI_PLUGIN_FRONTEND_ACTIONS_ENV = "JUSI_PLUGIN_FRONTEND_ACTIONS_FILE"
JUSI_VISIDATA_EDIT_TIMEOUT_ENV = "JUSI_VISIDATA_EDIT_TIMEOUT_SECONDS"


_PENDING_ACTION_RESULTS: dict[str, tuple[threading.Event, dict[str, Any]]] = {}


def normalize_visidatarc_content(content: object) -> str:
    if not isinstance(content, str):
        return ""
    stripped = content.strip()
    if not stripped:
        return ""
    return content if content.endswith("\n") else content + "\n"


def visidatarc_content_from_env() -> str:
    return normalize_visidatarc_content(os.environ.get(JUSI_VISIDATARC_ENV, ""))


def load_visidatarc_from_env() -> bool:
    content = visidatarc_content_from_env()
    if not content:
        return False
    import visidata

    fd, path = tempfile.mkstemp(prefix="jusi-visidatarc-", suffix=".py")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        visidata.vd.loadConfigFile(path)
        return True
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def plugin_frontend_actions_path_from_env() -> str:
    return str(os.environ.get(JUSI_PLUGIN_FRONTEND_ACTIONS_ENV, "")).strip()


def append_plugin_frontend_action(action_type: str, payload: dict[str, Any]) -> bool:
    path = plugin_frontend_actions_path_from_env()
    if not path:
        return False
    record = {"action_type": str(action_type), "payload": dict(payload)}
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    return True


def _edit_timeout_seconds() -> float | None:
    raw = str(os.environ.get(JUSI_VISIDATA_EDIT_TIMEOUT_ENV, "")).strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def wait_for_frontend_action_result(request_id: str) -> dict[str, Any]:
    event = threading.Event()
    payload: dict[str, Any] = {}
    _PENDING_ACTION_RESULTS[request_id] = (event, payload)
    try:
        timeout = _edit_timeout_seconds()
        completed = event.wait(timeout=timeout)
        if not completed:
            raise TimeoutError("timed out waiting for frontend action result")
        return dict(payload)
    finally:
        _PENDING_ACTION_RESULTS.pop(request_id, None)


def handle_plugin_runtime_control_request(request: dict[str, Any]) -> dict[str, Any]:
    message_type = str(request.get("message_type", "")).strip()
    if message_type != "action_result":
        return {}
    payload = request.get("payload", {})
    if not isinstance(payload, dict):
        return {}
    request_id = str(payload.get("request_id", "")).strip()
    if not request_id:
        return {}
    pending = _PENDING_ACTION_RESULTS.get(request_id)
    if pending is None:
        return {}
    event, result_payload = pending
    result_payload.clear()
    result_payload.update(dict(payload))
    event.set()
    return {}


def request_blocking_edit(path: str, *, line: int | None = None) -> dict[str, Any]:
    request_id = f"edit-{uuid.uuid4().hex}"
    payload: dict[str, Any] = {"request_id": request_id, "path": str(path)}
    if line is not None:
        payload["line"] = int(line)
    append_plugin_frontend_action("edit_path", payload)
    return wait_for_frontend_action_result(request_id)


def _render_visidata_rows(cols: Sequence[Any], rows: Sequence[Any]) -> str:
    rendered_rows: list[str] = []
    for row in rows:
        values: list[str] = []
        for col in cols:
            display = getattr(col, "getDisplayValue", None)
            if callable(display):
                values.append(str(display(row)))
            else:
                values.append(str(row))
        rendered_rows.append("\t".join(values))
    return "\n".join(rendered_rows)


def install_visidata_runtime_hooks() -> bool:
    try:
        import visidata
        from visidata import Sheet, vd
    except ModuleNotFoundError:
        return False

    if getattr(vd, "_jusi_runtime_hooks_installed", False):
        return True

    original_launch_editor = getattr(vd, "launchEditor", None)
    original_launch_external_editor = getattr(vd, "launchExternalEditor", None)

    def syscopy_value(sheet, val):  # type: ignore[no-untyped-def]
        append_plugin_frontend_action("yank_text", {"text": str(val)})
        vd.status("yanked value to editor")

    def syscopy_cells_async(sheet, cols, rows, filetype=None):  # type: ignore[no-untyped-def]
        _ = (sheet, filetype)
        text = _render_visidata_rows(list(cols), list(rows))
        append_plugin_frontend_action("yank_text", {"text": text})
        vd.status("yanked selection to editor")

    def launch_editor(vd_obj, *args):  # type: ignore[no-untyped-def]
        payloads: list[dict[str, Any]] = []
        last_payload: dict[str, Any] | None = None
        for arg in args:
            text = str(arg)
            if text.startswith("+") and text[1:].isdigit() and last_payload is not None and "line" not in last_payload:
                last_payload["line"] = int(text[1:])
                continue
            last_payload = {"path": text}
            payloads.append(last_payload)
        if payloads:
            for payload in payloads:
                append_plugin_frontend_action("open_path", payload)
            emitted = len(payloads)
            vd_obj.status("opened path in editor" if emitted == 1 else f"opened {emitted} paths in editor")
            return 0
        if callable(original_launch_editor):
            return original_launch_editor(*args)
        return 0

    def launch_external_editor(vd_obj, value, linenum=0):  # type: ignore[no-untyped-def]
        if callable(original_launch_editor) and os.environ.get("EDITOR"):
            return original_launch_external_editor(value, linenum) if callable(original_launch_external_editor) else value
        fd, path = tempfile.mkstemp(prefix="jusi-visidata-open-", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(value))
        result = request_blocking_edit(path, line=(int(linenum) if linenum else None))
        if result.get("ok", True) is False:
            return str(value)
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().rstrip("\n")

    def launch_external_editor_path(vd_obj, path, linenum=0):  # type: ignore[no-untyped-def]
        if callable(original_launch_editor) and os.environ.get("EDITOR"):
            if linenum:
                original_launch_editor(path, f"+{linenum}")
            else:
                original_launch_editor(path)
        else:
            request_blocking_edit(str(path), line=(int(linenum) if linenum else None))
        with open(path, "r", encoding="utf-8") as fp:
            try:
                return fp.read().rstrip("\n")
            except Exception as exc:  # pragma: no cover - mirrors visidata behavior
                vd_obj.exceptionCaught(exc)
                return ""

    Sheet.syscopyValue = syscopy_value
    Sheet.syscopyCells_async = syscopy_cells_async
    vd.launchEditor = MethodType(launch_editor, vd)
    vd.launchExternalEditor = MethodType(launch_external_editor, vd)
    vd.launchExternalEditorPath = MethodType(launch_external_editor_path, vd)
    add_globals = getattr(vd, "addGlobals", None)
    if callable(add_globals):
        add_globals(
            launchEditor=vd.launchEditor,
            launchExternalEditor=vd.launchExternalEditor,
            launchExternalEditorPath=vd.launchExternalEditorPath,
        )
    vd._jusi_runtime_hooks_installed = True
    return True
