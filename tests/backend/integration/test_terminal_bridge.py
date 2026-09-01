from __future__ import annotations

import asyncio
import json
import os

import pytest
import tornado.httpserver
import tornado.netutil
import tornado.web
import tornado.websocket

from jusi.infrastructure.terminal_bridge import (
    MAX_CURSOR,
    OUTPUT_HEADER,
    TerminalBridge,
    TerminalBridgeError,
    TerminalSurfaceRejected,
    terminal_websocket_url,
)


pytestmark = pytest.mark.skipif(os.name != "posix", reason="terminal bridge requires POSIX file descriptors")


class FakeTerminalEndpoint(tornado.websocket.WebSocketHandler):
    messages: list[dict[str, object] | bytes]
    attached: asyncio.Event
    resized: asyncio.Event

    def check_origin(self, origin: str) -> bool:
        return True

    def select_subprotocol(self, subprotocols: list[str]) -> str | None:
        return "jusi.terminal.v1" if "jusi.terminal.v1" in subprotocols else None

    def on_message(self, message: str | bytes) -> None:
        if isinstance(message, bytes):
            self.messages.append(message)
            return
        value = json.loads(message)
        self.messages.append(value)
        if value["kind"] == "attach":
            self.write_message(
                json.dumps(
                    {
                        "protocol_version": 1,
                        "kind": "attached",
                        "surface_id": value["surface_id"],
                        "attachment_id": value["attachment_id"],
                        "rows": value["rows"],
                        "columns": value["columns"],
                        "cursor": value["cursor"],
                    }
                )
            )
            self.write_message(OUTPUT_HEADER.pack(1, 1, 0) + b"\x1b[31mraw:\xff\x1b[0m", binary=True)
            self.attached.set()
        elif value["kind"] == "resize":
            self.write_message(
                json.dumps(
                    {
                        "protocol_version": 1,
                        "kind": "resized",
                        "surface_id": value["surface_id"],
                        "attachment_id": value["attachment_id"],
                        "resize_id": value["resize_id"],
                        "rows": value["rows"],
                        "columns": value["columns"],
                    }
                )
            )
            self.resized.set()


class ReconnectingTerminalEndpoint(tornado.websocket.WebSocketHandler):
    attaches: list[dict[str, object]]
    attempts: int
    replayed: asyncio.Event
    expire_replay: bool

    def check_origin(self, origin: str) -> bool:
        return True

    def select_subprotocol(self, subprotocols: list[str]) -> str | None:
        return "jusi.terminal.v1" if "jusi.terminal.v1" in subprotocols else None

    def on_message(self, message: str | bytes) -> None:
        assert isinstance(message, str)
        value = json.loads(message)
        assert value["kind"] == "attach"
        self.attaches.append(value)
        type(self).attempts += 1
        attempt = type(self).attempts
        if attempt == 2 and self.expire_replay:
            self.write_message(json.dumps({
                "protocol_version": 1,
                "kind": "failure",
                "surface_id": value["surface_id"],
                "attachment_id": value["attachment_id"],
                "operation": "attach",
                "reason": "cursor_expired",
                "message": "requested bytes are no longer retained",
                "retryable": True,
            }))
            return
        self.write_message(json.dumps({
            "protocol_version": 1,
            "kind": "attached",
            "surface_id": value["surface_id"],
            "attachment_id": value["attachment_id"],
            "rows": value["rows"],
            "columns": value["columns"],
            "cursor": value["cursor"],
        }))
        if attempt == 1:
            self.write_message(OUTPUT_HEADER.pack(1, 1, 0) + b"abc", binary=True)
            asyncio.get_running_loop().call_later(0.01, self.close)
        else:
            assert value["cursor"] == "3"
            self.write_message(OUTPUT_HEADER.pack(1, 1, 3) + b"def", binary=True)
            self.replayed.set()


def test_terminal_websocket_url_is_target_relative() -> None:
    assert (
        terminal_websocket_url("https://target.example/jusi/", "surf_123")
        == "wss://target.example/jusi/v1/surfaces/surf_123/terminal"
    )
    with pytest.raises(ValueError):
        terminal_websocket_url("target.example", "surf_123")
    with pytest.raises(ValueError):
        terminal_websocket_url("https://target.example?token=secret", "surf_123")


def test_bridge_relays_raw_bytes_and_verified_geometry_controls() -> None:
    asyncio.run(_bridge_round_trip())


