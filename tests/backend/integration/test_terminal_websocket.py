from __future__ import annotations

import asyncio
import json
import queue
import sys

import tornado.httpserver
import tornado.netutil
import tornado.websocket

from jusi.application.ports import TerminalSurfaceRequest
from jusi.application.terminal_surfaces import TerminalAttachment, TerminalChunk, TerminalSurfaceManager
from jusi.domain.models import SurfaceResource
from jusi.infrastructure.terminal_pty import PosixTerminalBroker
from jusi.interfaces.http import make_application
from jusi.protocol import decode_terminal_output_frame, validate_terminal_stream_control


class FakeSurfaceSupervisor:
    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.attachment = TerminalAttachment(
            surface_id="srf_test",
            attachment_id="att_test",
            rows=21,
            cols=79,
            chunks=queue.Queue(),
        )
        self.attaches: list[dict] = []
        self.inputs: list[bytes] = []
        self.resizes: list[tuple[int, int]] = []
        self.detached = asyncio.Event()

    def attach_terminal_surface(self, surface_id: str, **kwargs):  # type: ignore[no-untyped-def]
        self.attaches.append({"surface_id": surface_id, **kwargs})
        return self.attachment, {"earliest_cursor": 0, "latest_cursor": 0}

    def write_terminal_surface(self, surface_id: str, attachment_id: str, data: bytes) -> None:
        assert (surface_id, attachment_id) == ("srf_test", "att_test")
        self.inputs.append(data)

    def resize_terminal_surface(
        self, surface_id: str, attachment_id: str, *, rows: int, cols: int,
    ) -> None:
        assert (surface_id, attachment_id) == ("srf_test", "att_test")
        self.resizes.append((rows, cols))

    def detach_terminal_surface(self, surface_id: str, attachment_id: str) -> None:
        assert (surface_id, attachment_id) == ("srf_test", "att_test")
        self.loop.call_soon_threadsafe(self.detached.set)


async def scenario() -> None:
    supervisor = FakeSurfaceSupervisor()
    application = make_application(supervisor)  # type: ignore[arg-type]
    sockets = tornado.netutil.bind_sockets(0, address="127.0.0.1")
    server = tornado.httpserver.HTTPServer(application)
    server.add_sockets(sockets)
    port = sockets[0].getsockname()[1]
    connection = await tornado.websocket.websocket_connect(
        f"ws://127.0.0.1:{port}/v1/surfaces/srf_test/terminal",
        subprotocols=["jusi.terminal.v1"],
    )
    try:
        await connection.write_message(json.dumps({
            "protocol_version": 1,
            "kind": "attach",
            "surface_id": "srf_test",
            "attachment_id": "att_test",
            "editor_id": "editor_test",
            "cursor": "0",
            "rows": 21,
            "columns": 79,
        }))
        attached = json.loads(await asyncio.wait_for(connection.read_message(), 2))
        assert validate_terminal_stream_control(attached)["kind"] == "attached"
        assert supervisor.attaches == [{
            "surface_id": "srf_test",
            "attachment_id": "att_test",
            "editor_id": "editor_test",
            "rows": 21,
            "cols": 79,
            "after_cursor": 0,
        }]

        supervisor.attachment.chunks.put(TerminalChunk(0, b"\x00\xff\x1b[31mred"))
        cursor, payload = decode_terminal_output_frame(await connection.read_message())
        assert (cursor, payload) == (0, b"\x00\xff\x1b[31mred")

        await connection.write_message(b"input\x00\xff", binary=True)
        deadline = asyncio.get_running_loop().time() + 1
        while not supervisor.inputs and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert supervisor.inputs == [b"input\x00\xff"]

        await connection.write_message(json.dumps({
            "protocol_version": 1,
            "kind": "resize",
            "surface_id": "srf_test",
            "attachment_id": "att_test",
            "resize_id": "resize_one",
            "rows": 30,
            "columns": 100,
        }))
        resized = json.loads(await asyncio.wait_for(connection.read_message(), 2))
        assert validate_terminal_stream_control(resized)["kind"] == "resized"
        assert supervisor.resizes == [(30, 100)]
    finally:
        connection.close()
        await asyncio.wait_for(supervisor.detached.wait(), timeout=2)
        server.stop()
        await server.close_all_connections()


def test_terminal_websocket_relays_control_and_opaque_bytes() -> None:
    asyncio.run(scenario())


class RealSurfaceSupervisor:
    def __init__(self, manager: TerminalSurfaceManager) -> None:
        self.manager = manager

    def attach_terminal_surface(self, surface_id: str, *, editor_id=None, **kwargs):  # type: ignore[no-untyped-def]
        return self.manager.attach(surface_id, **kwargs)

    def write_terminal_surface(self, surface_id: str, attachment_id: str, data: bytes) -> None:
        self.manager.write(surface_id, attachment_id, data)

    def resize_terminal_surface(
        self, surface_id: str, attachment_id: str, *, rows: int, cols: int,
    ) -> None:
        self.manager.resize(surface_id, attachment_id, rows=rows, cols=cols)

    def detach_terminal_surface(self, surface_id: str, attachment_id: str) -> None:
        self.manager.detach(surface_id, attachment_id)


async def real_pty_scenario() -> None:
    manager = TerminalSurfaceManager(PosixTerminalBroker())
    manager.prepare(
        SurfaceResource("srf_real", "cli_real", "run_real", "terminal", ("input", "resize"), "/v1/surfaces/srf_real/terminal"),
        TerminalSurfaceRequest(
            "req_real",
            (
                sys.executable,
                "-c",
                "import os,tty;tty.setraw(0);os.write(1,b'\\x1b[31mready\\xff');data=os.read(0,4);os.write(1,b'got:'+data)",
            ),
        ),
    )
    application = make_application(RealSurfaceSupervisor(manager))  # type: ignore[arg-type]
    sockets = tornado.netutil.bind_sockets(0, address="127.0.0.1")
    server = tornado.httpserver.HTTPServer(application)
    server.add_sockets(sockets)
    port = sockets[0].getsockname()[1]
    connection = await tornado.websocket.websocket_connect(
        f"ws://127.0.0.1:{port}/v1/surfaces/srf_real/terminal",
        subprotocols=["jusi.terminal.v1"],
    )
    cursor = 0
    received = b""
    try:
        await connection.write_message(json.dumps({
            "protocol_version": 1, "kind": "attach", "surface_id": "srf_real",
            "attachment_id": "att_real", "cursor": "0", "rows": 17, "columns": 73,
        }))
        attached = json.loads(await asyncio.wait_for(connection.read_message(), 2))
        assert attached["kind"] == "attached"
        while b"ready\xff" not in received:
            start, payload = decode_terminal_output_frame(await asyncio.wait_for(connection.read_message(), 2))
            assert start == cursor
            cursor += len(payload)
            received += payload
        assert b"\x1b[31mready\xff" in received

        await connection.write_message(b"ping", binary=True)
        while b"got:ping" not in received:
            start, payload = decode_terminal_output_frame(await asyncio.wait_for(connection.read_message(), 2))
            assert start == cursor
            cursor += len(payload)
            received += payload
        assert b"got:ping" in received
    finally:
        connection.close()
        await asyncio.sleep(0.05)
        await asyncio.to_thread(manager.close, "srf_real", timeout=1)
        server.stop()
        await server.close_all_connections()


def test_terminal_websocket_drives_real_geometry_gated_pty_without_decoding_bytes() -> None:
    asyncio.run(real_pty_scenario())
