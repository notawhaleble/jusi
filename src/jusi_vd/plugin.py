from __future__ import annotations

import json
import os
import sys
from importlib import util
from typing import Any

from jusi.domain.models import ExecutableCell
from jusi.plugins import BaseVdHandler, DisplayHandlerSpec, HandlerContext, MagicCommand


class VDDisplayHandler(BaseVdHandler):
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

    @staticmethod
    def bootstrap_cell_body(first_line: str) -> str | None:
        _ = first_line
        return "None"

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


def _build_vd_command() -> tuple[list[str], str]:
    if util.find_spec("visidata") is None:
        raise RuntimeError(
            "VisiData is not available for %%vd. Install the 'visidata' package in the active environment."
        )
    return [sys.executable, "-m", "jusi", "plugin-runtime"], ""


def _build_vd_env() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"HOME", "LANG", "LC_ALL", "LC_CTYPE", "PATH", "SHELL", "TERM", "TMPDIR", "USER", "VIRTUAL_ENV"}
        or key.startswith("JUSI_")
    }
    env["TERM"] = os.environ.get("JUSI_VD_TERM", "").strip() or "xterm-256color"
    env["JUSI_PLUGIN_RUNTIME_CALLABLE"] = "jusi_vd.runner:run_vd_runner"
    return env

def display_handler_specs() -> tuple[DisplayHandlerSpec, ...]:
    return (
        DisplayHandlerSpec(
            handler_id="vd",
            factory=VDDisplayHandler,
            magic_commands=(MagicCommand("vd", bootstrap_body=VDDisplayHandler.bootstrap_cell_body),),
            kernel_extension_modules=("jusi_vd.kernel",),
            presentation={"syntax": "python", "indent": "python", "followup": True, "completion": False},
        ),
    )
