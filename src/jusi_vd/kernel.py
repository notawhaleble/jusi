from __future__ import annotations

import base64
import pickle

from jusi.domain.models import JUSI_HANDLER_HANDOFF_MIME


def load_ipython_extension(ipython) -> None:  # type: ignore[no-untyped-def]
    cell_magics = getattr(getattr(ipython, "magics_manager", None), "magics", {}).get("cell", {})
    if "vd" in cell_magics:
        return

    def _jusi_vd_magic(line, cell):  # type: ignore[no-untyped-def]
        from IPython.display import display

        user_ns = getattr(ipython, "user_ns", {})
        value = eval(cell, user_ns, user_ns)
        meta = {"line": line}
        if getattr(type(value), "__module__", "").startswith("pandas"):
            meta["ftype"] = "pandas"
        content = base64.b64encode(pickle.dumps(value)).decode("ascii")
        payload = {"handler_id": "vd", "magic_name": "vd", "content": content, "meta": meta}
        display({JUSI_HANDLER_HANDOFF_MIME: payload}, raw=True, metadata={JUSI_HANDLER_HANDOFF_MIME: {"line": line}})

    ipython.register_magic_function(_jusi_vd_magic, magic_kind="cell", magic_name="vd")
