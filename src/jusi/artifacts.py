"""Portable finite artifacts over the existing chunked content channel.

No paths appear on the wire. Each record is bounded and checked against a
SHA-256 over its base64 spelling (portable to Neovim's string-only sha256).
Offsets and declared sizes fence missing, repeated and reordered records.
"""
from __future__ import annotations
import base64
import hashlib
import json
import re
from pathlib import Path

BLOCK = 24576
MAX_BYTES = 256 * 1024 * 1024


def manifest(value):
    if not isinstance(value, dict) or set(value) != {"version", "metadata", "files"} or type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("Invalid artifact manifest")
    if not isinstance(value["metadata"], dict) or len(json.dumps(value["metadata"], ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()) > 16384:
        raise ValueError("Invalid artifact metadata")
    files, names, total = value["files"], set(), 0
    if not isinstance(files, list) or not 1 <= len(files) <= 16:
        raise ValueError("Artifact requires 1–16 files")
    for item in files:
        if not isinstance(item, dict) or set(item) != {"name", "media_type", "size"}:
            raise ValueError("Invalid artifact file")
        name = item["name"]
        if (not isinstance(name, str) or not 1 <= len(name.encode('utf-8')) <= 255
                or name in {'.', '..'} or re.search(r'[/\\:\x00-\x1f\x7f]', name) or name in names):
            raise ValueError("Invalid or duplicate artifact filename")
        if not isinstance(item["media_type"], str) or not re.fullmatch(r"[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+", item["media_type"]):
            raise ValueError("Invalid artifact MIME type")
        if type(item["size"]) is not int or item["size"] < 0:
            raise ValueError("Invalid artifact size")
        total += item["size"]; names.add(name)
    if total > MAX_BYTES:
        raise ValueError("Artifacts exceed 256 MiB")
    return value


def encode(files, metadata=None, *, main_sha256=None):
    """Yield bounded text records. files = [(path, MIME type, filename hint)]."""
    paths = [(Path(path), mime, name) for path, mime, name in files]
    header = manifest({"version": 1, "metadata": metadata or {}, "files": [
        {"name": name, "media_type": mime, "size": path.stat().st_size} for path, mime, name in paths]})
    yield json.dumps(header, ensure_ascii=False) + "\n"
    for index, (path, _, _) in enumerate(paths):
        digest = hashlib.sha256() if index == 0 and main_sha256 is not None else None
        offset = 0
        with path.open("rb") as stream:
            while block := stream.read(BLOCK):
                if digest is not None:
                    digest.update(block)
                data = base64.b64encode(block).decode("ascii")
                yield json.dumps({"index": index, "offset": offset, "data": data,
                                  "sha256": hashlib.sha256(data.encode()).hexdigest()}) + "\n"
                offset += len(block)
        if digest is not None and digest.hexdigest() != main_sha256:
            raise ValueError("Artifact changed after identity offer")
        if offset != header["files"][index]["size"]:
            raise ValueError("Artifact changed during transfer")
    yield '{"end":true}\n'


def decode(source, directory):
    """Decode a complete staged wire file into a caller-owned empty directory."""
    directory = Path(directory)
    created, streams = [], []
    try:
        with Path(source).open("r", encoding="utf-8") as wire:
            def record():
                line = wire.readline(65537)
                if not line.endswith("\n") or len(line.encode("utf-8")) > 65536:
                    raise ValueError("Invalid artifact record")
                return json.loads(line)
            header = manifest(record())
            for item in header["files"]:
                path = directory / item["name"]
                streams.append(path.open("xb")); created.append(path)
            offsets = [0] * len(streams)
            last_index = 0
            while True:
                item = record()
                if isinstance(item, dict) and set(item) == {"end"} and item["end"] is True:
                    if wire.read(1) or offsets != [f["size"] for f in header["files"]]:
                        raise ValueError("Incomplete artifact")
                    return header
                if not isinstance(item, dict) or set(item) != {"index", "offset", "data", "sha256"}:
                    raise ValueError("Invalid artifact data")
                index, data = item["index"], item["data"]
                if type(index) is not int or not last_index <= index < len(streams) or type(item["offset"]) is not int or item["offset"] != offsets[index]:
                    raise ValueError("Artifact offset mismatch")
                if not isinstance(data, str) or hashlib.sha256(data.encode("ascii")).hexdigest() != item["sha256"]:
                    raise ValueError("Artifact checksum mismatch")
                block = base64.b64decode(data, validate=True)
                if not 1 <= len(block) <= BLOCK or base64.b64encode(block).decode() != data:
                    raise ValueError("Invalid artifact block")
                offsets[index] += len(block); last_index = index
                if offsets[index] > header["files"][index]["size"]:
                    raise ValueError("Artifact size exceeded")
                streams[index].write(block)
    except BaseException:
        for stream in streams: stream.close()
        for path in created: path.unlink(missing_ok=True)
        raise
    finally:
        for stream in streams: stream.close()
