from __future__ import annotations

import os
import pty
import shlex
import signal
import subprocess
import sys
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


class VDDisplayHandler:
    def __init__(self) -> None:
        self._mode = "browse"
        self._entry = "%%vd"
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._master_fd: int | None = None
        self._reader: threading.Thread | None = None
        self._context: HandlerContext | None = None
        self._read_buffer = ""

    def execute(self, context: HandlerContext, cell: ExecutableCell) -> str:
        self.stop()
        self._mode = "browse"
        self._entry = cell.main_lines[0] if cell.main_lines else "%%vd"
        self._context = context
        self._read_buffer = ""
        context.append_execution_event(
            {
                "type": "execution_started",
                "cell_id": context.cell_id,
                "kind": cell.kind,
                "syntax": cell.syntax,
                "handler_id": "vd",
            }
        )
        context.channel.emit_event(
            "handler_snapshot",
            {
                "handler_id": "vd",
                "mode": self._mode,
                "entry": self._entry,
            },
        )
        context.channel.request_action(
            "vd.bootstrap",
            {
                "handler_id": "vd",
                "mode": self._mode,
            },
        )
        context.update_execution_status("follow-up")
        context.append_execution_event(
            {
                "type": "execution_finished",
                "status": "follow-up",
                "handler_id": "vd",
            }
        )
        return "follow-up"

    def on_frontend_message(self, context: HandlerContext, message_type: str, payload: dict[str, Any]) -> None:
        context.channel.emit_event(
            "frontend_message",
            {
                "handler_id": "vd",
                "message_type": message_type,
                "payload": dict(payload),
            },
        )
        if message_type == "bootstrap_done":
            self._bootstrap_live_process(context)
        if message_type == "send_input":
            text = str(payload.get("text", ""))
            self._send_input(context, text)

    def snapshot(self) -> dict[str, Any]:
        return {"handler_id": "vd", "mode": self._mode, "entry": self._entry}

    def stop(self) -> None:
        with self._lock:
            process = self._process
            master_fd = self._master_fd
            self._process = None
            self._master_fd = None
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
        self.stop()
        master_fd, slave_fd = pty.openpty()
        command, fallback_notice = _build_vd_command()
        process = subprocess.Popen(
            command,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            start_new_session=True,
        )
        os.close(slave_fd)
        with self._lock:
            self._process = process
            self._master_fd = master_fd
            self._context = context
            self._mode = "live"
            self._read_buffer = ""
        context.channel.emit_event(
            "handler_snapshot",
            {
                "handler_id": "vd",
                "mode": self._mode,
                "ready": True,
                "transport": "pty",
                "command": command[0],
            },
        )
        context.push_frontend_message(
            "handler_snapshot",
            {
                "handler_id": "vd",
                "mode": self._mode,
                "ready": True,
                "transport": "pty",
                "command": command[0],
            },
        )
        if fallback_notice:
            context.push_frontend_message("terminal_output", {"text": fallback_notice})
        self._reader = threading.Thread(target=self._read_output_loop, daemon=True)
        self._reader.start()

    def _send_input(self, context: HandlerContext, text: str) -> None:
        context.push_frontend_message("terminal_input", {"text": text})
        with self._lock:
            master_fd = self._master_fd
        if master_fd is None:
            context.push_frontend_message("terminal_output", {"text": "vd transport is not active"})
            return
        os.write(master_fd, (text + "\n").encode("utf-8"))

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
            self._append_output(context, chunk.decode("utf-8", errors="replace"))
            if process.poll() is not None:
                break
        self._flush_output()

    def _append_output(self, context: HandlerContext, text: str) -> None:
        with self._lock:
            self._read_buffer += text
            lines = self._read_buffer.splitlines(keepends=True)
            self._read_buffer = ""
            if lines and not lines[-1].endswith(("\n", "\r")):
                self._read_buffer = lines.pop()
            if self._read_buffer == "vd> ":
                lines.append(self._read_buffer)
                self._read_buffer = ""
        for line in lines:
            normalized = line.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
            if normalized == "vd> ":
                context.push_frontend_message("terminal_prompt", {"text": "vd> "})
            elif normalized:
                context.push_frontend_message("terminal_output", {"text": normalized})

    def _flush_output(self) -> None:
        with self._lock:
            remaining = self._read_buffer
            context = self._context
            self._read_buffer = ""
        if context is None or not remaining:
            return
        normalized = remaining.replace("\r\n", "\n").replace("\r", "\n")
        if normalized == "vd> ":
            context.push_frontend_message("terminal_prompt", {"text": "vd> "})
            return
        stripped = normalized.rstrip("\n")
        if stripped:
            context.push_frontend_message("terminal_output", {"text": stripped})


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


def _build_vd_command() -> tuple[list[str], str]:
    raw = os.environ.get("JUSI_VD_CMD", "").strip()
    if raw:
        command = shlex.split(raw)
        if not command:
            raise RuntimeError("JUSI_VD_CMD did not produce an executable command")
        return command, ""
    vd_path = shutil.which("vd") or shutil.which("visidata")
    if vd_path:
        return [vd_path], ""
    return (
        ["/bin/sh", "-lc", "export PS1='vd> '; exec /bin/sh -i"],
        "vd binary unavailable; using shell fallback",
    )
