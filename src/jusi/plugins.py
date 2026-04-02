from __future__ import annotations

import fcntl
import json
import os
import pty
import shlex
import signal
import subprocess
import struct
import sys
import tempfile
import termios
import threading
import shutil
from dataclasses import dataclass, field
from importlib import metadata
from typing import Any, Callable, Iterable, Protocol, Sequence

from jusi.domain.models import ExecutableCell


DISPLAY_HANDLER_ENTRY_POINT_GROUP = "jusi.display_handlers"


@dataclass(frozen=True)
class MagicCommand:
    name: str


class FrontendChannel(Protocol):
    def emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        ...

    def request_action(self, action_type: str, payload: dict[str, Any]) -> None:
        ...


@dataclass(frozen=True)
class HandlerContext:
    notebook_id: str
    session_id: str
    cell_id: int
    client_id: str
    channel: FrontendChannel
    push_frontend_message: Callable[[str, dict[str, Any]], None]
    invoke_backend_action: Callable[[str, dict[str, Any]], dict[str, Any]]
    append_execution_event: Callable[[dict[str, Any]], None]
    update_execution_status: Callable[[str], None]


class DisplayHandler(Protocol):
    def execute(self, context: HandlerContext, cell: ExecutableCell) -> str:
        ...

    def on_frontend_message(self, context: HandlerContext, message_type: str, payload: dict[str, Any]) -> None:
        ...

    def snapshot(self) -> dict[str, Any]:
        ...

    def stop(self) -> None:
        ...


