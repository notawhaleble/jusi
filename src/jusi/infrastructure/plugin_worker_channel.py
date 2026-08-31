from __future__ import annotations

import json
import struct
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


def read_frame(stream: BinaryIO, *, limit: int = DEFAULT_FRAME_LIMIT) -> dict[str, Any]:
    header = _read_exact(stream, 4)
    size = struct.unpack(">I", header)[0]
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
