from __future__ import annotations

import os
import signal
import sys
import tty

from IPython.display import display

from jusi.plugin_api import WorkerResult, terminal_surface


HANDOFF_MIME = "application/vnd.jusi.handoff.v1+json"


def catalog_entry() -> dict:
    return {
        "plugin_id": "terminal_fixture",
        "plugin_version": "1.0.0",
        "distribution": "jusi-terminal-fixture",
        "families": [{
            "family_id": "terminal_fixture",
            "magic_name": "terminal_fixture",
            "capabilities": ["execute"],
        }],
        "kernel_extensions": ["jusi_terminal_fixture"],
        "worker_entry_point": "jusi_terminal_fixture:create_worker",
        "media_types": ["text/x-ansi"],
        "interaction": "terminal_interactive",
    }


def jusi_kernel_adapter_v1() -> dict:
    return {
        "plugin_id": "terminal_fixture",
        "plugin_version": "1.0.0",
        "families": [{"family_id": "terminal_fixture", "magic_name": "terminal_fixture"}],
    }


def load_ipython_extension(ipython) -> None:  # type: ignore[no-untyped-def]
    def fixture_magic(line: str, cell: str) -> None:
        display({HANDOFF_MIME: {
            "protocol_version": 1,
            "kind": "plugin.handoff",
            "plugin_id": "terminal_fixture",
            "plugin_version": "1.0.0",
            "family_id": "terminal_fixture",
            "magic_name": "terminal_fixture",
            "payload": {"line": line, "body": cell},
        }}, raw=True)

    ipython.register_magic_function(fixture_magic, "cell", "terminal_fixture")


class FixtureWorker:
    def __init__(self, context) -> None:  # type: ignore[no-untyped-def]
        self.context = context

    def handle(self, operation: str, payload: dict) -> WorkerResult:
        if operation != "execute":
            raise ValueError(f"unsupported fixture operation: {operation}")
        return WorkerResult(
            {"accepted": True},
            (terminal_surface(
                "terminal_fixture_main",
                (sys.executable, "-m", "jusi_terminal_fixture", "--application"),
                environment_overrides={"TERM": "xterm-256color"},
                signal=True,
            ),),
        )


def create_worker(context) -> FixtureWorker:  # type: ignore[no-untyped-def]
    return FixtureWorker(context)


def run_application() -> int:
    tty.setraw(sys.stdin.fileno())

    def draw_size(prefix: str) -> None:
        size = os.get_terminal_size(sys.stdin.fileno())
        os.write(sys.stdout.fileno(), f"\r\n\x1b[36m{prefix}={size.columns}x{size.lines}\x1b[0m\r\n".encode())

    signal.signal(signal.SIGWINCH, lambda *_: draw_size("resized"))
    draw_size("initial")
    os.write(sys.stdout.fileno(), b"Type in this terminal; the target echoes opaque bytes. Close with :JusiCloseClient.\r\n> ")
    while True:
        value = os.read(sys.stdin.fileno(), 4096)
        if not value:
            return 0
        os.write(sys.stdout.fileno(), b"\x1b[33m" + value + b"\x1b[0m")


if __name__ == "__main__":
    if sys.argv[1:] != ["--application"]:
        raise SystemExit("fixture module is not a user command")
    raise SystemExit(run_application())
