from __future__ import annotations

import os
import queue
import signal
import subprocess
import sys
import threading
import uuid
from typing import Any, BinaryIO, Sequence

from jusi.application.ports import (
    PluginWorkerError,
    PluginWorkerHandle,
    PluginWorkerOperationResult,
    PluginWorkerSpec,
    TerminalSurfaceRequest,
)
from jusi.domain.models import ProcessDiagnostics
from jusi.infrastructure.plugin_worker_channel import (
    DEFAULT_FRAME_LIMIT,
    WorkerChannelClosed,
    WorkerFrameError,
    read_frame,
    write_frame,
)
from jusi.protocol import ProtocolValidationError, validate_plugin_worker_message


DEFAULT_STDERR_LIMIT = 16 * 1024


class _BoundedStderrReader(threading.Thread):
    def __init__(self, stream: BinaryIO, limit: int) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._limit = limit
        self._tail = bytearray()
        self.total = 0

    def run(self) -> None:
        while True:
            chunk = self._stream.read(8192)
            if not chunk:
                return
            self.total += len(chunk)
            self._tail.extend(chunk)
            if len(self._tail) > self._limit:
                del self._tail[: len(self._tail) - self._limit]

    @property
    def excerpt(self) -> str:
        return bytes(self._tail).decode("utf-8", errors="replace")

    @property
    def truncated(self) -> bool:
        return self.total > self._limit


class _MessageReader(threading.Thread):
    def __init__(self, stream: BinaryIO, messages: queue.Queue[object], limit: int) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._messages = messages
        self._limit = limit

    def run(self) -> None:
        while True:
            try:
                self._messages.put(read_frame(self._stream, limit=self._limit))
            except (WorkerChannelClosed, WorkerFrameError, OSError) as exc:
                self._messages.put(exc)
                return


