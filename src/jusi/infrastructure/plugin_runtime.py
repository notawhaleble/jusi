from __future__ import annotations

import importlib
import os
import sys
from typing import Callable


def run_plugin_runtime() -> int:
    raw = os.environ.get("JUSI_PLUGIN_RUNTIME_CALLABLE", "").strip()
    if not raw or ":" not in raw:
        sys.stderr.write("missing JUSI_PLUGIN_RUNTIME_CALLABLE\n")
        sys.stderr.flush()
        return 2
    module_name, func_name = raw.split(":", 1)
    module_name = module_name.strip()
    func_name = func_name.strip()
    if not module_name or not func_name:
        sys.stderr.write("invalid JUSI_PLUGIN_RUNTIME_CALLABLE\n")
        sys.stderr.flush()
        return 2
    module = importlib.import_module(module_name)
    target = getattr(module, func_name, None)
    if not callable(target):
        sys.stderr.write("invalid JUSI_PLUGIN_RUNTIME_CALLABLE\n")
        sys.stderr.flush()
        return 2
    return int(target())
