from __future__ import annotations

import json
import os
import sys

from jusi.interfaces.server import ProtocolServer


def main() -> int:
    os.environ["JUSI_RUNTIME"] = "managed"
    server = ProtocolServer()

    messages = server.handle_message(
        json.dumps(
            {
                "version": 1,
                "kind": "request",
                "type": "start_session",
                "request_id": "req-1",
                "payload": {"notebook_id": "nb-1", "kernel_name": "python3"},
            }
        )
    )
    for message in messages:
        print(message)

    envelopes = [json.loads(message) for message in messages]
    session_id = envelopes[2]["payload"]["session"]["id"]
    client_id = envelopes[4]["payload"]["prepared"]["id"]

    messages = server.handle_message(
        json.dumps(
            {
                "version": 1,
                "kind": "request",
                "type": "bind_prepared_client",
                "request_id": "req-bind-1",
                "payload": {
                    "notebook_id": "nb-1",
                    "session_id": session_id,
                    "client_id": client_id,
                    "client_bufnr": 91,
                },
            }
        )
    )
    for message in messages:
        print(message)

    messages = server.handle_message(
        json.dumps(
            {
                "version": 1,
                "kind": "request",
                "type": "execute_cell",
                "request_id": "req-2",
                "payload": {
                    "notebook_id": "nb-1",
                    "session_id": session_id,
                    "cell": {
                        "id": 12,
                        "kind": "code",
                        "syntax": "python",
                        "main_lines": ["print('smoke')"],
                    },
                },
            }
        )
    )
    for message in messages:
        print(message)
    envelopes = [json.loads(message) for message in messages]
    next_client_id = envelopes[4]["payload"]["prepared"]["id"]

    messages = server.handle_message(
        json.dumps(
            {
                "version": 1,
                "kind": "request",
                "type": "bind_prepared_client",
                "request_id": "req-bind-2",
                "payload": {
                    "notebook_id": "nb-1",
                    "session_id": session_id,
                    "client_id": next_client_id,
                    "client_bufnr": 92,
                },
            }
        )
    )
    for message in messages:
        print(message)

    messages = server.handle_message(
        json.dumps(
            {
                "version": 1,
                "kind": "request",
                "type": "stop_session",
                "request_id": "req-3",
                "payload": {"notebook_id": "nb-1", "session_id": session_id},
            }
        )
    )
    for message in messages:
        print(message)

    return 0


if __name__ == "__main__":
    sys.exit(main())
