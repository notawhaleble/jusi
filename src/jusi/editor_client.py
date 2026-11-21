"""Application-side copy/open requests. No Neovim or kernel imports."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket
import sys
import uuid
import threading
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


def _deliver_chunks(content, chunks, *, socket_path=None, reply_sink=None, cancel=None):
    validate_editor_action(content, content.get("action"))
    path = socket_path or os.environ.get("JUSI_EDITOR_ACTION_SOCKET")
    if not path:
        raise EditorDeliveryError("No Jusi client action channel is available")
    request_id = f"areq_{uuid.uuid4().hex}"
    request = {"protocol_version": 1, "kind": "application.editor_action_begin", "request_id": request_id, "content": content}
    stopped = threading.Event()
    def watch(connection):
        while not stopped.wait(0.05):
            if cancel.is_set():
                try: connection.shutdown(socket.SHUT_RDWR)
                except OSError: pass
                return
    try:
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Source capture cancelled")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(35)
            connection.connect(path)
            if cancel is not None:
                threading.Thread(target=watch, args=(connection,), daemon=True).start()
            with connection.makefile("rwb") as stream:
                write_frame(stream, request)
                for text in chunks:
                    write_frame(stream, {"protocol_version": 1, "kind": "application.editor_action_chunk",
                                         "request_id": request_id, "text": text, "eof": False})
                write_frame(stream, {"protocol_version": 1, "kind": "application.editor_action_chunk",
                                     "request_id": request_id, "text": "", "eof": True})
                # The service renews its inactivity lease as the editor fetches.
                connection.settimeout(None)
                reply_done = False
                while True:
                    result = validate_application_action(read_frame(stream))
                    if result["request_id"] != request_id:
                        raise ValueError("Editor reply identity mismatch")
                    if result["kind"] != "application.editor_reply_chunk": break
                    if reply_sink is None or reply_done:
                        raise ValueError("Unexpected artifact reply")
                    reply_sink.write(result["text"])
                    reply_done = result["eof"]
                if reply_sink is not None and result.get("outcome") == "delivered" and not reply_done:
                    raise ValueError("Incomplete artifact reply")
    except (OSError, EOFError, ValueError) as exc:
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Source capture cancelled") from exc
        raise EditorDeliveryError("Editor delivery could not be confirmed; request was not retried") from exc
    finally:
        stopped.set()
    if result["kind"] != "application.action_result" or result["request_id"] != request_id:
        raise EditorDeliveryError("Editor action response identity mismatch")
    if result["outcome"] != "delivered":
        raise EditorDeliveryError(f"Editor action {result['outcome']}: {result['reason']}")
    return result


def copy(text: str, *, linewise: bool = False) -> dict[str, str]:
    return deliver({"action": "copy", "text": text, "regtype": "V" if linewise else "v"})


def open_url(url: str, *, slot: str | None = None) -> dict[str, str]:
    """Open an HTTP(S) URL; a slot replaces only this client's previous page."""
    content = {"action": "open_url", "text": "", "url": url}
    if slot is not None:
        content["slot"] = slot
    return deliver(content)


def close_url(*, slot: str) -> dict[str, str]:
    """Close this client's named URL slot, including a hidden page; absent is OK."""
    return deliver({"action": "close_url", "text": "", "slot": slot})


def open_text(text: str, *, name: str = "selection.txt", filetype: str = "") -> dict[str, str]:
    return deliver({"action": "open", "text": text, "name": name, "filetype": filetype})


def _attention(content: dict) -> str:
    """Wait only for target-side acceptance, never for editor focus or a human."""
    path = os.environ.get("JUSI_EDITOR_ACTION_SOCKET")
    if not path:
        raise EditorDeliveryError("No Jusi client action channel is available")
    request_id = f"areq_{uuid.uuid4().hex}"
    request = {"protocol_version": 1, "kind": "application.attention", "request_id": request_id,
               "content": {"action": "attention", **content}}
    validate_application_action(request)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(5)
            connection.connect(path)
            with connection.makefile("rwb") as stream:
                write_frame(stream, request)
                result = validate_application_action(read_frame(stream))
    except (OSError, EOFError, ValueError) as exc:
        raise EditorDeliveryError("Attention acceptance could not be confirmed; request was not retried") from exc
    if result["kind"] != "application.attention_result" or result["request_id"] != request_id:
        raise EditorDeliveryError("Attention response identity mismatch")
    if result["outcome"] != "accepted":
        raise EditorDeliveryError(f"Attention rejected: {result['reason']}")
    return result["attention_id"]


def request_attention(*, kind: str, message: str) -> str:
    """Publish an action_required or notice item for this client and return its ID."""
    return _attention({"operation": "request", "kind": kind, "message": message})


def update_attention(attention_id: str, *, kind: str, message: str) -> None:
    _attention({"operation": "update", "attention_id": attention_id, "kind": kind, "message": message})


def clear_attention(attention_id: str) -> None:
    """Resolve/withdraw an item; frontend focus never resolves action_required."""
    _attention({"operation": "clear", "attention_id": attention_id})



