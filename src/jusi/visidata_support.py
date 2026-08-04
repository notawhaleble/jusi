from __future__ import annotations

import os
import socket
import tempfile
import json
from typing import Any, Sequence
from types import MethodType
import threading
import uuid

from jusi.infrastructure.debug_timing import emit_timing


JUSI_VISIDATARC_ENV = "JUSI_VISIDATARC_CONTENT"
JUSI_PLUGIN_FRONTEND_ACTIONS_ENV = "JUSI_PLUGIN_FRONTEND_ACTIONS_FILE"
JUSI_PLUGIN_RUNTIME_EVENTS_SOCKET_ENV = "JUSI_PLUGIN_RUNTIME_EVENTS_SOCKET"
JUSI_VISIDATA_EDIT_TIMEOUT_ENV = "JUSI_VISIDATA_EDIT_TIMEOUT_SECONDS"


_PENDING_ACTION_RESULTS: dict[str, tuple[threading.Event, dict[str, Any]]] = {}
_JUSI_RUNTIME_ATTR = "jusi_runtime"
_VISIDATA_RESIZE_LOCK = threading.Lock()
_JUSI_PENDING_RESIZE_ATTR = "_jusi_pending_terminal_resize"
_JUSI_RESIZE_QUEUED_ATTR = "_jusi_terminal_resize_queued"
_JUSI_APPLIED_RESIZE_ATTR = "_jusi_applied_terminal_resize"
_JUSI_RESIZE_COMMAND = "jusi-terminal-resize"


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


def plugin_runtime_events_socket_from_env() -> str:
    return str(os.environ.get(JUSI_PLUGIN_RUNTIME_EVENTS_SOCKET_ENV, "")).strip()


def emit_plugin_runtime_record(record: dict[str, Any]) -> bool:
    record_type = str(record.get("record_type", "")).strip()
    socket_path = plugin_runtime_events_socket_from_env()
    if socket_path:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
                client.connect(socket_path)
                client.sendall(json.dumps(dict(record)).encode("utf-8"))
            emit_timing(
                "plugin_runtime.record.emit",
                record_type=record_type,
                transport="socket",
                socket_path=socket_path,
                status=str(record.get("status", "")).strip(),
            )
            return True
        except OSError as exc:
            emit_timing(
                "plugin_runtime.record.emit_error",
                record_type=record_type,
                transport="socket",
                socket_path=socket_path,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            pass

    path = plugin_frontend_actions_path_from_env()
    if not path:
        emit_timing("plugin_runtime.record.emit_skip", record_type=record_type, reason="no_transport")
        return False
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(record)) + "\n")
    emit_timing(
        "plugin_runtime.record.emit",
        record_type=record_type,
        transport="file",
        path=path,
        status=str(record.get("status", "")).strip(),
    )
    return True


def append_plugin_frontend_action(action_type: str, payload: dict[str, Any]) -> bool:
    record = {"record_type": "action_request", "action_type": str(action_type), "payload": dict(payload)}
    socket_path = plugin_runtime_events_socket_from_env()
    if socket_path:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
                client.connect(socket_path)
                client.sendall(json.dumps(record).encode("utf-8"))
            emit_timing(
                "plugin_runtime.record.emit",
                record_type="action_request",
                action_type=str(action_type),
                transport="socket",
                socket_path=socket_path,
            )
            return True
        except OSError as exc:
            emit_timing(
                "plugin_runtime.record.emit_error",
                record_type="action_request",
                action_type=str(action_type),
                transport="socket",
                socket_path=socket_path,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            pass

    path = plugin_frontend_actions_path_from_env()
    if not path:
        emit_timing("plugin_runtime.record.emit_skip", record_type="action_request", action_type=str(action_type), reason="no_transport")
        return False
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"action_type": str(action_type), "payload": dict(payload)}) + "\n")
    emit_timing(
        "plugin_runtime.record.emit",
        record_type="action_request",
        action_type=str(action_type),
        transport="file",
        path=path,
    )
    return True


