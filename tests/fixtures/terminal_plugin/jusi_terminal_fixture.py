from __future__ import annotations

import codecs
import json
import os
import select
import tempfile
from pathlib import Path
import signal
import sys
import tty
import threading
import subprocess

from IPython.display import display

from jusi.plugin_api import WorkerResult, terminal_surface, copy_text, open_text, OperationInterrupted, OperationRejected


HANDOFF_MIME = "application/vnd.jusi.handoff.v1+json"


def catalog_entry() -> dict:
    return {
        "plugin_id": "terminal_fixture",
        "plugin_version": "1.0.0",
        "distribution": "jusi-terminal-fixture",
        "families": [{
            "family_id": "terminal_fixture",
            "magic_name": "terminal_fixture",
            "capabilities": ["execute", "followup", "complete", "interrupt", "editor_actions"],
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
        self.followup_count = 0
        self.cancel = threading.Event()
        self.selection = ""
        self.directory = tempfile.TemporaryDirectory(prefix="jusi-terminal-fixture-")
        self.submissions = Path(self.directory.name) / "submissions.jsonl"
        self.submissions.touch(mode=0o600)

    def publish(self, label: str, body: str) -> None:
        with self.submissions.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"label": label, "body": body}) + "\n")

    def close(self) -> None:
        self.directory.cleanup()

    def interrupt(self) -> None:
        self.cancel.set()

    def handle(self, operation: str, payload: dict) -> WorkerResult:
        if operation == "editor_action":
            if payload["selection"].get("scope") == "missing":
                raise OperationRejected("No selection")
            if payload["action"] == "copy":
                return copy_text(self.selection)
            return open_text(self.selection, name="selection.txt", filetype="text")
        if operation == "complete":
            assert payload["prefix"] == payload["body"][:payload["cursor_pos"]]
            return WorkerResult({"items": [
                {"text": "public.", "label": "public", "detail": "schema",
                 "start": payload["cursor_pos"], "end": payload["cursor_pos"]},
                {"text": "private.", "label": "private", "detail": "schema",
                 "start": payload["cursor_pos"], "end": payload["cursor_pos"]},
            ]})
        if operation == "followup":
            if payload["body"] == "fixture:wait":
                self.publish("waiting", "interruptible fixture operation")
                try:
                    if not self.cancel.wait(60):
                        raise OperationRejected("Test fixture wait expired")
                    raise OperationInterrupted()
                finally:
                    self.cancel.clear()
            self.selection = payload["body"]
            self.followup_count += 1
            self.publish(f"followup {self.followup_count}", payload["body"])
            return WorkerResult({"body": payload["body"], "count": self.followup_count})
        if operation != "execute":
            raise ValueError(f"unsupported fixture operation: {operation}")
        self.selection = payload.get("body", "")
        self.publish("initial submission", self.selection)
        return WorkerResult(
            {"accepted": True},
            (terminal_surface(
                "terminal_fixture_main",
                (sys.executable, "-m", "jusi_terminal_fixture", "--application", str(self.submissions)),
                environment_overrides={"TERM": "xterm-256color"},
                signal=True,
            ),),
        )


def create_worker(context) -> FixtureWorker:  # type: ignore[no-untyped-def]
    return FixtureWorker(context)


def run_application() -> int:
    tty.setraw(sys.stdin.fileno())

    typed = ""
    selection = ""

    def draw_size(prefix: str) -> None:
        size = os.get_terminal_size(sys.stdin.fileno())
        os.write(sys.stdout.fileno(), f"\r\n\x1b[36m{prefix}={size.columns}x{size.lines}\x1b[0m\r\n".encode())
        if prefix == "resized":
            os.write(sys.stdout.fileno(), ("> " + typed).encode())

    signal.signal(signal.SIGWINCH, lambda *_: draw_size("resized"))
    draw_size("initial")
    os.write(sys.stdout.fileno(), b"Followups appear here. Type a line and press Enter; Ctrl-C exits.\r\n> ")
    typed = ""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    with open(sys.argv[2], "rb") as submissions:
        while True:
            # The worker's private journal also preserves submissions made before
            # terminal attachment. Only complete records are consumed.
            while True:
                offset = submissions.tell()
                record = submissions.readline()
                if not record.endswith(b"\n"):
                    submissions.seek(offset)
                    break
                item = json.loads(record)
                if item["label"].startswith("followup ") or item["label"] == "initial submission":
                    selection = item["body"]
                body = item["body"].replace("\r", "\\r").replace("\n", "\r\n")
                message = f"\r\x1b[2K\x1b[36m{item['label']}:\x1b[0m\r\n{body}\r\n> {typed}"
                os.write(sys.stdout.fileno(), message.encode())
            if not select.select([sys.stdin], [], [], 0.05)[0]:
                continue
            value = os.read(sys.stdin.fileno(), 4096)
            if not value:
                return 0
            if b"\x03" in value:
                os.write(sys.stdout.fileno(), b"\r\ntarget received control-c and will exit\r\n")
                return 7
            for char in decoder.decode(value):
                if char in "\r\n":
                    if typed in {"action:copy", "action:open", "action:file", "action:diff"}:
                        from jusi import editor_client
                        try:
                            if typed == "action:copy":
                                editor_client.copy(selection)
                            elif typed == "action:diff":
                                editor_client.show_diff(selection, selection + "\nchanged α",
                                                        before_name="before.txt", after_name="after.txt", filetype="text")
                            elif typed == "action:open":
                                editor_client.open_text(selection, name="application.txt", filetype="text")
                            else:
                                source = Path(sys.argv[2]).with_name("application-file.txt")
                                source.write_text(selection, encoding="utf-8")
                                try:
                                    subprocess.run([sys.executable, "-m", "jusi.editor_client", str(source), "--filetype", "text"], check=True)
                                finally:
                                    source.unlink(missing_ok=True)
                            os.write(sys.stdout.fileno(), f"\r\n{typed} delivered\r\n> ".encode())
                        except Exception as exc:
                            os.write(sys.stdout.fileno(), f"\r\n{typed} failed: {exc}\r\n> ".encode())
                    else:
                        os.write(sys.stdout.fileno(), f"\r\ninput: {typed}\r\n> ".encode())
                    typed = ""
                elif char in "\x7f\b":
                    typed = typed[:-1]
                    os.write(sys.stdout.fileno(), ("\r\x1b[2K> " + typed).encode())
                elif char.isprintable():
                    typed += char
                    os.write(sys.stdout.fileno(), char.encode())


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--application":
        raise SystemExit("fixture module is not a user command")
    raise SystemExit(run_application())