class FreshProcessPluginWorker:
    def __init__(
        self,
        process: subprocess.Popen[bytes],
        spec: PluginWorkerSpec,
        stderr_reader: _BoundedStderrReader,
        message_reader: _MessageReader,
        messages: queue.Queue[object],
        frame_limit: int,
    ) -> None:
        self._process = process
        self._spec = spec
        self._stderr_reader = stderr_reader
        self._message_reader = message_reader
        self._messages = messages
        self._frame_limit = frame_limit
        self._lock = threading.Lock()
        self._fenced = False

    @property
    def pid(self) -> int | None:
        return self._process.pid

    def request(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        trace_id: str,
        timeout: float,
    ) -> PluginWorkerOperationResult:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        request_id = f"wreq_{uuid.uuid4().hex}"
        request = validate_plugin_worker_message({
            "protocol_version": 1,
            "kind": "worker.request",
            "plugin_worker_id": self._spec.plugin_worker_id,
            "request_id": request_id,
            "trace_id": trace_id,
            "operation": operation,
            "payload": payload,
        })
        with self._lock:
            if self._fenced or self._process.poll() is not None:
                raise self._error("Plugin worker is not running", reason="channel_closed", retryable=False)
            self._send(request)
            response = self._receive(timeout, operation=operation)
            self._require_correlation(response, request)
            if response["kind"] == "worker.failure":
                failure = response["failure"]
                self._fenced = True
                self._terminate()
                raise self._error(
                    failure["message"],
                    reason=failure["reason"],
                    retryable=failure["retryable"],
                    details=failure.get("details"),
                )
            if response["kind"] != "worker.result":
                self._fenced = True
                self._terminate()
                raise self._error("Plugin worker returned an unexpected message", reason="protocol_violation", retryable=False)
            return PluginWorkerOperationResult(
                result=dict(response["result"]),
                core_requests=tuple(
                    TerminalSurfaceRequest(
                        request_id=item["request_id"],
                        argv=tuple(item["argv"]),
                        cwd=item["cwd"],
                        environment_overrides=dict(item["environment_overrides"]),
                        capabilities=tuple(item["capabilities"]),
                    )
                    for item in response["core_requests"]
                ),
            )

    def stop(self, *, trace_id: str, timeout: float) -> str:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        with self._lock:
            if self._process.poll() is not None:
                self._fenced = True
                self._finish_readers()
                return "already_absent"
            self._fenced = True
            request = {
                "protocol_version": 1,
                "kind": "worker.shutdown",
                "plugin_worker_id": self._spec.plugin_worker_id,
                "request_id": f"wreq_{uuid.uuid4().hex}",
                "trace_id": trace_id,
            }
            try:
                self._send(request)
                response = self._receive(timeout, operation="cleanup", terminate_on_failure=False)
                self._require_correlation(response, request)
                if response["kind"] != "worker.stopped":
                    raise self._error("Plugin worker did not acknowledge shutdown", reason="protocol_violation", retryable=False)
                self._process.wait(timeout=timeout)
                if self._process.returncode != 0:
                    raise self._process_error("Plugin worker exited during shutdown")
                self._kill_remaining_group()
            except (subprocess.TimeoutExpired, PluginWorkerError) as exc:
                self._terminate()
                if isinstance(exc, PluginWorkerError):
                    raise
                raise self._error("Plugin worker shutdown timed out", reason="timeout", retryable=True) from exc
            finally:
                self._finish_readers()
            return "stopped"

    def _send(self, message: dict[str, Any]) -> None:
        assert self._process.stdin is not None
        try:
            write_frame(self._process.stdin, message, limit=self._frame_limit)
        except (OSError, WorkerFrameError) as exc:
            self._fenced = True
            self._terminate()
            raise self._error("Could not send plugin worker control message", reason="channel_closed", retryable=False) from exc

    def _receive(self, timeout: float, *, operation: str, terminate_on_failure: bool = True) -> dict[str, Any]:
        try:
            value = self._messages.get(timeout=timeout)
        except queue.Empty as exc:
            if terminate_on_failure:
                self._fenced = True
                self._terminate()
            raise self._error(
                f"Plugin worker {operation} timed out",
                reason="timeout",
                retryable=False,
                details={"side_effects_may_have_occurred": operation != "cleanup"},
            ) from exc
        if isinstance(value, WorkerFrameError):
            if terminate_on_failure:
                self._fenced = True
                self._terminate()
            raise self._error("Plugin worker returned an invalid frame", reason="protocol_violation", retryable=False) from value
        if isinstance(value, (WorkerChannelClosed, OSError)):
            try:
                self._process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass
            if terminate_on_failure:
                self._fenced = True
                if self._process.poll() is None:
                    self._terminate()
            raise self._process_error("Plugin worker control channel closed") from value
        try:
            return validate_plugin_worker_message(value)
        except ProtocolValidationError as exc:
            if terminate_on_failure:
                self._fenced = True
                self._terminate()
            raise self._error("Plugin worker returned an invalid envelope", reason="protocol_violation", retryable=False) from exc

    def _require_correlation(self, response: dict[str, Any], request: dict[str, Any]) -> None:
        for field in ("plugin_worker_id", "request_id", "trace_id"):
            if response.get(field) != request[field]:
                self._fenced = True
                self._terminate()
                raise self._error(
                    f"Plugin worker response {field} mismatch",
                    reason="protocol_violation",
                    retryable=False,
                )
        if request["kind"] == "worker.request" and response.get("operation") != request["operation"]:
            self._fenced = True
            self._terminate()
            raise self._error("Plugin worker response operation mismatch", reason="protocol_violation", retryable=False)

    def _process_error(self, message: str) -> PluginWorkerError:
        self._process.poll()
        if self._process.returncode is not None:
            self._finish_readers()
        code = self._process.returncode
        if code is not None and code < 0:
            return self._error(message, reason="process_signalled", retryable=False)
        if code is not None:
            return self._error(message, reason="process_exited", retryable=False)
        return self._error(message, reason="channel_closed", retryable=False)

    def _error(
        self,
        message: str,
        *,
        reason: str,
        retryable: bool,
        details: dict[str, Any] | None = None,
    ) -> PluginWorkerError:
        return PluginWorkerError(
            message,
            reason=reason,
            retryable=retryable,
            diagnostics=self._diagnostics(),
            details=details,
        )

    def _diagnostics(self) -> ProcessDiagnostics:
        self._process.poll()
        code = self._process.returncode
        return ProcessDiagnostics(
            pid=self._process.pid,
            exit_code=code if code is not None and code >= 0 else None,
            signal=-code if code is not None and code < 0 else None,
            stderr_excerpt=self._stderr_reader.excerpt,
            stderr_truncated=self._stderr_reader.truncated,
        )

    def _terminate(self) -> None:
        if self._process.poll() is None:
            try:
                if os.name != "nt":
                    os.killpg(self._process.pid, signal.SIGTERM)
                else:
                    self._process.terminate()
                self._process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if self._process.poll() is None:
            try:
                if os.name != "nt":
                    os.killpg(self._process.pid, signal.SIGKILL)
                else:
                    self._process.kill()
                self._process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if os.name != "nt":
            self._kill_remaining_group()
        self._finish_readers()

    def _kill_remaining_group(self) -> None:
        if os.name == "nt":
            return
        try:
            os.killpg(self._process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _finish_readers(self) -> None:
        self._stderr_reader.join(timeout=1)
        self._message_reader.join(timeout=1)


class FreshProcessPluginWorkerFactory:
    def __init__(
        self,
        *,
        python_executable: str | None = None,
        search_paths: Sequence[str | os.PathLike[str]] | None = None,
        stderr_limit: int = DEFAULT_STDERR_LIMIT,
        frame_limit: int = DEFAULT_FRAME_LIMIT,
    ) -> None:
        self._python_executable = python_executable or sys.executable
        self._search_paths = None if search_paths is None else tuple(os.fspath(path) for path in search_paths)
        self._stderr_limit = stderr_limit
        self._frame_limit = frame_limit

    def start(self, spec: PluginWorkerSpec, *, timeout: float) -> PluginWorkerHandle:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        command = [
            self._python_executable,
            "-m", "jusi.infrastructure.plugin_worker_child",
            "--entry-point", spec.entry_point,
            "--plugin-worker-id", spec.plugin_worker_id,
            "--runtime-id", spec.runtime_id,
            "--plugin-id", spec.plugin_id,
            "--family-id", spec.family_id,
            "--client-id", spec.client_id,
            "--execution-id", spec.execution_id,
            "--frame-limit", str(self._frame_limit),
        ]
        if self._search_paths is not None:
            for search_path in self._search_paths:
                command.extend(("--search-path", search_path))
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=os.name != "nt",
            )
        except OSError as exc:
            raise PluginWorkerError(
                f"Could not start plugin worker: {exc}",
                reason="spawn_failed",
                retryable=True,
            ) from exc
        assert process.stdout is not None and process.stderr is not None
        messages: queue.Queue[object] = queue.Queue()
        stderr_reader = _BoundedStderrReader(process.stderr, self._stderr_limit)
        message_reader = _MessageReader(process.stdout, messages, self._frame_limit)
        stderr_reader.start()
        message_reader.start()
        handle = FreshProcessPluginWorker(
            process, spec, stderr_reader, message_reader, messages, self._frame_limit,
        )
        try:
            ready = handle._receive(timeout, operation="readiness")
            expected = {
                "plugin_worker_id": spec.plugin_worker_id,
                "runtime_id": spec.runtime_id,
                "plugin_id": spec.plugin_id,
                "family_id": spec.family_id,
                "client_id": spec.client_id,
                "execution_id": spec.execution_id,
                "pid": process.pid,
            }
            if ready.get("kind") != "worker.ready" or any(ready.get(key) != value for key, value in expected.items()):
                raise PluginWorkerError(
                    "Plugin worker readiness identity mismatch",
                    reason="protocol_violation",
                    retryable=False,
                )
        except PluginWorkerError:
            handle._fenced = True
            handle._terminate()
            raise
        return handle
