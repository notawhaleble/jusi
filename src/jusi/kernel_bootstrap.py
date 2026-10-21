"""Explicit kernel-side initialization for catalog-declared plugin magics."""
from __future__ import annotations


def bootstrap(magic_name: str, arguments: str) -> None:
    from IPython import get_ipython

    ipython = get_ipython()
    magic = ipython.find_cell_magic(magic_name) if ipython is not None else None
    hook = getattr(magic, "__jusi_bootstrap_v1__", None)
    if not callable(hook):
        raise RuntimeError(f"Plugin magic %%{magic_name} has no bootstrap hook")
    # The provider resolves its alias and emits its ordinary exact handoff.
    # Neither a cell body nor a fabricated application request is supplied.
    hook(arguments)