def open_plugin_url(url: str, *, open_in: str = "client") -> bool:
    payload: dict[str, Any] = {"url": str(url)}
    target = str(open_in).strip()
    if target:
        payload["open_in"] = target
    return append_plugin_frontend_action("open_url", payload)


def set_plugin_execution_status(status: str) -> bool:
    return emit_plugin_runtime_record({"record_type": "execution_status", "status": str(status)})


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


def bind_visidata_runtime(sheet: Any, runtime: Any) -> None:
    import visidata

    base_sheet = getattr(visidata, "BaseSheet", None)
    setattr(sheet, _JUSI_RUNTIME_ATTR, runtime)
    setattr(sheet, "_jusi_runtime", runtime)
    if base_sheet is not None:
        setattr(base_sheet, _JUSI_RUNTIME_ATTR, runtime)
        setattr(base_sheet, "_jusi_runtime", runtime)
    vd = getattr(visidata, "vd", None)
    if vd is not None:
        setattr(vd, "_jusi_runtime", runtime)


def _visidata_runtime(sheet: Any) -> Any:
    import visidata

    current = sheet
    seen: set[int] = set()
    while current is not None:
        marker = id(current)
        if marker in seen:
            break
        seen.add(marker)
        runtime = getattr(current, _JUSI_RUNTIME_ATTR, None) or getattr(current, "_jusi_runtime", None)
        if runtime is not None:
            return runtime
        current = getattr(current, "source", None)
    base_sheet = getattr(visidata, "BaseSheet", None)
    if base_sheet is not None:
        runtime = getattr(base_sheet, _JUSI_RUNTIME_ATTR, None) or getattr(base_sheet, "_jusi_runtime", None)
        if runtime is not None:
            return runtime
    vd = getattr(visidata, "vd", None)
    if vd is not None:
        runtime = getattr(vd, "_jusi_runtime", None)
        if runtime is not None:
            return runtime
    raise RuntimeError("No active Jusi runtime is bound to the current VisiData session")


