"""Client-owned snapshot staging and terminal launch; no kernel or VisiData imports."""
from importlib.util import find_spec
import os
from pathlib import Path
import sys
import tempfile

from jusi.plugin_api import OperationRejected, WorkerResult, terminal_surface
from .snapshot import dumps, restore


class VdWorker:
    def __init__(self, context):
        self.directory = None

    def handle(self, operation, payload):
        if operation != "execute":
            raise OperationRejected("Use VisiData's copy/open keys inside its terminal", reason="unsupported")
        if self.directory is not None:
            raise OperationRejected("VisiData client already initialized", reason="conflict")
        if find_spec("visidata") is None:
            raise OperationRejected("%%vd requires VisiData; install 'jusi[vd]' in the target environment", reason="unsupported")
        restore(payload)
        content = dumps(payload)
        self.directory = tempfile.TemporaryDirectory(prefix="jusi-vd-")
        path = Path(self.directory.name) / "snapshot.json"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
            return WorkerResult({"accepted": True}, (terminal_surface(
                "vd_terminal", (sys.executable, "-m", "jusi.plugins.vd.application", str(path)),
                environment_overrides={"TERM": "xterm-256color"}, signal=True,
            ),))
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.directory is not None:
            self.directory.cleanup()
            self.directory = None


def create_worker(context):
    return VdWorker(context)