@dataclass(frozen=True)
class DisplayHandlerSpec:
    handler_id: str
    factory: Callable[[], DisplayHandler]
    magic_commands: tuple[MagicCommand, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.handler_id.strip():
            raise ValueError("Display handler id is required")


class DisplayHandlerRegistry:
    def __init__(self, specs: Iterable[DisplayHandlerSpec] = ()) -> None:
        self._by_id: dict[str, DisplayHandlerSpec] = {}
        self._by_magic: dict[str, DisplayHandlerSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: DisplayHandlerSpec) -> None:
        if spec.handler_id in self._by_id:
            raise ValueError(f"Duplicate display handler id: {spec.handler_id}")
        self._by_id[spec.handler_id] = spec
        for magic in spec.magic_commands:
            if magic.name in self._by_magic:
                raise ValueError(f"Duplicate magic command: {magic.name}")
            self._by_magic[magic.name] = spec

    def get(self, handler_id: str) -> DisplayHandlerSpec | None:
        return self._by_id.get(handler_id)

    def find_for_cell(self, main_lines: Sequence[str]) -> DisplayHandlerSpec | None:
        if not main_lines:
            return None
        first_line = main_lines[0].strip()
        if not first_line.startswith("%%"):
            return None
        magic_name = first_line[2:].split(None, 1)[0]
        if not magic_name:
            return None
        return self._by_magic.get(magic_name)

    def all(self) -> tuple[DisplayHandlerSpec, ...]:
        return tuple(self._by_id.values())


def _entry_points_for_group(group: str) -> list[Any]:
    raw = metadata.entry_points()
    if hasattr(raw, "select"):
        return list(raw.select(group=group))
    legacy = raw.get(group, [])
    return list(legacy)


def _coerce_display_handler_spec(loaded: object) -> DisplayHandlerSpec:
    if isinstance(loaded, DisplayHandlerSpec):
        return loaded
    if callable(loaded):
        spec = loaded()
        if isinstance(spec, DisplayHandlerSpec):
            return spec
    raise TypeError("Display handler entry point must resolve to DisplayHandlerSpec or a zero-arg factory returning one")


def load_display_handler_specs(group: str = DISPLAY_HANDLER_ENTRY_POINT_GROUP) -> tuple[DisplayHandlerSpec, ...]:
    specs: list[DisplayHandlerSpec] = []
    for entry_point in _entry_points_for_group(group):
        specs.append(_coerce_display_handler_spec(entry_point.load()))
    return tuple(specs)


class RecordingFrontendChannel:
    def __init__(
        self,
        *,
        handler_id: str,
        notebook_id: str,
        session_id: str,
        client_id: str,
        append_execution_event: Callable[[dict[str, Any]], None],
        emit_handler_message: Callable[[str, str, str, str, str, dict[str, Any]], None],
    ) -> None:
        self._handler_id = handler_id
        self._notebook_id = notebook_id
        self._session_id = session_id
        self._client_id = client_id
        self._append_execution_event = append_execution_event
        self._emit_handler_message = emit_handler_message

    def emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self._append_execution_event(
            {
                "type": "handler_channel_event",
                "event_type": event_type,
                "payload": dict(payload),
            }
        )
        self._emit_handler_message(
            self._notebook_id,
            self._session_id,
            self._client_id,
            self._handler_id,
            event_type,
            dict(payload),
        )

    def request_action(self, action_type: str, payload: dict[str, Any]) -> None:
        event_payload = {
            "action_type": action_type,
            "payload": dict(payload),
        }
        self._append_execution_event(
            {
                "type": "frontend_action_request",
                "action_type": action_type,
                "payload": dict(payload),
            }
        )
        self._emit_handler_message(
            self._notebook_id,
            self._session_id,
            self._client_id,
            self._handler_id,
            "action_request",
            event_payload,
        )

class TerminalDisplayHandler:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._master_fd: int | None = None
        self._reader: threading.Thread | None = None
        self._context: HandlerContext | None = None
        self._pending_geometry: tuple[int, int] | None = None

    def on_frontend_message(self, context: HandlerContext, message_type: str, payload: dict[str, Any]) -> None:
        context.channel.emit_event(
            "frontend_message",
            {
                "handler_id": self.handler_id(),
                "message_type": message_type,
                "payload": dict(payload),
            },
        )
        if message_type == "bootstrap_done":
            self._remember_geometry(payload)
            self._bootstrap_live_process(context)
        if message_type == "send_input":
            text = str(payload.get("text", ""))
            self._send_bytes(context, (text + "\n").encode("utf-8"), echo_payload={"text": text}, message_type="terminal_input")
        if message_type == "terminal_input":
            text = str(payload.get("text", ""))
            self._send_bytes(context, text.encode("utf-8"), echo_payload={"text": text}, message_type="terminal_input")
        if message_type == "terminal_bytes":
            raw_hex = str(payload.get("hex", "")).strip()
            if raw_hex:
                self._send_bytes(context, bytes.fromhex(raw_hex), echo_payload={"hex": raw_hex}, message_type="terminal_bytes")
        if message_type == "terminal_key":
            sequence = _terminal_key_bytes(payload)
            if sequence:
                self._send_bytes(context, sequence, echo_payload=dict(payload), message_type="terminal_key")
        if message_type == "terminal_resize":
            self._resize_terminal(payload)
        if message_type == "terminal_signal":
            self._signal_terminal(payload)

    def handler_id(self) -> str:
        raise NotImplementedError

    def terminal_command(self) -> tuple[list[str], str]:
        raise NotImplementedError

    def terminal_env(self) -> dict[str, str]:
        return _build_terminal_env()

    def stop(self) -> None:
        with self._lock:
            process = self._process
            master_fd = self._master_fd
            self._process = None
            self._master_fd = None
            self._pending_geometry = None
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=1)
        if master_fd is not None:
            try:
                os.close(master_fd)
            except OSError:
                pass
        self._reader = None
        self._read_buffer = ""

    def _bootstrap_live_process(self, context: HandlerContext) -> None:
        with self._lock:
            geometry = self._pending_geometry
        self.stop()
        if geometry is not None:
            with self._lock:
                self._pending_geometry = geometry
        master_fd, slave_fd = pty.openpty()
        if geometry is not None:
            self._apply_winsize(slave_fd, geometry[0], geometry[1])
        command, fallback_notice = self.terminal_command()
        env = self.terminal_env()
        env.pop("LINES", None)
        env.pop("COLUMNS", None)
        if geometry is not None:
            env["LINES"] = str(geometry[0])
            env["COLUMNS"] = str(geometry[1])
        process = subprocess.Popen(
            command,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            start_new_session=True,
            env=env,
        )
        os.close(slave_fd)
        with self._lock:
            self._process = process
            self._master_fd = master_fd
            self._context = context
        context.channel.emit_event("handler_snapshot", self.snapshot())
        context.push_frontend_message(
            "handler_snapshot",
            self.snapshot(),
        )
        if fallback_notice:
            context.append_execution_event({"type": "handler_stream", "text": fallback_notice})
        self._reader = threading.Thread(target=self._read_output_loop, daemon=True)
        self._reader.start()

    def _send_bytes(
        self,
        context: HandlerContext,
        payload: bytes,
        *,
        echo_payload: dict[str, Any],
        message_type: str,
    ) -> None:
        context.push_frontend_message(message_type, echo_payload)
        with self._lock:
            master_fd = self._master_fd
        if master_fd is None:
            context.push_frontend_message("terminal_output", {"text": "vd transport is not active"})
            return
        os.write(master_fd, payload)

    def _resize_terminal(self, payload: dict[str, Any]) -> None:
        geometry = self._parse_geometry(payload)
        if geometry is None:
            return
        rows, cols = geometry
        with self._lock:
            master_fd = self._master_fd
            process = self._process
            self._pending_geometry = geometry
        if master_fd is None:
            return
        self._apply_winsize(master_fd, rows, cols)
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGWINCH)
            except ProcessLookupError:
                pass

    def _signal_terminal(self, payload: dict[str, Any]) -> None:
        name = str(payload.get("name", "")).strip().lower()
        with self._lock:
            process = self._process
            master_fd = self._master_fd
        if name == "interrupt":
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGINT)
                except ProcessLookupError:
                    pass
            return
        if name == "eof" and master_fd is not None:
            os.write(master_fd, b"\x04")

    def _remember_geometry(self, payload: dict[str, Any]) -> None:
        geometry = self._parse_geometry(payload)
        if geometry is None:
            return
        with self._lock:
            self._pending_geometry = geometry

    def _parse_geometry(self, payload: dict[str, Any]) -> tuple[int, int] | None:
        rows = int(payload.get("rows", 0) or 0)
        cols = int(payload.get("cols", 0) or 0)
        if rows <= 0 or cols <= 0:
            return None
        return rows, cols

    def _apply_winsize(self, fd: int, rows: int, cols: int) -> None:
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)


