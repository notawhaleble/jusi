from __future__ import annotations

import os
import select
import signal
from typing import TextIO

from jusi.interfaces.server import ProtocolServer


def process_stream(instream: TextIO, outstream: TextIO, server: ProtocolServer | None = None) -> int:
    active_server = server or ProtocolServer()
    running = {"value": True}

    def _handle_stop(_signum: int, _frame: object) -> None:
        running["value"] = False

    try:
        previous_sigterm = signal.signal(signal.SIGTERM, _handle_stop)
        previous_sigint = signal.signal(signal.SIGINT, _handle_stop)
        try:
            instream_fd = instream.fileno()
        except Exception:
            instream_fd = None
        if instream_fd is None:
            for raw in instream:
                if active_server.poll_session_timeouts():
                    break
                active_server.poll_frontend_health()
                if not running["value"]:
                    break
                message = raw.strip()
                if not message:
                    continue
                for response in active_server.handle_message(message):
                    outstream.write(response + "\n")
                for event in active_server.drain_pending_messages():
                    outstream.write(event + "\n")
                outstream.flush()
            return 0

        pending = ""
        while running["value"]:
            if active_server.poll_session_timeouts():
                break
            active_server.poll_frontend_health()
            for event in active_server.drain_pending_messages():
                outstream.write(event + "\n")
            outstream.flush()

            readable, _writable, _errors = select.select([instream_fd], [], [], 0.05)
            if not readable:
                continue
            chunk = os.read(instream_fd, 4096)
            if chunk == b"":
                if pending.strip():
                    for response in active_server.handle_message(pending.strip()):
                        outstream.write(response + "\n")
                    for event in active_server.drain_pending_messages():
                        outstream.write(event + "\n")
                    outstream.flush()
                break
            pending += chunk.decode("utf-8")
            while True:
                newline = pending.find("\n")
                if newline < 0:
                    break
                raw = pending[:newline]
                pending = pending[newline + 1 :]
                message = raw.strip()
                if not message:
                    continue
                for response in active_server.handle_message(message):
                    outstream.write(response + "\n")
                for event in active_server.drain_pending_messages():
                    outstream.write(event + "\n")
                outstream.flush()
        return 0
    finally:
        try:
            signal.signal(signal.SIGTERM, previous_sigterm)
            signal.signal(signal.SIGINT, previous_sigint)
        except Exception:
            pass
        active_server.close()
