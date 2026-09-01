from __future__ import annotations

import queue
import time

import pytest

from jusi.application.ports import TerminalLaunchSpec, TerminalSurfaceRequest
from jusi.application.terminal_surfaces import (
    TerminalChunk,
    TerminalSurfaceError,
    TerminalSurfaceManager,
)
from jusi.domain.models import SurfaceResource


class FakeHandle:
    pid = 4321

    def __init__(self) -> None:
        self.running = True
        self.output: queue.Queue[bytes] = queue.Queue()
        self.input: list[bytes] = []
        self.sizes: list[tuple[int, int]] = []
        self.stop_count = 0

    def read(self, *, timeout: float, maximum: int = 65536) -> bytes:
        try:
            return self.output.get(timeout=timeout)[:maximum]
        except queue.Empty:
            return b""

    def write(self, data: bytes) -> None:
        self.input.append(data)

    def resize(self, *, rows: int, cols: int) -> None:
        self.sizes.append((rows, cols))

    def stop(self, *, timeout: float) -> str:
        self.stop_count += 1
        self.running = False
        return "stopped" if self.stop_count == 1 else "already_absent"


class FakeBroker:
    def __init__(self) -> None:
        self.handle = FakeHandle()
        self.starts: list[tuple[TerminalLaunchSpec, int, int]] = []

    def start(self, spec: TerminalLaunchSpec, *, rows: int, cols: int) -> FakeHandle:
        self.starts.append((spec, rows, cols))
        return self.handle


def resource() -> SurfaceResource:
    return SurfaceResource(
        surface_id="srf_test",
        client_id="cli_test",
        runtime_id="run_test",
        kind="terminal",
        capabilities=("input", "resize", "signal"),
        endpoint="/v1/surfaces/srf_test/terminal",
    )


def request() -> TerminalSurfaceRequest:
    return TerminalSurfaceRequest(
        request_id="terminal_main",
        argv=("vd", "--play", "/private/query.vd"),
        cwd="/target/work",
        environment_overrides={"TERM": "xterm-256color"},
        capabilities=("input", "resize", "signal"),
    )


def wait_chunk(attachment, timeout: float = 1.0) -> TerminalChunk:  # type: ignore[no-untyped-def]
    value = attachment.chunks.get(timeout=timeout)
    assert isinstance(value, TerminalChunk)
    return value


def test_first_attachment_gates_launch_and_streams_without_operation_lock() -> None:
    broker = FakeBroker()
    manager = TerminalSurfaceManager(broker)
    manager.prepare(resource(), request())

    attachment, window = manager.attach(
        "srf_test", attachment_id="att_one", rows=21, cols=79, after_cursor=0,
    )
    assert broker.starts == [(TerminalLaunchSpec(request().argv, request().cwd, request().environment_overrides), 21, 79)]
    assert window == {"earliest_cursor": 0, "latest_cursor": 0}
    manager.write("srf_test", "att_one", b"select 1\n")
    manager.resize("srf_test", "att_one", rows=30, cols=100)
    assert broker.handle.input == [b"select 1\n"]
    assert broker.handle.sizes == [(30, 100)]

    broker.handle.output.put(b"\x1b[31mred\x1b[0m")
    chunk = wait_chunk(attachment)
    assert chunk.cursor == 0
    assert chunk.data == b"\x1b[31mred\x1b[0m"
    assert manager.close("srf_test", timeout=1)["result"] == "stopped"
    assert manager.close("srf_test", timeout=1)["result"] == "already_absent"


def test_detach_keeps_process_and_exact_replay_while_cursor_is_retained() -> None:
    broker = FakeBroker()
    manager = TerminalSurfaceManager(broker, replay_limit=64)
    manager.prepare(resource(), request())
    first, _ = manager.attach(
        "srf_test", attachment_id="att_one", rows=20, cols=80, after_cursor=0,
    )
    broker.handle.output.put(b"first")
    assert wait_chunk(first).data == b"first"
    manager.detach("srf_test", "att_one")
    broker.handle.output.put(b"second")
    deadline = time.monotonic() + 1
    while manager._surfaces["srf_test"].next_cursor < 11 and time.monotonic() < deadline:  # noqa: SLF001
        time.sleep(0.01)

    second, window = manager.attach(
        "srf_test", attachment_id="att_two", rows=24, cols=90, after_cursor=5,
    )
    replay = wait_chunk(second)
    assert replay == TerminalChunk(5, b"second")
    assert window["latest_cursor"] == 11
    assert len(broker.starts) == 1
    assert broker.handle.running is True
    manager.close("srf_test", timeout=1)


def test_busy_writer_and_expired_cursor_are_explicit() -> None:
    broker = FakeBroker()
    manager = TerminalSurfaceManager(broker, replay_limit=4)
    manager.prepare(resource(), request())
    first, _ = manager.attach(
        "srf_test", attachment_id="att_one", rows=20, cols=80, after_cursor=0,
    )
    with pytest.raises(TerminalSurfaceError) as busy:
        manager.attach("srf_test", attachment_id="att_two", rows=20, cols=80, after_cursor=0)
    assert busy.value.reason == "busy"
    broker.handle.output.put(b"abcdef")
    assert wait_chunk(first).data == b"abcdef"
    manager.detach("srf_test", "att_one")
    with pytest.raises(TerminalSurfaceError) as expired:
        manager.attach("srf_test", attachment_id="att_two", rows=20, cols=80, after_cursor=0)
    assert expired.value.reason == "cursor_expired"
    assert expired.value.details == {"earliest_cursor": 2, "latest_cursor": 6}
    manager.close("srf_test", timeout=1)


def test_slow_attachment_is_fenced_without_blocking_surface_drain_or_cleanup() -> None:
    broker = FakeBroker()
    manager = TerminalSurfaceManager(broker, attachment_queue_limit=1)
    manager.prepare(resource(), request())
    manager.attach("srf_test", attachment_id="att_slow", rows=20, cols=80, after_cursor=0)
    broker.handle.output.put(b"first")
    broker.handle.output.put(b"second")
    deadline = time.monotonic() + 1
    while manager._surfaces["srf_test"].attachment is not None and time.monotonic() < deadline:  # noqa: SLF001
        time.sleep(0.01)
    assert manager._surfaces["srf_test"].attachment is None  # noqa: SLF001
    replacement, window = manager.attach(
        "srf_test", attachment_id="att_replacement", rows=20, cols=80, after_cursor=5,
    )
    assert wait_chunk(replacement).data == b"second"
    assert window["latest_cursor"] == 11
    assert manager.close("srf_test", timeout=1)["result"] == "stopped"
