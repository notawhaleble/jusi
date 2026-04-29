from __future__ import annotations

import json
import os
import sys
from typing import Any

from jusi.infrastructure.client_runtime_host import build_transcript_runtime_host_factory


def run_client_process() -> int:
    return TranscriptRuntimeRunner().run()


TranscriptRuntimeRunner = build_transcript_runtime_host_factory()


class TranscriptRuntimeLegacyAdapter:
    def __init__(self) -> None:
        self._host = TranscriptRuntimeRunner()
        self._session = self._host._session
        self._pull()

    def _pull(self) -> None:
        self._state = self._session.state
        self._control_dir = self._session._control_dir
        self._commands_path = self._session._commands_path
        self._status_path = self._session._status_path
        self._command_offset = self._session._command_offset
        self._supervisor_pid = self._host._supervisor_pid

    def _sync(self) -> None:
        self._session._state = self._state
        self._session._control_dir = self._control_dir
        self._session._commands_path = self._commands_path
        self._session._status_path = self._status_path
        self._session._command_offset = self._command_offset
        self._host._supervisor_pid = self._supervisor_pid

    def _stop(self, signum: int, frame: object) -> None:
        return self._host._stop(signum, frame)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._host, name)

    def run(self) -> int:
        self._sync()
        rc = self._host.run()
        self._pull()
        return rc


ClientProcessRunner = TranscriptRuntimeRunner
ClientProcessRunner = TranscriptRuntimeLegacyAdapter


def run_terminal_attach() -> int:
    raw_command = os.environ.get("JUSI_TERMINAL_CMD_JSON", "").strip()
    if not raw_command:
        sys.stderr.write("missing JUSI_TERMINAL_CMD_JSON\n")
        sys.stderr.flush()
        return 2
    try:
        command = json.loads(raw_command)
    except json.JSONDecodeError:
        sys.stderr.write("invalid JUSI_TERMINAL_CMD_JSON\n")
        sys.stderr.flush()
        return 2
    if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
        sys.stderr.write("terminal attach command must be a non-empty string list\n")
        sys.stderr.flush()
        return 2
    child_env = os.environ.copy()
    child_env.pop("LINES", None)
    child_env.pop("COLUMNS", None)
    raw_env = os.environ.get("JUSI_TERMINAL_ENV_JSON", "").strip()
    if raw_env:
        try:
            env_updates = json.loads(raw_env)
        except json.JSONDecodeError:
            env_updates = {}
        if isinstance(env_updates, dict):
            for key, value in env_updates.items():
                if isinstance(key, str) and isinstance(value, str):
                    child_env[key] = value
    os.execvpe(command[0], command, child_env)
