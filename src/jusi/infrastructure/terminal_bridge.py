from __future__ import annotations

import asyncio
import json
import os
import signal
import struct
import sys
import termios
import tty
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from tornado.httpclient import HTTPClientError, HTTPRequest
from tornado.websocket import WebSocketClientConnection, WebSocketError, websocket_connect

from jusi.protocol import ProtocolValidationError, validate_terminal_stream_control


SUBPROTOCOL = "jusi.terminal.v1"
OUTPUT_HEADER = struct.Struct(">BBQ")
OUTPUT_VERSION = 1
OUTPUT_TYPE = 1
MAX_CURSOR = (1 << 64) - 1


class TerminalBridgeError(RuntimeError):
    pass


class TerminalBridgeReconnect(TerminalBridgeError):
    pass


class TerminalSurfaceRejected(TerminalBridgeError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"terminal surface rejected transport: {reason}")
        self.reason = reason


def terminal_websocket_url(base_url: str, surface_id: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.netloc:
        raise ValueError("service URL must be an absolute HTTP or WebSocket URL")
    if parsed.query or parsed.fragment:
        raise ValueError("service URL must not contain a query or fragment")
    if not surface_id or "/" in surface_id:
        raise ValueError("surface id must be a non-empty path segment")
    scheme = {"http": "ws", "https": "wss"}.get(parsed.scheme, parsed.scheme)
    prefix = parsed.path.rstrip("/")
    path = f"{prefix}/v1/surfaces/{quote(surface_id, safe='')}/terminal"
    return urlunsplit((scheme, parsed.netloc, path, "", ""))


class TerminalBridge:
    """Relay one local terminal file descriptor pair to one target surface."""

    def __init__(
        self,
        base_url: str,
        surface_id: str,
        *,
        editor_id: str | None = None,
        stdin_fd: int = 0,
        stdout_fd: int = 1,
        stderr_fd: int = 2,
        geometry: Callable[[], os.terminal_size] | None = None,
        install_signal_handler: bool = True,
        manage_input_mode: bool = True,
        reconnect_min_delay: float = 0.05,
        reconnect_max_delay: float = 2.0,
    ) -> None:
        if reconnect_min_delay <= 0 or reconnect_max_delay < reconnect_min_delay:
            raise ValueError("terminal reconnect delays must be positive and ordered")
        self.url = terminal_websocket_url(base_url, surface_id)
        self.surface_id = surface_id
        self.editor_id = editor_id
        self.attachment_id = ""
        self.stdin_fd = stdin_fd
        self.stdout_fd = stdout_fd
        self.stderr_fd = stderr_fd
        self._geometry = geometry or (lambda: os.get_terminal_size(self.stdin_fd))
        self._install_signal_handler = install_signal_handler
        self._manage_input_mode = manage_input_mode
        self._connection: WebSocketClientConnection | None = None
        self._write_lock: asyncio.Lock | None = None
        self._last_cursor = 0
        self._resize_task: asyncio.Task[None] | None = None
        self._pending_resizes: dict[str, tuple[int, int]] = {}
        self._reconnect_min_delay = reconnect_min_delay
        self._reconnect_max_delay = reconnect_max_delay
        self._attachment_succeeded = False

    async def run(self) -> None:
        self._write_lock = asyncio.Lock()
        loop = asyncio.get_running_loop()
        signal_installed = False
        saved_input_mode = self._enter_raw_input_mode()
        try:
            if self._install_signal_handler and hasattr(signal, "SIGWINCH"):
                try:
                    loop.add_signal_handler(signal.SIGWINCH, self._schedule_resize)
                    signal_installed = True
                except (NotImplementedError, RuntimeError):
                    signal_installed = False

            delay = self._reconnect_min_delay
            disconnected = False
            try:
                while True:
                    self._attachment_succeeded = False
                    try:
                        keep_running = await self._run_connection(
                            self._read_geometry(), announce_reconnected=disconnected,
                        )
                        if not keep_running:
                            return
                        disconnected = True
                    except TerminalSurfaceRejected as exc:
                        if exc.reason not in {"busy", "channel_closed"}:
                            raise
                        disconnected = True
                    except TerminalBridgeReconnect:
                        disconnected = True
                    except HTTPClientError as exc:
                        if exc.code and 400 <= exc.code < 500 and exc.code not in {408, 409, 429}:
                            raise TerminalBridgeError(f"terminal endpoint rejected connection: HTTP {exc.code}") from exc
                        disconnected = True
                    except (OSError, WebSocketError):
                        disconnected = True
                    if disconnected:
                        if self._attachment_succeeded:
                            delay = self._reconnect_min_delay
                        self._write_diagnostic(
                            f"[Jusi terminal transport disconnected; retrying from byte {self._last_cursor}]"
                        )
                        await asyncio.sleep(delay)
                        delay = min(self._reconnect_max_delay, max(delay * 2, self._reconnect_min_delay))
            finally:
                if signal_installed:
                    loop.remove_signal_handler(signal.SIGWINCH)
                await self._close_connection()
        finally:
            self._restore_input_mode(saved_input_mode)

    def _enter_raw_input_mode(self) -> list[Any] | None:
        if not self._manage_input_mode:
            return None
        try:
            saved = termios.tcgetattr(self.stdin_fd)
            tty.setraw(self.stdin_fd, termios.TCSANOW)
        except (OSError, termios.error) as exc:
            raise TerminalBridgeError("bridge stdin is not a usable terminal") from exc
        return saved

    def _restore_input_mode(self, saved: list[Any] | None) -> None:
        if saved is None:
            return
        try:
            termios.tcsetattr(self.stdin_fd, termios.TCSANOW, saved)
        except (OSError, termios.error) as exc:
            self._write_diagnostic(f"could not restore bridge terminal mode: {exc}")

    async def _run_connection(
        self, size: os.terminal_size, *, announce_reconnected: bool,
    ) -> bool:
        self.attachment_id = f"att_{uuid.uuid4().hex}"
        self._pending_resizes = {}
        request = HTTPRequest(self.url, connect_timeout=10.0, request_timeout=0.0)
        connection = await websocket_connect(request, subprotocols=[SUBPROTOCOL])
        if connection.selected_subprotocol != SUBPROTOCOL:
            connection.close()
            raise TerminalBridgeError("terminal endpoint did not select jusi.terminal.v1")
        self._connection = connection
        try:
            await self._send_control({
                "protocol_version": 1,
                "kind": "attach",
                "surface_id": self.surface_id,
                "attachment_id": self.attachment_id,
                "rows": size.lines,
                "columns": size.columns,
                "cursor": str(self._last_cursor),
                **({"editor_id": self.editor_id} if self.editor_id else {}),
            })
            await self._wait_attached(size)
            self._attachment_succeeded = True
            if announce_reconnected:
                self._write_diagnostic("[Jusi terminal transport reconnected]")
        except BaseException:
            await self._close_connection()
            raise
        input_task = asyncio.create_task(self._relay_input())
        output_task = asyncio.create_task(self._relay_output())
        try:
            done, pending = await asyncio.wait(
                (input_task, output_task), return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
            return output_task in done
        finally:
            await self._close_connection()

    async def _close_connection(self) -> None:
        if self._resize_task is not None:
            self._resize_task.cancel()
            await asyncio.gather(self._resize_task, return_exceptions=True)
            self._resize_task = None
        self._pending_resizes = {}
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    async def send_resize(self) -> None:
        size = self._read_geometry()
        request_id = f"req_{uuid.uuid4().hex}"
        self._pending_resizes[request_id] = (size.lines, size.columns)
        await self._send_control(
            {
                "protocol_version": 1,
                "kind": "resize",
                "surface_id": self.surface_id,
                "attachment_id": self.attachment_id,
                "resize_id": request_id,
                "rows": size.lines,
                "columns": size.columns,
            }
        )

    def _schedule_resize(self) -> None:
        if self._connection is None:
            return
        if self._resize_task is not None and not self._resize_task.done():
            return
        self._resize_task = asyncio.create_task(self.send_resize())

    def _read_geometry(self) -> os.terminal_size:
        try:
            size = self._geometry()
        except OSError as exc:
            raise TerminalBridgeError("bridge stdin is not a terminal") from exc
        if not 1 <= size.lines <= 65535 or not 1 <= size.columns <= 65535:
            raise TerminalBridgeError("terminal geometry must be between 1 and 65535")
        return size

    async def _send_control(self, value: dict[str, Any]) -> None:
        connection = self._connection
        if connection is None:
            raise TerminalBridgeError("terminal WebSocket is not connected")
        message = json.dumps(value, separators=(",", ":"), sort_keys=True)
        if self._write_lock is None:
            raise TerminalBridgeError("terminal WebSocket is not connected")
        async with self._write_lock:
            await connection.write_message(message)

    async def _relay_input(self) -> None:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        def readable() -> None:
            try:
                value = os.read(self.stdin_fd, 65536)
            except OSError as exc:
                loop.remove_reader(self.stdin_fd)
                queue.put_nowait(None)
                self._write_diagnostic(f"terminal input failed: {exc}")
                return
            if not value:
                loop.remove_reader(self.stdin_fd)
                queue.put_nowait(None)
                return
            queue.put_nowait(value)

        loop.add_reader(self.stdin_fd, readable)
        try:
            while True:
                value = await queue.get()
                if value is None:
                    return
                connection = self._connection
                if connection is None:
                    return
                assert self._write_lock is not None
                async with self._write_lock:
                    await connection.write_message(value, binary=True)
        finally:
            loop.remove_reader(self.stdin_fd)

    async def _relay_output(self) -> None:
        connection = self._connection
        assert connection is not None
        while True:
            message = await connection.read_message()
            if message is None:
                raise TerminalBridgeReconnect("terminal WebSocket closed while the surface was attached")
            if isinstance(message, bytes):
                self._accept_output(message)
                continue
            await self._accept_control(message)

    async def _wait_attached(self, expected: os.terminal_size) -> None:
        connection = self._connection
        assert connection is not None
        message = await connection.read_message()
        if message is None:
            raise TerminalBridgeError("terminal endpoint closed before attachment")
        if isinstance(message, bytes):
            raise TerminalBridgeError("terminal endpoint sent output before attachment")
        kind = await self._accept_control(message)
        if kind != "attached":
            raise TerminalBridgeError(f"terminal endpoint sent {kind} before attachment")
        value = json.loads(message)
        if value.get("rows") != expected.lines or value.get("columns") != expected.columns:
            raise TerminalBridgeError("terminal attachment acknowledged the wrong geometry")
        if value.get("cursor") != str(self._last_cursor):
            raise TerminalBridgeError("terminal attachment acknowledged the wrong cursor")

    def _accept_output(self, message: bytes) -> None:
        if len(message) < OUTPUT_HEADER.size:
            raise TerminalBridgeError("terminal output frame is shorter than its header")
        version, frame_type, cursor = OUTPUT_HEADER.unpack_from(message)
        if version != OUTPUT_VERSION or frame_type != OUTPUT_TYPE:
            raise TerminalBridgeError("terminal output frame has an unsupported version or type")
        payload = message[OUTPUT_HEADER.size :]
        if cursor != self._last_cursor:
            raise TerminalBridgeError(
                f"terminal output cursor gap: expected {self._last_cursor}, received {cursor}"
            )
        if len(payload) > MAX_CURSOR - cursor:
            raise TerminalBridgeError("terminal output cursor overflow")
        self._write_all(self.stdout_fd, payload)
        self._last_cursor = cursor + len(payload)

    async def _accept_control(self, message: str) -> str:
        try:
            value = validate_terminal_stream_control(json.loads(message))
        except (json.JSONDecodeError, ProtocolValidationError) as exc:
            raise TerminalBridgeError(f"invalid terminal control frame: {exc}") from exc
        if value.get("surface_id") != self.surface_id:
            raise TerminalBridgeError("terminal control frame has the wrong surface id")
        if value.get("attachment_id") != self.attachment_id:
            raise TerminalBridgeError("terminal control frame has the wrong attachment id")
        kind = value["kind"]
        if kind == "attached":
            return kind
        if kind == "resized":
            request_id = value.get("resize_id")
            expected = (
                self._pending_resizes.pop(request_id, None)
                if isinstance(request_id, str)
                else None
            )
            if expected is None:
                raise TerminalBridgeError("terminal resize acknowledged an unknown request")
            if value.get("rows") != expected[0] or value.get("columns") != expected[1]:
                raise TerminalBridgeError("terminal resize acknowledged the wrong geometry")
            return kind
        if kind == "failure":
            reason = value.get("reason", "failure")
            raise TerminalSurfaceRejected(reason)
        raise TerminalBridgeError(f"unknown terminal control frame: {kind}")

    def _write_diagnostic(self, message: str) -> None:
        self._write_all(self.stderr_fd, (message + "\n").encode("utf-8", errors="replace"))

    @staticmethod
    def _write_all(fd: int, value: bytes) -> None:
        view = memoryview(value)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise TerminalBridgeError("terminal output closed")
            view = view[written:]


async def run_terminal_bridge(base_url: str, surface_id: str, editor_id: str | None = None) -> None:
    await TerminalBridge(base_url, surface_id, editor_id=editor_id).run()


def terminal_bridge_main(base_url: str, surface_id: str, editor_id: str | None = None) -> int:
    try:
        asyncio.run(run_terminal_bridge(base_url, surface_id, editor_id))
    except (OSError, ValueError, HTTPClientError, WebSocketError, TerminalBridgeError) as exc:
        print(f"jusi terminal bridge: {exc}", file=sys.stderr)
        return 1
    return 0
