"""Disk-backed UTF-8 snapshots. Transfer chunks are bounded; total text is not."""
from __future__ import annotations

import tempfile

CHUNK_CHARS = 16384
CHUNK_BYTES = 65536


class TextSnapshot:
    def __init__(self, content):
        self.content = {**content, "text": ""}
        self.stream = tempfile.TemporaryFile(mode="w+b")
        self.size = 0

    def append(self, text):
        if not isinstance(text, str) or "\x00" in text:
            raise ValueError("Editor text must be UTF-8 without NUL")
        data = text.encode("utf-8")
        self.stream.write(data)
        self.size += len(data)

    def read(self, offset=0, *, chunked=True):
        if type(offset) is not int or not 0 <= offset <= self.size:
            raise ValueError("Invalid text offset")
        self.stream.seek(offset)
        data = self.stream.read(CHUNK_BYTES if chunked else -1)
        # Do not split a UTF-8 character across HTTP records.
        if offset + len(data) < self.size:
            while data:
                try:
                    text = data.decode("utf-8")
                    break
                except UnicodeDecodeError as exc:
                    if exc.start < len(data) - 3:
                        raise
                    data = data[:exc.start]
            else:
                text = ""
        else:
            text = data.decode("utf-8")
        return text, offset + len(data)

    def close(self):
        self.stream.close()

    @classmethod
    def capture(cls, content):
        snapshot = cls(content)
        try:
            for start in range(0, len(content["text"]), CHUNK_CHARS):
                snapshot.append(content["text"][start:start + CHUNK_CHARS])
            return snapshot
        except BaseException:
            snapshot.close()
            raise
