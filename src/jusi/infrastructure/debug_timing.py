from __future__ import annotations

import json
import os
import threading
import time
from typing import Any


_DEFAULT_TIMING_LOG_PATH = "/tmp/jusi-backend-timing.log"
_LOCK = threading.Lock()


def _timing_enabled() -> bool:
    raw = str(os.environ.get("JUSI_DEBUG_TIMING", "")).strip().lower()
    return raw not in {"", "0", "false", "no", "off"}


def _timing_log_path() -> str:
    configured = str(os.environ.get("JUSI_DEBUG_TIMING_FILE", "")).strip()
    return configured or _DEFAULT_TIMING_LOG_PATH


def emit_timing(event: str, **fields: Any) -> None:
    if not _timing_enabled():
        return
    payload = {
        "event": event,
        "ts": time.time(),
        "monotonic": time.monotonic(),
        "pid": os.getpid(),
        "thread": threading.current_thread().name,
    }
    payload.update(fields)
    try:
        line = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    except TypeError:
        sanitized = {key: repr(value) for key, value in payload.items()}
        line = json.dumps(sanitized, sort_keys=True, ensure_ascii=True)
    try:
        with _LOCK:
            with open(_timing_log_path(), "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except OSError:
        return
