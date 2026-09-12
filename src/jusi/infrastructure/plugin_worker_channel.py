from __future__ import annotations

import json
import struct
import tempfile
from typing import Any, BinaryIO


DEFAULT_FRAME_LIMIT = 1024 * 1024


class WorkerChannelClosed(EOFError):
    pass


class WorkerFrameError(ValueError):
    pass


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            raise WorkerChannelClosed("Plugin worker control channel closed")
        chunks.extend(chunk)
    return bytes(chunks)


def read_frame(stream: BinaryIO, *, limit: int = DEFAULT_FRAME_LIMIT, allow_editor_stream: bool = False) -> dict[str, Any]:
    header = _read_exact(stream, 4)
    size = struct.unpack(">I", header)[0]
    if size == 0xffffffff and allow_editor_stream:
        if _read_exact(stream, 4) != b"JEA1":
            raise WorkerFrameError("Invalid editor result stream version")
        with tempfile.TemporaryFile() as spool:
            while True:
                count = struct.unpack(">I", _read_exact(stream, 4))[0]
                if count == 0:
                    break
                if count > min(limit, 65536):
                    raise WorkerFrameError("Invalid editor result chunk")
                spool.write(_read_exact(stream, count))
            spool.seek(0)
            try:
                value = json.load(spool)
            except (UnicodeError, ValueError) as exc:
                raise WorkerFrameError("Invalid editor result stream") from exc
        if not isinstance(value, dict) or value.get("kind") != "worker.result" or value.get("operation") != "editor_action":
            raise WorkerFrameError("Only editor results may use a data stream")
        return value
    if size < 2 or size > limit:
        raise WorkerFrameError("Plugin worker frame size is invalid")
    payload = _read_exact(stream, size)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise WorkerFrameError("Plugin worker frame is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise WorkerFrameError("Plugin worker frame must contain an object")
    return value


def write_frame(stream: BinaryIO, value: dict[str, Any], *, limit: int = DEFAULT_FRAME_LIMIT) -> None:
    try:
        payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise WorkerFrameError("Plugin worker frame is not JSON-compatible") from exc
    if len(payload) < 2 or len(payload) > limit:
        raise WorkerFrameError("Plugin worker frame size is invalid")
    stream.write(struct.pack(">I", len(payload)))
    stream.write(payload)
    stream.flush()


def write_editor_result(stream: BinaryIO, value: dict[str, Any], *, limit: int = DEFAULT_FRAME_LIMIT) -> None:
    """Versioned data framing for editor results; ordinary control stays bounded."""
    if value.get("kind") != "worker.result" or value.get("operation") != "editor_action":
        raise WorkerFrameError("Expected editor result")
    with tempfile.TemporaryFile(mode="w+b") as spool:
        # Stage before publishing the header so encoding failure cannot leave a
        # partial frame followed by a purported recoverable response.
        encoder = json.JSONEncoder(separators=(",", ":"), ensure_ascii=False)
        for fragment in encoder.iterencode(value):
            for start in range(0, len(fragment), 16384):
                spool.write(fragment[start:start + 16384].encode("utf-8"))
        spool.seek(0)
        stream.write(struct.pack(">I", 0xffffffff) + b"JEA1")
        while True:
            data = spool.read(min(limit, 65536))
            if not data:
                break
            stream.write(struct.pack(">I", len(data)))
            stream.write(data)
        stream.write(struct.pack(">I", 0))
        stream.flush()