async def _bridge_round_trip() -> None:
    input_read, input_write = os.pipe()
    output_read, output_write = os.pipe()
    error_read, error_write = os.pipe()
    geometry = [os.terminal_size((73, 19))]
    FakeTerminalEndpoint.messages = []
    FakeTerminalEndpoint.attached = asyncio.Event()
    FakeTerminalEndpoint.resized = asyncio.Event()
    application = tornado.web.Application(
        [(r"/v1/surfaces/surf_test/terminal", FakeTerminalEndpoint)]
    )
    sockets = tornado.netutil.bind_sockets(0, address="127.0.0.1")
    server = tornado.httpserver.HTTPServer(application)
    server.add_sockets(sockets)
    port = sockets[0].getsockname()[1]
    bridge = TerminalBridge(
        f"http://127.0.0.1:{port}",
        "surf_test",
        stdin_fd=input_read,
        stdout_fd=output_write,
        stderr_fd=error_write,
        geometry=lambda: geometry[0],
        install_signal_handler=False,
    )
    task = asyncio.create_task(bridge.run())
    try:
        await asyncio.wait_for(FakeTerminalEndpoint.attached.wait(), timeout=2)
        os.write(input_write, b"input:\x00\xff")
        geometry[0] = os.terminal_size((101, 31))
        await bridge.send_resize()
        await asyncio.wait_for(FakeTerminalEndpoint.resized.wait(), timeout=2)
        deadline = asyncio.get_running_loop().time() + 2
        while bridge._pending_resizes and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert not bridge._pending_resizes
        os.close(input_write)
        input_write = -1
        await asyncio.wait_for(task, timeout=2)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        server.stop()
        await server.close_all_connections()
        for fd in (input_read, input_write, output_write, error_write):
            if fd >= 0:
                os.close(fd)

    output = os.read(output_read, 65536)
    error = os.read(error_read, 65536)
    os.close(output_read)
    os.close(error_read)
    assert output == b"\x1b[31mraw:\xff\x1b[0m"
    assert error == b""
    attach = FakeTerminalEndpoint.messages[0]
    assert isinstance(attach, dict)
    assert attach == {
        "protocol_version": 1,
        "kind": "attach",
        "surface_id": "surf_test",
        "attachment_id": bridge.attachment_id,
        "rows": 19,
        "columns": 73,
        "cursor": "0",
    }
    assert b"input:\x00\xff" in FakeTerminalEndpoint.messages
    resize = next(
        value
        for value in FakeTerminalEndpoint.messages
        if isinstance(value, dict) and value.get("kind") == "resize"
    )
    assert resize["rows"] == 31
    assert resize["columns"] == 101


def test_output_cursor_gaps_are_rejected_without_guessing() -> None:
    output_read, output_write = os.pipe()
    bridge = TerminalBridge(
        "http://127.0.0.1:1",
        "surf_test",
        stdout_fd=output_write,
        geometry=lambda: os.terminal_size((80, 24)),
        install_signal_handler=False,
    )
    try:
        bridge._accept_output(OUTPUT_HEADER.pack(1, 1, 0) + b"abc")
        with pytest.raises(TerminalBridgeError, match="cursor gap"):
            bridge._accept_output(OUTPUT_HEADER.pack(1, 1, 4) + b"def")
        bridge._last_cursor = MAX_CURSOR
        with pytest.raises(TerminalBridgeError, match="cursor overflow"):
            bridge._accept_output(OUTPUT_HEADER.pack(1, 1, MAX_CURSOR) + b"x")
    finally:
        os.close(output_write)
    assert os.read(output_read, 65536) == b"abc"
    os.close(output_read)


def test_bridge_reattaches_in_process_from_exact_consumed_cursor() -> None:
    asyncio.run(_reconnect_round_trip(expire=False))


def test_bridge_stops_when_exact_replay_cursor_has_expired() -> None:
    with pytest.raises(TerminalSurfaceRejected, match="cursor_expired"):
        asyncio.run(_reconnect_round_trip(expire=True))


async def _reconnect_round_trip(*, expire: bool) -> None:
    input_read, input_write = os.pipe()
    output_read, output_write = os.pipe()
    error_read, error_write = os.pipe()
    ReconnectingTerminalEndpoint.attaches = []
    ReconnectingTerminalEndpoint.attempts = 0
    ReconnectingTerminalEndpoint.replayed = asyncio.Event()
    ReconnectingTerminalEndpoint.expire_replay = expire
    application = tornado.web.Application(
        [(r"/v1/surfaces/surf_reconnect/terminal", ReconnectingTerminalEndpoint)]
    )
    sockets = tornado.netutil.bind_sockets(0, address="127.0.0.1")
    server = tornado.httpserver.HTTPServer(application)
    server.add_sockets(sockets)
    port = sockets[0].getsockname()[1]
    bridge = TerminalBridge(
        f"http://127.0.0.1:{port}",
        "surf_reconnect",
        stdin_fd=input_read,
        stdout_fd=output_write,
        stderr_fd=error_write,
        geometry=lambda: os.terminal_size((80, 24)),
        install_signal_handler=False,
        reconnect_min_delay=0.01,
        reconnect_max_delay=0.02,
    )
    task = asyncio.create_task(bridge.run())
    caught: BaseException | None = None
    try:
        if expire:
            try:
                await asyncio.wait_for(task, timeout=2)
            except BaseException as exc:
                caught = exc
        else:
            await asyncio.wait_for(ReconnectingTerminalEndpoint.replayed.wait(), timeout=2)
            os.close(input_write)
            input_write = -1
            await asyncio.wait_for(task, timeout=2)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        server.stop()
        await server.close_all_connections()
        for fd in (input_read, input_write, output_write, error_write):
            if fd >= 0:
                os.close(fd)
    output = os.read(output_read, 65536)
    diagnostics = os.read(error_read, 65536)
    os.close(output_read)
    os.close(error_read)
    assert output == (b"abc" if expire else b"abcdef")
    assert [item["cursor"] for item in ReconnectingTerminalEndpoint.attaches] == ["0", "3"]
    assert (
        ReconnectingTerminalEndpoint.attaches[0]["attachment_id"]
        != ReconnectingTerminalEndpoint.attaches[1]["attachment_id"]
    )
    assert b"retrying from byte 3" in diagnostics
    if not expire:
        assert b"transport reconnected" in diagnostics
    if caught is not None:
        raise caught
