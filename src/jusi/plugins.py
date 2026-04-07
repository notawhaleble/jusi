from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from importlib import util
from importlib import metadata
from typing import Any, Callable, Iterable, Protocol, Sequence

from jusi.domain.models import ClientTransport, ExecutableCell, HandlerHandoff


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
    set_client_transport: Callable[[ClientTransport], None]
    magic_name: str = ""
    content: str = ""
    meta: dict[str, object] = field(default_factory=dict)


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
    handoff_validator: Callable[[HandlerHandoff], bool] | None = None

    def __post_init__(self) -> None:
        if not self.handler_id.strip():
            raise ValueError("Display handler id is required")


class DisplayHandlerRegistry:
    def __init__(self, specs: Iterable[DisplayHandlerSpec] = ()) -> None:
        self._by_id: dict[str, DisplayHandlerSpec] = {}
        self._by_magic: dict[str, list[DisplayHandlerSpec]] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: DisplayHandlerSpec) -> None:
        if spec.handler_id in self._by_id:
            raise ValueError(f"Duplicate display handler id: {spec.handler_id}")
        self._by_id[spec.handler_id] = spec
        for magic in spec.magic_commands:
            self._by_magic.setdefault(magic.name, []).append(spec)

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
        specs = self._by_magic.get(magic_name, [])
        if len(specs) != 1:
            return None
        return specs[0]

    def validate_handoff(self, handoff: HandlerHandoff) -> DisplayHandlerSpec | None:
        spec = self._by_id.get(handoff.handler_id)
        if spec is None:
            return None
        allowed_magics = {magic.name for magic in spec.magic_commands}
        if handoff.magic_name not in allowed_magics:
            return None
        validator = spec.handoff_validator
        if validator is not None and not validator(handoff):
            return None
        return spec

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
        self._mode = "ready"

    def on_frontend_message(self, context: HandlerContext, message_type: str, payload: dict[str, Any]) -> None:
        context.channel.emit_event(
            "frontend_message",
            {
                "handler_id": self.handler_id(),
                "message_type": message_type,
                "payload": dict(payload),
            },
        )

    def handler_id(self) -> str:
        raise NotImplementedError

    def terminal_command(self) -> tuple[list[str], str]:
        raise NotImplementedError

    def terminal_env(self) -> dict[str, str]:
        return _build_terminal_env()

    def stop(self) -> None:
        self._mode = "ready"

    def prepare_transport(self, context: HandlerContext) -> None:
        _command, fallback_notice, _env = self._prepare_native_terminal_transport(context)
        if fallback_notice:
            context.append_execution_event(
                {
                    "type": "handler_notice",
                    "text": fallback_notice,
                    "handler_id": self.handler_id(),
                }
            )

    def _prepare_native_terminal_transport(self, context: HandlerContext) -> tuple[list[str], str, dict[str, str]]:
        command, fallback_notice = self.terminal_command()
        env = self.terminal_env()
        transport = ClientTransport(
            kind="native_terminal",
            attach_cmd=_native_terminal_attach_command(),
            attach_env=_native_terminal_attach_env(
                command=command,
                env=env,
                session_id=context.session_id,
                client_id=context.client_id,
                handler_id=self.handler_id(),
            ),
            session_id=context.session_id,
            client_id=context.client_id,
            handler_id=self.handler_id(),
        )
        context.set_client_transport(transport)
        return command, fallback_notice, env


class VDDisplayHandlerBase(TerminalDisplayHandler):
    def __init__(self) -> None:
        super().__init__()
        self._entry = ""

    def execute(self, context: HandlerContext, cell: ExecutableCell) -> str:
        self.stop()
        self._mode = "ready"
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
        self.prepare_transport(context)
        context.update_execution_status("follow-up")
        context.append_execution_event(
            {
                "type": "execution_finished",
                "status": "follow-up",
                "handler_id": self.handler_id(),
            }
        )
        return "follow-up"

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
            snapshot.update({"ready": True, "transport": "native_terminal"})
        return snapshot


class VDDisplayHandler(VDDisplayHandlerBase):
    def __init__(self) -> None:
        super().__init__()
        self._payload: dict[str, object] | None = None

    def execute(self, context: HandlerContext, cell: ExecutableCell) -> str:
        self.stop()
        self._mode = "ready"
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
        self._payload = {
            "content": context.content,
            "meta": dict(context.meta),
        }
        self.prepare_transport(context)
        context.update_execution_status("follow-up")
        context.append_execution_event(
            {
                "type": "execution_finished",
                "status": "follow-up",
                "handler_id": self.handler_id(),
            }
        )
        return "follow-up"

    def handler_id(self) -> str:
        return "vd"

    def terminal_command(self) -> tuple[list[str], str]:
        command, fallback_notice = _build_vd_command()
        self._mode = "live"
        return command, fallback_notice

    def snapshot(self) -> dict[str, Any]:
        snapshot = super().snapshot()
        if self._payload is not None:
            snapshot["payload"] = dict(self._payload)
        return snapshot

    def terminal_env(self) -> dict[str, str]:
        env = _build_vd_env()
        payload = self._payload or {"content": "", "meta": {}}
        env["JUSI_VD_PAYLOAD_JSON"] = json.dumps(payload)
        return env

    def stop(self) -> None:
        super().stop()
        self._payload = None


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
    context: HandlerContext | None


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

    def interrupt_client(self, session_id: str, client_id: str) -> None:
        active = self._active.get((session_id, client_id))
        if active is None:
            return
        interrupt = getattr(active.handler, "interrupt", None)
        if callable(interrupt):
            interrupt()

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
    if util.find_spec("visidata") is None:
        raise RuntimeError(
            "VisiData is not available for %%vd. Install the 'visidata' package in the active environment."
        )
    return [sys.executable, "-m", "jusi", "vd-runner"], ""


def _build_vd_env() -> dict[str, str]:
    env = os.environ.copy()
    env["TERM"] = os.environ.get("JUSI_VD_TERM", "").strip() or "xterm-256color"
    return env


def _native_terminal_attach_command() -> list[str]:
    return [sys.executable, "-m", "jusi", "client-process", "terminal-attach"]


def _native_terminal_attach_env(
    *,
    command: list[str],
    env: dict[str, str],
    session_id: str,
    client_id: str,
    handler_id: str,
) -> dict[str, str]:
    attach_child_env = dict(env)
    attach_child_env.pop("LINES", None)
    attach_child_env.pop("COLUMNS", None)
    attach_env = {
        "JUSI_TERMINAL_CMD_JSON": json.dumps(command),
        "JUSI_TERMINAL_ENV_JSON": json.dumps(attach_child_env),
        "JUSI_SESSION_ID": session_id,
        "JUSI_CLIENT_ID": client_id,
        "JUSI_HANDLER_ID": handler_id,
    }
    pythonpath = str(os.environ.get("PYTHONPATH", "")).strip()
    if pythonpath:
        attach_env["PYTHONPATH"] = pythonpath
    supervisor_pid = str(os.getpid()).strip()
    if supervisor_pid:
        attach_env["JUSI_SUPERVISOR_PID"] = supervisor_pid
    return attach_env
