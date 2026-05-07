from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

from jusi.infrastructure.client_runtime_constants import JUSI_CLIENT_RUNTIME_MODE_ENV
from jusi.infrastructure.runtime_supervisor import parse_supervisor_pid


JUSI_CLIENT_RUNTIME_STARTUP_JSON_ENV = "JUSI_CLIENT_RUNTIME_STARTUP_JSON"


@dataclass(frozen=True)
class TranscriptClientRuntimeStartup:
    mode: str
    client_id: str
    notebook_id: str
    session_id: str
    control_dir: str
    supervisor_pid: int


def build_transcript_runtime_startup(
    *,
    client_id: str,
    notebook_id: str,
    session_id: str,
    control_dir: str,
    supervisor_pid: int,
) -> TranscriptClientRuntimeStartup:
    return TranscriptClientRuntimeStartup(
        mode="transcript",
        client_id=client_id,
        notebook_id=notebook_id,
        session_id=session_id,
        control_dir=control_dir,
        supervisor_pid=supervisor_pid,
    )


def transcript_startup_env(startup: TranscriptClientRuntimeStartup) -> dict[str, str]:
    payload = asdict(startup)
    return {
        "JUSI_CLIENT_ID": startup.client_id,
        "JUSI_NOTEBOOK_ID": startup.notebook_id,
        "JUSI_SESSION_ID": startup.session_id,
        "JUSI_CLIENT_CONTROL_DIR": startup.control_dir,
        "JUSI_SUPERVISOR_PID": str(startup.supervisor_pid),
        JUSI_CLIENT_RUNTIME_MODE_ENV: "transcript",
        JUSI_CLIENT_RUNTIME_STARTUP_JSON_ENV: json.dumps(payload),
    }


def load_transcript_runtime_startup_from_env() -> TranscriptClientRuntimeStartup:
    raw = str(os.environ.get(JUSI_CLIENT_RUNTIME_STARTUP_JSON_ENV, "")).strip()
    if raw:
        payload = json.loads(raw)
        if isinstance(payload, dict) and str(payload.get("mode", "")).strip() == "transcript":
            return TranscriptClientRuntimeStartup(
                mode="transcript",
                client_id=str(payload.get("client_id", "")),
                notebook_id=str(payload.get("notebook_id", "")),
                session_id=str(payload.get("session_id", "")),
                control_dir=str(payload.get("control_dir", "")),
                supervisor_pid=int(payload.get("supervisor_pid", 0)),
            )
    return TranscriptClientRuntimeStartup(
        mode="transcript",
        client_id=str(os.environ.get("JUSI_CLIENT_ID", "")),
        notebook_id=str(os.environ.get("JUSI_NOTEBOOK_ID", "")),
        session_id=str(os.environ.get("JUSI_SESSION_ID", "")),
        control_dir=str(os.environ.get("JUSI_CLIENT_CONTROL_DIR", "")).strip(),
        supervisor_pid=parse_supervisor_pid(os.environ.get("JUSI_SUPERVISOR_PID", "")),
    )
