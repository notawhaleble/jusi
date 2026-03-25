from __future__ import annotations

import json
import os
import sys
import time

from jusi.interfaces.server import ProtocolServer


def _send(server: ProtocolServer, request_id: str, request_type: str, payload: dict) -> list[dict]:
    messages = server.handle_message(
        json.dumps(
            {
                "version": 1,
                "kind": "request",
                "type": request_type,
                "request_id": request_id,
                "payload": payload,
            }
        )
    )
    for message in messages:
        print(message)

    envelopes = [json.loads(message) for message in messages]
    response = envelopes[0]
    if not response.get("ok", False):
        raise RuntimeError(f"{request_type} failed: {response.get('error', {})}")
    return envelopes


def _drain(server: ProtocolServer, timeout: float = 2.0) -> list[dict]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        messages = server.drain_pending_messages()
        if messages:
            for message in messages:
                print(message)
            return [json.loads(message) for message in messages]
        time.sleep(0.02)
    raise RuntimeError("timed out waiting for pending backend events")


def main() -> int:
    os.environ["JUSI_RUNTIME"] = "managed"
    server = ProtocolServer()

    envelopes = _send(
        server,
        request_id="req-1",
        request_type="start_session",
        payload={"notebook_id": "nb-1", "kernel_name": "python3"},
    )
    if envelopes[1]["type"] != "session_updated" or envelopes[1]["payload"]["session"]["state"] != "starting":
        raise RuntimeError("start_session did not emit starting session update first")
    if envelopes[4]["type"] != "prepared_updated" or envelopes[4]["payload"]["prepared"]["state"] != "binding":
        raise RuntimeError("start_session did not end in prepared binding state")
    session_id = envelopes[2]["payload"]["session"]["id"]
    prepared_client_id = envelopes[4]["payload"]["prepared"]["id"]

    envelopes = _send(
        server,
        request_id="req-bind-1",
        request_type="bind_prepared_client",
        payload={
            "notebook_id": "nb-1",
            "session_id": session_id,
            "client_id": prepared_client_id,
            "client_bufnr": 91,
        },
    )
    if envelopes[1]["payload"]["prepared"]["state"] != "ready":
        raise RuntimeError("bind_prepared_client did not mark prepared client ready")

    envelopes = _send(
        server,
        request_id="req-2",
        request_type="execute_cell",
        payload={
            "notebook_id": "nb-1",
            "session_id": session_id,
            "cell": {
                "id": 12,
                "kind": "code",
                "syntax": "python",
                "main_lines": ["print('smoke')"],
            },
        },
    )
    if envelopes[3]["payload"]["cell"]["status"] != "busy":
        raise RuntimeError("execute_cell did not emit busy state first")
    active_client_id = envelopes[3]["payload"]["cell"]["client_id"]
    next_prepared_client_id = envelopes[4]["payload"]["prepared"]["id"]

    envelopes = _drain(server)
    if envelopes[0]["type"] != "cell_updated" or envelopes[0]["payload"]["cell"]["status"] != "done":
        raise RuntimeError("managed execute did not emit terminal done state asynchronously")

    envelopes = _send(
        server,
        request_id="req-bind-2",
        request_type="bind_prepared_client",
        payload={
            "notebook_id": "nb-1",
            "session_id": session_id,
            "client_id": next_prepared_client_id,
            "client_bufnr": 92,
        },
    )
    if envelopes[1]["payload"]["prepared"]["state"] != "ready":
        raise RuntimeError("second bind_prepared_client did not mark next prepared client ready")

    envelopes = _send(
        server,
        request_id="req-inspect",
        request_type="inspect_client",
        payload={
            "notebook_id": "nb-1",
            "session_id": session_id,
            "client_id": active_client_id,
        },
    )
    client_view = envelopes[0]["payload"]["client"]
    if client_view["title"] != "cell 12: done":
        raise RuntimeError("inspect_client did not return the expected client title")
    if client_view["revision"] <= 0:
        raise RuntimeError("inspect_client did not return a positive client revision")
    if client_view["lines"] != [
        f"meta> client={active_client_id} session={session_id} bufnr=91",
        "started cell 12 [code:python]",
        "stdout> smoke",
        "finished: done",
    ]:
        raise RuntimeError("inspect_client did not return the expected rendered client lines")

    envelopes = _send(
        server,
        request_id="req-shutdown-cell",
        request_type="shutdown_client",
        payload={
            "notebook_id": "nb-1",
            "session_id": session_id,
            "cell_id": 12,
            "client_id": active_client_id,
            "reason": "user_close",
        },
    )
    if envelopes[1]["payload"]["cell"]["client_state"] != "shutting_down":
        raise RuntimeError("cell shutdown did not emit shutting_down state first")
    if envelopes[2]["payload"]["cell"]["client_state"] != "shutdown":
        raise RuntimeError("cell shutdown did not emit shutdown state")

    envelopes = _send(
        server,
        request_id="req-shutdown-prepared",
        request_type="shutdown_client",
        payload={
            "notebook_id": "nb-1",
            "session_id": session_id,
            "cell_id": 0,
            "client_id": next_prepared_client_id,
            "reason": "user_close",
        },
    )
    if envelopes[1]["payload"]["prepared"]["client_state"] != "shutting_down":
        raise RuntimeError("prepared shutdown did not emit shutting_down state first")
    if envelopes[2]["payload"]["prepared"]["state"] != "missing":
        raise RuntimeError("prepared shutdown did not end in missing state")

    envelopes = _send(
        server,
        request_id="req-3",
        request_type="stop_session",
        payload={"notebook_id": "nb-1", "session_id": session_id},
    )
    if envelopes[1]["payload"]["session"]["state"] != "stopping":
        raise RuntimeError("stop_session did not emit stopping state first")
    if envelopes[-1]["payload"]["session"]["state"] != "stopped":
        raise RuntimeError("stop_session did not emit stopped state")

    return 0


if __name__ == "__main__":
    sys.exit(main())
