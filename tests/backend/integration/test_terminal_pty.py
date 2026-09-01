from __future__ import annotations

import os
import signal
import sys
import time

import pytest

from jusi.application.ports import TerminalLaunchSpec
from jusi.infrastructure.terminal_pty import PosixTerminalBroker


pytestmark = pytest.mark.skipif(os.name != "posix", reason="PTY broker requires POSIX")


def read_until(handle, marker: bytes, *, timeout: float = 2.0) -> bytes:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    received = bytearray()
    while marker not in received and time.monotonic() < deadline:
        received.extend(handle.read(timeout=min(0.1, max(0.0, deadline - time.monotonic()))))
    assert marker in received, bytes(received)
    return bytes(received)


def test_initial_geometry_exists_before_first_child_draw_and_resize_converges() -> None:
    program = """
import os
tty = os.open('/dev/tty', os.O_RDWR)
os.close(tty)
print('controlling-tty=yes', flush=True)
print('initial=%dx%d' % os.get_terminal_size(0), flush=True)
input()
print('resized=%dx%d' % os.get_terminal_size(0), flush=True)
"""
    handle = PosixTerminalBroker().start(
        TerminalLaunchSpec((sys.executable, "-c", program)),
        rows=19,
        cols=73,
    )
    try:
        initial = read_until(handle, b"initial=73x19")
        assert b"controlling-tty=yes" in initial
        handle.resize(rows=31, cols=101)
        handle.resize(rows=31, cols=101)
        handle.write(b"\n")
        assert b"resized=101x31" in read_until(handle, b"resized=101x31")
    finally:
        assert handle.stop(timeout=2) in {"stopped", "already_absent"}
    assert handle.stop(timeout=2) == "already_absent"


def test_terminal_bytes_are_not_decoded_or_rewritten() -> None:
    payload = b"plain \\x1b[31mred\\x1b[0m \\xff"
    program = "import os; os.write(1, " + repr(payload) + ")"
    handle = PosixTerminalBroker().start(
        TerminalLaunchSpec((sys.executable, "-c", program)),
        rows=10,
        cols=40,
    )
    try:
        assert payload in read_until(handle, payload)
    finally:
        handle.stop(timeout=2)


def test_cleanup_escalates_and_reaps_term_resistant_process_group() -> None:
    program = """
import signal
import time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
print('ready', flush=True)
while True:
    time.sleep(1)
"""
    handle = PosixTerminalBroker().start(
        TerminalLaunchSpec((sys.executable, "-c", program)),
        rows=10,
        cols=40,
    )
    read_until(handle, b"ready")
    pid = handle.pid
    assert handle.stop(timeout=0.1) == "stopped"
    assert handle.stop(timeout=0.1) == "already_absent"
    with pytest.raises(ProcessLookupError):
        os.kill(pid, signal.SIGCONT)
