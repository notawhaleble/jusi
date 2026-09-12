"""Private target-local data artifacts, owned by the managed kernel lifetime.

References belong inside provider payloads, never editor delivery or public
state. Readers consume ordinary data; this module never unpickles or imports it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import uuid


def publish_json(value):
    root = os.environ.get("JUSI_KERNEL_ARTIFACT_DIRECTORY")
    if not root:
        raise ValueError("Kernel has no Jusi artifact directory")
    path = Path(root) / (uuid.uuid4().hex + ".json")
    try:
        with path.open("x", encoding="utf-8") as stream:
            os.chmod(path, 0o600)
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        return {"artifact_version": 1, "path": str(path)}
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def consume_json_file(reference, destination):
    if (not isinstance(reference, dict) or set(reference) != {"artifact_version", "path"}
            or reference["artifact_version"] != 1 or not isinstance(reference["path"], str)):
        raise ValueError("Invalid kernel artifact reference")
    path = Path(reference["path"])
    if (not path.is_absolute() or not path.parent.name.startswith("jusi-artifacts-")
            or len(path.stem) != 32 or any(c not in "0123456789abcdef" for c in path.stem)
            or path.suffix != ".json"):
        raise ValueError("Invalid kernel artifact path")
    parent = path.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise ValueError("Invalid kernel artifact directory")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Invalid kernel artifact file")
        with os.fdopen(fd, "rb", closefd=False) as source:
            out = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(out, "wb") as target:
                shutil.copyfileobj(source, target, length=65536)
        path.unlink()
    finally:
        os.close(fd)