def _call_runtime_control(runtime: Any, message_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    names = {
        "followup": ("handle_followup", "followup"),
        "complete": ("handle_complete", "complete"),
    }.get(message_type, ())
    for name in names:
        method = getattr(runtime, name, None)
        if callable(method):
            result = method(dict(payload))
            return dict(result) if isinstance(result, dict) else {}
    return {}


def dispatch_visidata_runtime_message(message_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    import visidata

    vd = getattr(visidata, "vd", None)
    active_sheet = getattr(vd, "activeSheet", None) if vd is not None else None
    if active_sheet is not None:
        method = getattr(active_sheet, f"jusi_{message_type}", None)
        if callable(method):
            result = method(dict(payload))
            return dict(result) if isinstance(result, dict) else {}
        return _call_runtime_control(_visidata_runtime(active_sheet), message_type, payload)
    return _call_runtime_control(_visidata_runtime(None), message_type, payload)


def _terminal_resize_geometry(payload: dict[str, Any]) -> tuple[int, int] | None:
    raw_rows = payload.get("rows")
    raw_cols = payload.get("cols")
    if isinstance(raw_rows, bool) or isinstance(raw_cols, bool):
        return None
    try:
        rows = int(raw_rows)
        cols = int(raw_cols)
    except (TypeError, ValueError):
        return None
    if rows <= 0 or cols <= 0:
        return None
    return rows, cols


def queue_visidata_terminal_resize(payload: dict[str, Any]) -> dict[str, Any]:
    geometry = _terminal_resize_geometry(payload)
    if geometry is None:
        return {"queued": False, "reason": "invalid_geometry"}

    import curses
    import visidata

    vd = getattr(visidata, "vd", None)
    queue_command = getattr(vd, "queueCommand", None) if vd is not None else None
    if vd is None or not callable(queue_command):
        return {"queued": False, "reason": "runtime_not_ready"}

    with _VISIDATA_RESIZE_LOCK:
        already_applied = getattr(vd, _JUSI_APPLIED_RESIZE_ATTR, None) == geometry
        command_queued = bool(getattr(vd, _JUSI_RESIZE_QUEUED_ATTR, False))
        pending = getattr(vd, _JUSI_PENDING_RESIZE_ATTR, None)
        if already_applied and not command_queued and pending is None:
            return {"queued": False, "duplicate": True, "rows": geometry[0], "cols": geometry[1]}
        setattr(vd, _JUSI_PENDING_RESIZE_ATTR, geometry)
        if not command_queued:
            queue_command(_JUSI_RESIZE_COMMAND)
            setattr(vd, _JUSI_RESIZE_QUEUED_ATTR, True)

    woke = False
    try:
        curses.ungetch(curses.KEY_RESIZE)
        woke = True
    except (AttributeError, curses.error):
        pass
    return {"queued": True, "woke": woke, "rows": geometry[0], "cols": geometry[1]}


def apply_pending_visidata_terminal_resize(vd: Any) -> dict[str, Any]:
    with _VISIDATA_RESIZE_LOCK:
        geometry = getattr(vd, _JUSI_PENDING_RESIZE_ATTR, None)
        setattr(vd, _JUSI_PENDING_RESIZE_ATTR, None)
        setattr(vd, _JUSI_RESIZE_QUEUED_ATTR, False)
    if geometry is None:
        return {"applied": False}

    rows, cols = geometry
    import curses

    update_lines_cols = getattr(curses, "update_lines_cols", None)
    if callable(update_lines_cols):
        update_lines_cols()
    resize_terminal = getattr(curses, "resizeterm", None) or getattr(curses, "resize_term", None)
    if not callable(resize_terminal):
        return {"applied": False, "reason": "resize_unavailable", "rows": rows, "cols": cols}
    resize_terminal(rows, cols)

    redraw = getattr(vd, "redraw", None)
    if callable(redraw):
        redraw()
    else:
        scr = getattr(vd, "scrFull", None)
        if scr is not None:
            scr.clear()
            set_windows = getattr(vd, "setWindows", None)
            if callable(set_windows):
                set_windows(scr)
    setattr(vd, _JUSI_APPLIED_RESIZE_ATTR, geometry)
    return {"applied": True, "rows": rows, "cols": cols}


def dispatch_visidata_control_request(request: dict[str, Any]) -> dict[str, Any]:
    message_type = str(request.get("message_type", "")).strip()
    if message_type == "action_result":
        return handle_plugin_runtime_control_request(request)
    payload = request.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}
    if message_type == "terminal_resize":
        return queue_visidata_terminal_resize(payload)
    if message_type not in {"followup", "complete"}:
        return {}
    return dispatch_visidata_runtime_message(message_type, payload)


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
    base_sheet = getattr(visidata, "BaseSheet", Sheet)

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

    def jusi_followup(sheet, payload):  # type: ignore[no-untyped-def]
        return _call_runtime_control(_visidata_runtime(sheet), "followup", dict(payload))

    def jusi_complete(sheet, payload):  # type: ignore[no-untyped-def]
        return _call_runtime_control(_visidata_runtime(sheet), "complete", dict(payload))

    def jusi_apply_terminal_resize(vd_obj):  # type: ignore[no-untyped-def]
        return apply_pending_visidata_terminal_resize(vd_obj)

    Sheet.syscopyValue = syscopy_value
    Sheet.syscopyCells_async = syscopy_cells_async
    base_sheet.jusi_followup = jusi_followup
    base_sheet.jusi_complete = jusi_complete
    vd.jusiApplyTerminalResize = MethodType(jusi_apply_terminal_resize, vd)
    add_command = getattr(base_sheet, "addCommand", None)
    if callable(add_command):
        add_command(
            "",
            _JUSI_RESIZE_COMMAND,
            "vd.jusiApplyTerminalResize()",
            "apply a terminal resize queued by Jusi",
            replay=False,
        )
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
