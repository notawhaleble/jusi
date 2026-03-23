from __future__ import annotations

from typing import TextIO

from jusi.interfaces.server import ProtocolServer


def process_stream(instream: TextIO, outstream: TextIO, server: ProtocolServer | None = None) -> int:
    active_server = server or ProtocolServer()
    for raw in instream:
        message = raw.strip()
        if not message:
            continue
        for response in active_server.handle_message(message):
            outstream.write(response + "\n")
        outstream.flush()
    return 0
