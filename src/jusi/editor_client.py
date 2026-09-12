"""Application-side copy/open requests. No Neovim or kernel imports."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket
import sys
import uuid
from typing import Any

from jusi.infrastructure.plugin_worker_channel import read_frame, write_frame
from jusi.application.text_snapshot import CHUNK_CHARS
from jusi.protocol import validate_application_action, validate_editor_action


class EditorDeliveryError(RuntimeError):
    pass


def deliver(content: dict[str, Any], *, socket_path: str | None = None) -> dict[str, str]:
    validate_editor_action(content, content.get("action"))
    text = content["text"]
    return _deliver_chunks({**content, "text": ""}, (text[i:i + CHUNK_CHARS] for i in range(0, len(text), CHUNK_CHARS)),
                           socket_path=socket_path)


def _deliver_chunks(content, chunks, *, socket_path=None):
    validate_editor_action(content, content.get("action"))
    path = socket_path or os.environ.get("JUSI_EDITOR_ACTION_SOCKET")
    if not path:
        raise EditorDeliveryError("No Jusi client action channel is available")
    request_id = f"areq_{uuid.uuid4().hex}"
    request = {"protocol_version": 1, "kind": "application.editor_action_begin", "request_id": request_id, "content": content}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(35)
            connection.connect(path)
            with connection.makefile("rwb") as stream:
                write_frame(stream, request)
                for text in chunks:
                    write_frame(stream, {"protocol_version": 1, "kind": "application.editor_action_chunk",
                                         "request_id": request_id, "text": text, "eof": False})
                write_frame(stream, {"protocol_version": 1, "kind": "application.editor_action_chunk",
                                     "request_id": request_id, "text": "", "eof": True})
                # The service renews its inactivity lease as the editor fetches.
                connection.settimeout(None)
                result = validate_application_action(read_frame(stream))
    except (OSError, EOFError, ValueError) as exc:
        raise EditorDeliveryError("Editor delivery could not be confirmed; request was not retried") from exc
    if result["kind"] != "application.action_result" or result["request_id"] != request_id:
        raise EditorDeliveryError("Editor action response identity mismatch")
    if result["outcome"] != "delivered":
        raise EditorDeliveryError(f"Editor action {result['outcome']}: {result['reason']}")
    return result


def copy(text: str, *, linewise: bool = False) -> dict[str, str]:
    return deliver({"action": "copy", "text": text, "regtype": "V" if linewise else "v"})


def open_text(text: str, *, name: str = "selection.txt", filetype: str = "") -> dict[str, str]:
    return deliver({"action": "open", "text": text, "name": name, "filetype": filetype})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jusivim", description="Open a target-side file in the owning Jusi editor")
    parser.add_argument("path")
    parser.add_argument("--filetype", default="")
    args = parser.parse_args(argv)
    try:
        path = Path(args.path)
        with path.open("r", encoding="utf-8", newline="") as stream:
            _deliver_chunks({"action": "open", "text": "", "name": path.name, "filetype": args.filetype},
                            iter(lambda: stream.read(CHUNK_CHARS), ""))
    except (OSError, ValueError, EditorDeliveryError) as exc:
        print(f"jusivim: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
