from __future__ import annotations

import asyncio
import json
import os
import socket
import uuid
from pathlib import Path

import pytest
import tornado.httpserver

from jusi.application.ports import KernelExecutionResult, KernelOutput
from jusi.application.supervisor import Supervisor
from jusi.interfaces.http import make_application


class FakeKernel:
    pid = 2468

    def execute(self, code: str, *, timeout: float) -> KernelExecutionResult:
        assert code == "1 + 1"
        return KernelExecutionResult("succeeded", (KernelOutput("result", "text/plain", "2"),))

    def stop(self, *, timeout: float) -> None:
        return None


class FakeFactory:
    def start(self, kernel_name: str, *, timeout: float) -> FakeKernel:
        assert kernel_name == "python3"
        return FakeKernel()


def make_command(kind: str, trace_id: str, **payload) -> dict:
    return {
        "protocol_version": 1,
        "command_id": f"cmd_{trace_id}",
        "trace_id": trace_id,
        "kind": kind,
        **payload,
    }


async def request_json(socket_path: str, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    reader, writer = await asyncio.open_unix_connection(socket_path)
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    writer.write(
        (
            f"{method} {path} HTTP/1.1\r\n"
            "Host: localhost\r\n"
            "Connection: close\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n\r\n"
        ).encode("ascii")
        + body
    )
    await writer.drain()
    header = await reader.readuntil(b"\r\n\r\n")
    lines = header.decode("ascii").split("\r\n")
    status = int(lines[0].split()[1])
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.lower()] = value.strip()
    content_length = int(headers.get("content-length", "0"))
    response_body = await reader.readexactly(content_length)
    writer.close()
    await writer.wait_closed()
    return status, json.loads(response_body)


async def run_http_sse_scenario(socket_path: str) -> None:
    supervisor = Supervisor(FakeFactory())
    application = make_application(supervisor)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(socket_path)
    except PermissionError:
        listener.close()
        pytest.skip("sandbox forbids Unix-domain socket binds required by the HTTP adapter test")
    listener.listen(16)
    listener.setblocking(False)
    server = tornado.httpserver.HTTPServer(application)
    server.add_socket(listener)

    sse_reader, sse_writer = await asyncio.open_unix_connection(socket_path)
    sse_writer.write(
        b"GET /v1/events?after=0 HTTP/1.1\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n"
    )
    await sse_writer.drain()
    sse_header = await sse_reader.readuntil(b"\r\n\r\n")
    assert b"200 OK" in sse_header
    assert b"text/event-stream" in sse_header

    try:
        status, started = await request_json(
            socket_path,
            "POST",
            "/v1/kernels",
            make_command("start_kernel", "trace_start", notebook_id="nb", kernel_name="python3"),
        )
        assert status == 201
        kernel_id = started["kernel"]["kernel_id"]

        status, executed = await request_json(
            socket_path,
            "POST",
            f"/v1/kernels/{kernel_id}/executions",
            make_command(
                "execute",
                "trace_execute",
                kernel_id=kernel_id,
                notebook_id="nb",
                cell_id="cell",
                code="1 + 1",
            ),
        )
        assert status == 200
        assert executed["execution"]["outcome"] == "succeeded"

        status, stopped = await request_json(
            socket_path,
            "DELETE",
            f"/v1/kernels/{kernel_id}",
            make_command("stop_kernel", "trace_stop", kernel_id=kernel_id),
        )
        assert status == 200
        assert stopped["cleanup"]["result"] == "stopped"

        events: list[dict] = []
        while len(events) < 12:
            line = await asyncio.wait_for(sse_reader.readline(), timeout=2)
            if line.startswith(b"data: "):
                events.append(json.loads(line[6:]))
        assert [event["sequence"] for event in events] == list(range(1, 13))
        assert [event["kind"] for event in events] == [
            "service.ready",
            "operation.started",
            "kernel.state_changed",
            "operation.completed",
            "operation.started",
            "execution.started",
            "execution.output",
            "execution.completed",
            "operation.completed",
            "operation.started",
            "kernel.state_changed",
            "operation.completed",
        ]
        assert events[6]["payload"]["data"] == "2"

        status, repeated = await request_json(
            socket_path,
            "DELETE",
            f"/v1/kernels/{kernel_id}",
            make_command("stop_kernel", "trace_stop_again", kernel_id=kernel_id),
        )
        assert status == 200
        assert repeated["cleanup"]["result"] == "already_absent"
    finally:
        sse_writer.close()
        await sse_writer.wait_closed()
        server.stop()
        await server.close_all_connections()


def test_http_commands_and_replayable_sse_over_unix_socket(tmp_path: Path) -> None:
    del tmp_path
    socket_path = f"/tmp/jusi-test-{os.getpid()}-{uuid.uuid4().hex[:8]}.sock"
    try:
        asyncio.run(run_http_sse_scenario(socket_path))
    finally:
        try:
            os.unlink(socket_path)
        except FileNotFoundError:
            pass
