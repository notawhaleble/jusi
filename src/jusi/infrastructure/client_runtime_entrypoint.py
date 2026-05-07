from __future__ import annotations

import os

from jusi.infrastructure.client_runtime_constants import JUSI_CLIENT_RUNTIME_MODE_ENV
from jusi.infrastructure.client_runtime_host import ClientRuntimeHostFactory, run_client_runtime_mode


def run_client_runtime(
    default_mode: str | None = None,
    *,
    host_factories: dict[str, ClientRuntimeHostFactory] | None = None,
) -> int:
    mode = str(os.environ.get(JUSI_CLIENT_RUNTIME_MODE_ENV, default_mode or "transcript")).strip().lower() or "transcript"
    return run_client_runtime_mode(mode, host_factories=host_factories)
