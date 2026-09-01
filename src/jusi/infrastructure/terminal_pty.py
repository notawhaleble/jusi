from __future__ import annotations

import errno
import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
from pathlib import Path
from collections.abc import Mapping

from jusi.application.ports import TerminalBrokerError, TerminalLaunchSpec
from jusi.domain.models import ProcessDiagnostics


def _validate_geometry(rows: int, cols: int) -> None:
    if (
        not isinstance(rows, int)
        or isinstance(rows, bool)
        or not isinstance(cols, int)
        or isinstance(cols, bool)
        or not 1 <= rows <= 65535
        or not 1 <= cols <= 65535
    ):
        raise ValueError("terminal geometry must be integers between 1 and 65535")


def _set_geometry(fd: int, *, rows: int, cols: int) -> None:
    _validate_geometry(rows, cols)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _geometry(fd: int) -> tuple[int, int]:
    rows, cols, _, _ = struct.unpack("HHHH", fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8))
    return rows, cols


class PosixTerminalHandle:
    def __init__(self, process: subprocess.Popen[bytes], master_fd: int) -> None:
        self._process = process
        self._master_fd = master_fd
        self._closed = False

    @property
    def pid(self) -> int | None:
        return self._process.pid

    @property
    def running(self) -> bool:
        return not self._closed and self._process.poll() is None

    @property
    def diagnostics(self) -> ProcessDiagnostics:
        self._process.poll()
        code = self._process.returncode
        return ProcessDiagnostics(
            pid=self._process.pid,
            exit_code=code if code is not None and code >= 0 else None,
            signal=-code if code is not None and code < 0 else None,
        )

    def read(self, *, timeout: float, maximum: int = 65536) -> bytes:
        if timeout < 0 or maximum < 1:
            raise ValueError("timeout must be non-negative and maximum must be positive")
        if self._closed:
            return b""
        ready, _, _ = select.select([self._master_fd], [], [], timeout)
        if not ready:
            return b""
        try:
            return os.read(self._master_fd, maximum)
        except OSError as exc:
            if exc.errno == errno.EIO and self._process.poll() is not None:
                return b""
            raise self._error("Could not read target terminal", "channel_closed") from exc

    def write(self, data: bytes) -> None:
        if not isinstance(data, bytes):
            raise TypeError("terminal input must be bytes")
        if self._closed or self._process.poll() is not None:
            raise self._error("Target terminal is not running", "channel_closed")
        view = memoryview(data)
        try:
            while view:
                written = os.write(self._master_fd, view)
                view = view[written:]
        except OSError as exc:
            raise self._error("Could not write target terminal", "channel_closed") from exc

    def resize(self, *, rows: int, cols: int) -> None:
        if self._closed or self._process.poll() is not None:
            raise self._error("Target terminal is not running", "channel_closed")
        try:
            _set_geometry(self._master_fd, rows=rows, cols=cols)
            if _geometry(self._master_fd) != (rows, cols):
                raise OSError("target PTY did not acknowledge requested geometry")
        except OSError as exc:
            raise self._error("Could not resize target terminal", "internal_error") from exc

    def stop(self, *, timeout: float) -> str:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if self._closed:
            return "already_absent"
        result = "already_absent" if self._process.poll() is not None else "stopped"
        if self._process.poll() is None:
            try:
                os.killpg(self._process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self._process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self._process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    self._process.wait(timeout=timeout)
                except subprocess.TimeoutExpired as exc:
                    raise self._error("Target terminal cleanup timed out", "cleanup_incomplete") from exc
        self._closed = True
        os.close(self._master_fd)
        return result

    def _error(self, message: str, reason: str) -> TerminalBrokerError:
        return TerminalBrokerError(
            message,
            reason=reason,
            retryable=reason in {"channel_closed", "cleanup_incomplete"},
            diagnostics=self.diagnostics,
        )


class PosixTerminalBroker:
    def __init__(self, *, base_env: Mapping[str, str] | None = None) -> None:
        self._base_env = dict(base_env) if base_env is not None else dict(os.environ)

    def start(
        self,
        spec: TerminalLaunchSpec,
        *,
        rows: int,
        cols: int,
    ) -> PosixTerminalHandle:
        _validate_geometry(rows, cols)
        if not spec.argv or any(not isinstance(value, str) or not value for value in spec.argv):
            raise ValueError("terminal argv must contain non-empty strings")
        if any(not isinstance(key, str) or not key or not isinstance(value, str) for key, value in spec.env.items()):
            raise ValueError("terminal environment must contain string names and values")
        master_fd, slave_fd = pty.openpty()
        try:
            # This is the geometry gate: the slave has its authoritative size
            # before the child exists and therefore before its first draw.
            _set_geometry(slave_fd, rows=rows, cols=cols)
            if _geometry(slave_fd) != (rows, cols):
                raise OSError("target PTY did not acknowledge initial geometry")
            environment = {**self._base_env, **spec.env}
            launcher = Path(__file__).with_name("terminal_child.py")
            process = subprocess.Popen(
                (sys.executable, os.fspath(launcher), "--", *spec.argv),
                cwd=spec.cwd,
                env=environment,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
                close_fds=True,
            )
        except (OSError, ValueError) as exc:
            os.close(master_fd)
            raise TerminalBrokerError(
                f"Could not start target terminal: {exc}",
                reason="spawn_failed",
                retryable=True,
            ) from exc
        finally:
            os.close(slave_fd)
        return PosixTerminalHandle(process, master_fd)
