from __future__ import annotations

import importlib
import json
import os

from jusi.infrastructure.runtime import JUSI_KERNEL_EXTENSIONS_ENV


def _configured_extension_modules() -> list[str]:
    raw = os.environ.get(JUSI_KERNEL_EXTENSIONS_ENV, "")
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    modules: list[str] = []
    for value in parsed:
        name = str(value).strip()
        if name:
            modules.append(name)
    return modules


def load_ipython_extension(ipython) -> None:  # type: ignore[no-untyped-def]
    loaded = getattr(ipython, "_jusi_loaded_extensions", None)
    if not isinstance(loaded, set):
        loaded = set()
        setattr(ipython, "_jusi_loaded_extensions", loaded)
    for module_name in _configured_extension_modules():
        if module_name in loaded:
            continue
        module = importlib.import_module(module_name)
        loader = getattr(module, "load_ipython_extension", None)
        if not callable(loader):
            raise RuntimeError(f"Jusi kernel extension lacks load_ipython_extension: {module_name}")
        loader(ipython)
        loaded.add(module_name)
