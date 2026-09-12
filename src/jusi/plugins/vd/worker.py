"""Client-owned snapshot staging and terminal launch; no kernel or VisiData imports."""
from importlib.util import find_spec
from pathlib import Path
import sys
import tempfile

from jusi.plugin_api import OperationRejected, WorkerResult, terminal_surface
from jusi.kernel_artifacts import consume_json_file


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
        self.directory = tempfile.TemporaryDirectory(prefix="jusi-vd-")
        path = Path(self.directory.name) / "snapshot.json"
        try:
            consume_json_file(payload, path)
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
