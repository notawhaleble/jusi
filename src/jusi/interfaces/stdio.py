from __future__ import annotations

import select
from typing import TextIO

from jusi.interfaces.server import ProtocolServer


def process_stream(instream: TextIO, outstream: TextIO, server: ProtocolServer | None = None) -> int:
    active_server = server or ProtocolServer()
    try:
        instream_fd = instream.fileno()
    except Exception:
        instream_fd = None
    if instream_fd is None:
        for raw in instream:
            message = raw.strip()
            if not message:
                continue
            for response in active_server.handle_message(message):
                outstream.write(response + "\n")
            for event in active_server.drain_pending_messages():
                outstream.write(event + "\n")
            outstream.flush()
        return 0

    while True:
        for event in active_server.drain_pending_messages():
            outstream.write(event + "\n")
        outstream.flush()

        readable, _writable, _errors = select.select([instream_fd], [], [], 0.05)
        if not readable:
            continue
        raw = instream.readline()
        if raw == "":
            break
        message = raw.strip()
        if not message:
            continue
        for response in active_server.handle_message(message):
            outstream.write(response + "\n")
        outstream.flush()
    return 0