class VDDisplayHandlerBase(TerminalDisplayHandler):
    def __init__(self) -> None:
        super().__init__()
        self._mode = "browse"
        self._entry = ""

    def execute(self, context: HandlerContext, cell: ExecutableCell) -> str:
        self.stop()
        self._mode = "browse"
        self._entry = cell.main_lines[0] if cell.main_lines else f"%%{self.handler_id()}"
        context.append_execution_event(
            {
                "type": "execution_started",
                "cell_id": context.cell_id,
                "kind": cell.kind,
                "syntax": cell.syntax,
                "handler_id": self.handler_id(),
            }
        )
        context.channel.emit_event(
            "handler_snapshot",
            {
                "handler_id": self.handler_id(),
                "mode": self._mode,
                "entry": self._entry,
                "family": "visidata",
            },
        )
        context.channel.request_action(
            self.bootstrap_action(),
            self.bootstrap_payload(context, cell),
        )
        context.update_execution_status("follow-up")
        context.append_execution_event(
            {
                "type": "execution_finished",
                "status": "follow-up",
                "handler_id": self.handler_id(),
            }
        )
        return "follow-up"

    def bootstrap_action(self) -> str:
        return f"{self.handler_id()}.bootstrap"

    def bootstrap_payload(self, context: HandlerContext, cell: ExecutableCell) -> dict[str, Any]:
        _ = (context, cell)
        return {
            "handler_id": self.handler_id(),
            "mode": self._mode,
        }

    def on_frontend_message(self, context: HandlerContext, message_type: str, payload: dict[str, Any]) -> None:
        if message_type == "vd_copy":
            self.handle_copy(context, payload)
            return
        if message_type == "vd_complete":
            completions = list(self.complete(context, payload))
            context.push_frontend_message(
                "vd_complete_result",
                {
                    "handler_id": self.handler_id(),
                    "items": completions,
                },
            )
            return
        if message_type == "vd_followup":
            followup_payload = self.followup(context, payload)
            context.push_frontend_message(
                "vd_followup_result",
                {
                    "handler_id": self.handler_id(),
                    "payload": dict(followup_payload),
                },
            )
            return
        super().on_frontend_message(context, message_type, payload)

    def handle_copy(self, context: HandlerContext, payload: dict[str, Any]) -> None:
        content = str(payload.get("text", ""))
        context.push_frontend_message(
            "vd_copy_result",
            {
                "handler_id": self.handler_id(),
                "text": content,
            },
        )

    def complete(self, context: HandlerContext, payload: dict[str, Any]) -> Sequence[str]:
        _ = (context, payload)
        return ()

    def followup(self, context: HandlerContext, payload: dict[str, Any]) -> dict[str, Any]:
        _ = (context, payload)
        return {}

    def terminal_command(self) -> tuple[list[str], str]:
        command, fallback_notice = _build_vd_command()
        self._mode = "live"
        return command, fallback_notice

    def terminal_env(self) -> dict[str, str]:
        return _build_vd_env()

    def snapshot(self) -> dict[str, Any]:
        snapshot = {"handler_id": self.handler_id(), "mode": self._mode, "entry": self._entry, "family": "visidata"}
        if self._mode == "live":
            snapshot.update({"ready": True, "transport": "pty"})
        return snapshot

    def _read_output_loop(self) -> None:
        while True:
            with self._lock:
                process = self._process
                master_fd = self._master_fd
                context = self._context
            if process is None or master_fd is None or context is None:
                return
            try:
                chunk = os.read(master_fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            self._push_output_bytes(context, chunk)
            if process.poll() is not None:
                break

    def _push_output_bytes(self, context: HandlerContext, chunk: bytes) -> None:
        if not chunk:
            return
        context.push_frontend_message(
            "terminal_bytes",
            {
                "hex": chunk.hex(),
            },
        )


class VDDisplayHandler(VDDisplayHandlerBase):
    def __init__(self) -> None:
        super().__init__()
        self._source_info: dict[str, str] | None = None

    def execute(self, context: HandlerContext, cell: ExecutableCell) -> str:
        self._source_info = None
        return super().execute(context, cell)

    def handler_id(self) -> str:
        return "vd"

    def bootstrap_payload(self, context: HandlerContext, cell: ExecutableCell) -> dict[str, Any]:
        payload = super().bootstrap_payload(context, cell)
        expression = _vd_expression_from_cell(cell)
        if not expression:
            return payload
        self._source_info = context.invoke_backend_action(
            "materialize_vd_source",
            {"expression": expression},
        )
        payload.update({"expression": expression, "source": dict(self._source_info)})
        return payload

    def terminal_command(self) -> tuple[list[str], str]:
        source_path = self._source_info.get("path", "") if self._source_info else ""
        command, fallback_notice = _build_vd_command(source_path=source_path)
        self._mode = "live"
        return command, fallback_notice

    def snapshot(self) -> dict[str, Any]:
        snapshot = super().snapshot()
        if self._source_info is not None:
            snapshot["source"] = dict(self._source_info)
        return snapshot

    def stop(self) -> None:
        source_path = self._source_info.get("path", "") if self._source_info else ""
        super().stop()
        self._source_info = None
        if source_path:
            try:
                os.unlink(source_path)
            except FileNotFoundError:
                pass


def default_frontend_channel(
    *,
    handler_id: str,
    notebook_id: str,
    session_id: str,
    client_id: str,
    append_execution_event: Callable[[dict[str, Any]], None],
    emit_handler_message: Callable[[str, str, str, str, str, dict[str, Any]], None],
) -> FrontendChannel:
    return RecordingFrontendChannel(
        handler_id=handler_id,
        notebook_id=notebook_id,
        session_id=session_id,
        client_id=client_id,
        append_execution_event=append_execution_event,
        emit_handler_message=emit_handler_message,
    )


@dataclass
class ActiveDisplayHandler:
    handler_id: str
    handler: DisplayHandler
    context: HandlerContext


class DisplayHandlerRuntime:
    def __init__(self) -> None:
        self._active: dict[tuple[str, str], ActiveDisplayHandler] = {}

    def register(self, session_id: str, client_id: str, active: ActiveDisplayHandler) -> None:
        self._active[(session_id, client_id)] = active

    def get(self, session_id: str, client_id: str) -> ActiveDisplayHandler | None:
        return self._active.get((session_id, client_id))

    def remove_client(self, session_id: str, client_id: str) -> None:
        active = self._active.pop((session_id, client_id), None)
        if active is not None:
            active.handler.stop()

    def remove_session(self, session_id: str) -> None:
        stale = [key for key in self._active if key[0] == session_id]
        for key in stale:
            active = self._active.pop(key, None)
            if active is not None:
                active.handler.stop()

    def stop_all(self) -> None:
        stale = list(self._active.values())
        self._active.clear()
        for active in stale:
            active.handler.stop()


def builtin_display_handler_specs() -> tuple[DisplayHandlerSpec, ...]:
    return (
        DisplayHandlerSpec(
            handler_id="vd",
            factory=VDDisplayHandler,
            magic_commands=(MagicCommand("vd"),),
        ),
    )


def build_display_handler_registry() -> DisplayHandlerRegistry:
    registry = DisplayHandlerRegistry(builtin_display_handler_specs())
    for spec in load_display_handler_specs():
        registry.register(spec)
    return registry


def _build_vd_command(*, source_path: str = "") -> tuple[list[str], str]:
    raw = os.environ.get("JUSI_VD_CMD", "").strip()
    if raw:
        command = shlex.split(raw)
        if not command:
            raise RuntimeError("JUSI_VD_CMD did not produce an executable command")
        return command, ""
    vd_path = (
        shutil.which("vd")
        or shutil.which("visidata")
        or _executable_sibling("vd")
        or _executable_sibling("visidata")
    )
    if vd_path:
        command = [vd_path]
        if source_path:
            command.append(source_path)
        return command, ""
    notice = "vd binary unavailable; using shell fallback"
    if source_path:
        notice = f"{notice} (exported source: {source_path})"
    return (
        ["/bin/sh", "-lc", "export PS1='vd> '; exec /bin/sh -i"],
        notice,
    )


def _build_vd_env() -> dict[str, str]:
    env = os.environ.copy()
    env["TERM"] = os.environ.get("JUSI_VD_TERM", "").strip() or "xterm-256color"
    return env


def _executable_sibling(name: str) -> str:
    executable_dir = os.path.dirname(sys.executable)
    if not executable_dir:
        return ""
    candidate = os.path.join(executable_dir, name)
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    return ""


def _vd_expression_from_cell(cell: ExecutableCell) -> str:
    if not cell.main_lines:
        return ""
    first_line = cell.main_lines[0].strip()
    inline = ""
    if first_line.startswith("%%"):
        parts = first_line[2:].split(None, 1)
        if len(parts) > 1:
            inline = parts[1].strip()
    body_lines: list[str] = []
    if inline:
        body_lines.append(inline)
    body_lines.extend(cell.main_lines[1:])
    return "\n".join(body_lines).strip()


def _terminal_key_bytes(payload: dict[str, Any]) -> bytes:
    key = str(payload.get("key", "")).strip()
    if not key:
        return b""
    ctrl = bool(payload.get("ctrl", False))
    alt = bool(payload.get("alt", False))
    shift = bool(payload.get("shift", False))

    named = {
        "enter": b"\r",
        "return": b"\r",
        "tab": b"\t",
        "backspace": b"\x7f",
        "escape": b"\x1b",
        "esc": b"\x1b",
        "space": b" ",
        "up": b"\x1b[A",
        "down": b"\x1b[B",
        "right": b"\x1b[C",
        "left": b"\x1b[D",
        "home": b"\x1b[H",
        "end": b"\x1b[F",
        "insert": b"\x1b[2~",
        "delete": b"\x1b[3~",
        "pageup": b"\x1b[5~",
        "pagedown": b"\x1b[6~",
        "f1": b"\x1bOP",
        "f2": b"\x1bOQ",
        "f3": b"\x1bOR",
        "f4": b"\x1bOS",
        "f5": b"\x1b[15~",
        "f6": b"\x1b[17~",
        "f7": b"\x1b[18~",
        "f8": b"\x1b[19~",
        "f9": b"\x1b[20~",
        "f10": b"\x1b[21~",
        "f11": b"\x1b[23~",
        "f12": b"\x1b[24~",
    }
    if key.lower() in named:
        sequence = named[key.lower()]
        return (b"\x1b" + sequence) if alt else sequence

    char = key
    if len(char) != 1:
        return b""
    if shift:
        char = char.upper()
    if ctrl:
        codepoint = ord(char.upper())
        if 64 <= codepoint <= 95:
            sequence = bytes([codepoint - 64])
        elif codepoint == 63:
            sequence = b"\x7f"
        else:
            return b""
    else:
        sequence = char.encode("utf-8")
    return (b"\x1b" + sequence) if alt else sequence