def show_diff(before: str, after: str, *, before_name: str = "before.txt",
              after_name: str = "after.txt", filetype: str = "") -> dict[str, str]:
    """Display two snapshots; acknowledgment means shown, never accepted."""
    if not isinstance(before, str) or not isinstance(after, str) or "\x00" in before or "\x00" in after:
        raise ValueError("Diff text must be UTF-8 without NUL")
    before_bytes = sum(len(before[i:i + CHUNK_CHARS].encode("utf-8")) for i in range(0, len(before), CHUNK_CHARS))
    content = {"action": "show_diff", "text": "", "before_bytes": before_bytes,
               "before_name": before_name, "after_name": after_name, "filetype": filetype}
    chunks = (text[i:i + CHUNK_CHARS] for text in (before, after) for i in range(0, len(text), CHUNK_CHARS))
    return _deliver_chunks(content, chunks)

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


def open_document(path, *, media_type, slot, provider="", lifetime="client", document_id=None, assets=(), name=None, placement="default"):
    """Offer identity first; core selects reuse, negotiated local staging or HTTP."""
    from jusi.artifacts import encode, manifest
    from jusi.protocol import validate_document_offer_reply
    from jusi.infrastructure.shared_artifacts import DIRECTORY_ENV, TOKEN_ENV
    import hashlib
    import json
    import tempfile
    import shutil
    path = Path(path)
    inferred_identity = document_id is None
    if inferred_identity:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(65536), b""): digest.update(block)
        document_id = digest.hexdigest()
    name = path.name if name is None else name
    files = [(path, media_type, name), *[(Path(p), mime, hint) for p, mime, hint in assets]]
    header = manifest({"version": 1, "metadata": {"document_id": document_id}, "files": [
        {"name": hint, "media_type": mime, "size": p.stat().st_size} for p, mime, hint in files]})
    text = json.dumps(header, ensure_ascii=False)
    presentation = {"slot": slot, "provider": provider, "lifetime": lifetime}
    if placement != "default":
        presentation["placement"] = placement
    directory, token = os.environ.get(DIRECTORY_ENV, ""), os.environ.get(TOKEN_ENV, "")
    token = token if directory else ""
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as reply:
        result = _deliver_chunks({"action": "offer_document", "text": "", **presentation,
            "shared_token": token}, (text,), reply_sink=reply)
        reply.seek(0)
        decision = json.loads(reply.read(65537))
    validate_document_offer_reply(decision)
    if decision["transport"] == "reused":
        return result
    if decision["transport"] == "shared":
        if not directory or not token:
            raise EditorDeliveryError("Shared artifacts were not negotiated")
        artifact_id = uuid.uuid4().hex
        stage = Path(directory) / artifact_id
        prepared, created = False, False
        try:
            stage.mkdir(mode=0o700)
            created = True
            for index, (source, _, hint) in enumerate(files):
                digest = hashlib.sha256()
                with source.open("rb") as incoming, (stage / hint).open("xb") as outgoing:
                    before = os.fstat(incoming.fileno())
                    for block in iter(lambda: incoming.read(1024 * 1024), b""):
                        if outgoing.tell() + len(block) > header["files"][index]["size"]:
                            raise ValueError("Artifact grew after identity offer")
                        outgoing.write(block)
                        if index == 0 and inferred_identity: digest.update(block)
                    after = os.fstat(incoming.fileno())
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or outgoing.tell() != header["files"][index]["size"]:
                        raise ValueError("Artifact changed during snapshot")
                if index == 0 and inferred_identity and digest.hexdigest() != document_id:
                    raise ValueError("Artifact changed after identity offer")
                (stage / hint).chmod(0o400)
            prepared = True
        except OSError:
            # No presentation has been attempted. A lost/unavailable shared mount
            # can safely use the content channel instead.
            pass
        finally:
            if created and not prepared: shutil.rmtree(stage, ignore_errors=True)
        if prepared:
            try:
                return _deliver_chunks({"action": "open_shared_document", "text": "", **presentation,
                    "shared_token": token, "artifact_id": artifact_id}, (text,))
            finally:
                # Adoption moves the directory out of service-owned staging.
                shutil.rmtree(stage, ignore_errors=True)
    return _deliver_chunks({"action": "open_document", "text": "", **presentation},
                           encode(files, header["metadata"], main_sha256=document_id if inferred_identity else None))


def close_document(*, slot):
    return deliver({"action": "close_document", "text": "", "slot": slot})


def source_snapshot(directory, *, slot, context_id="", cancel=None):
    """Capture a submitted source or a bound document into a target-local directory."""
    import tempfile
    from jusi.artifacts import decode
    with tempfile.NamedTemporaryFile(mode="w+", encoding="utf-8") as wire:
        _deliver_chunks({"action": "source_snapshot", "text": "", "slot": slot,
            "context_id": context_id}, (), reply_sink=wire, cancel=cancel)
        wire.flush()
        return decode(wire.name, directory)
