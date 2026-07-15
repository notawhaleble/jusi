from __future__ import annotations

import json
import os
import sys

from jusi.infrastructure.debug_timing import emit_timing

_SAFE_ATTACH_ENV_KEYS = {
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "SHELL",
    "TERM",
    "TMPDIR",
    "USER",
    "VIRTUAL_ENV",
}


def native_terminal_attach_command() -> list[str]:
    return [sys.executable, "-m", "jusi", "client-process", "terminal-attach"]


def build_exec_terminal_attach_env(
    *,
    command: list[str],
    env: dict[str, str],
    session_id: str,
    client_id: str,
    handler_id: str,
) -> dict[str, str]:
    attach_child_env = {
        key: value
        for key, value in dict(env).items()
        if key in _SAFE_ATTACH_ENV_KEYS or key.startswith("JUSI_")
    }
    attach_child_env.pop("LINES", None)
    attach_child_env.pop("COLUMNS", None)
    attach_env = {
        "JUSI_TERMINAL_MODE": "exec",
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
    emit_timing(
        "native_terminal.attach_env.build",
        session_id=session_id,
        client_id=client_id,
        handler_id=handler_id,
        command=list(command),
        child_env_keys=sorted(attach_child_env.keys()),
        attach_env_keys=sorted(attach_env.keys()),
        has_plugin_callable=bool(str(attach_child_env.get("JUSI_PLUGIN_RUNTIME_CALLABLE", "")).strip()),
        plugin_runtime_callable=str(attach_child_env.get("JUSI_PLUGIN_RUNTIME_CALLABLE", "")).strip(),
    )
    return attach_env


def build_transcript_terminal_attach_env(
    *,
    session_id: str,
    client_id: str,
) -> dict[str, str]:
    attach_env = {
        "JUSI_TERMINAL_MODE": "transcript",
        "JUSI_SESSION_ID": session_id,
        "JUSI_CLIENT_ID": client_id,
    }
    pythonpath = str(os.environ.get("PYTHONPATH", "")).strip()
    if pythonpath:
        attach_env["PYTHONPATH"] = pythonpath
    supervisor_pid = str(os.getpid()).strip()
    if supervisor_pid:
        attach_env["JUSI_SUPERVISOR_PID"] = supervisor_pid
    return attach_env
