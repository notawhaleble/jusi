from __future__ import annotations

import base64
import json
import os
import pickle
import sys


def run_vd_runner() -> int:
    raw_payload = os.environ.get("JUSI_VD_PAYLOAD_JSON", "").strip()
    if not raw_payload:
        sys.stderr.write("missing JUSI_VD_PAYLOAD_JSON\n")
        sys.stderr.flush()
        return 2
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        sys.stderr.write("invalid JUSI_VD_PAYLOAD_JSON\n")
        sys.stderr.flush()
        return 2
    if not isinstance(payload, dict):
        sys.stderr.write("invalid JUSI_VD_PAYLOAD_JSON\n")
        sys.stderr.flush()
        return 2

    raw_content = str(payload.get("content", "")).strip()
    meta = payload.get("meta", {})
    if not raw_content:
        sys.stderr.write("missing %%vd payload content\n")
        sys.stderr.flush()
        return 2

    try:
        value = pickle.loads(base64.b64decode(raw_content.encode("ascii")))
    except Exception as exc:
        sys.stderr.write(f"failed to decode %%vd payload: {exc}\n")
        sys.stderr.flush()
        return 2

    try:
        import visidata
    except ModuleNotFoundError:
        sys.stderr.write("VisiData is not available in the active environment\n")
        sys.stderr.flush()
        return 2

    if isinstance(meta, dict) and meta.get("ftype") == "pandas":
        visidata.vd.view_pandas(value)
    else:
        visidata.vd.view(value)
    return 0
