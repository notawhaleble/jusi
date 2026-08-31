from __future__ import annotations

import os
import queue
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

from jupyter_client import KernelManager

from jusi.application.ports import (
    KernelAdapterError,
    KernelExecutionResult,
    KernelFactory,
    KernelOutput,
)
from jusi.domain.models import ProcessDiagnostics


MAX_STDERR_BYTES = 16 * 1024


class ManagedJupyterKernelFactory(KernelFactory):
    def start(self, kernel_name: str, *, timeout: float):
        stderr_file = tempfile.NamedTemporaryFile(prefix="jusi-kernel-", suffix=".stderr", delete=False)
        stderr_path = stderr_file.name
        manager = KernelManager(kernel_name=kernel_name)
        client = None
        try:
            manager.start_kernel(stdout=subprocess.DEVNULL, stderr=stderr_file)
            client = manager.client()
            client.start_channels()
            client.wait_for_ready(timeout=timeout)
            return ManagedJupyterKernel(manager, client, stderr_file, stderr_path)
        except Exception as exc:
            kernel_was_started = bool(manager.has_kernel)
            diagnostics = _diagnostics(manager, stderr_path)
            if client is not None:
                try:
                    client.stop_channels()
                except Exception:
                    pass
            try:
                if manager.has_kernel:
                    manager.shutdown_kernel(now=True)
            except Exception:
                pass
            stderr_file.close()
            _remove_file(stderr_path)
            reason = "readiness_failed" if kernel_was_started else "spawn_failed"
            raise KernelAdapterError(
                f"Kernel {kernel_name!r} failed to become ready: {exc}",
                layer="kernel",
                reason=reason,
                retryable=True,
                diagnostics=diagnostics,
            ) from exc


class ManagedJupyterKernel:
    def __init__(self, manager: KernelManager, client: Any, stderr_file, stderr_path: str) -> None:
        self._manager = manager
        self._client = client
        self._stderr_file = stderr_file
        self._stderr_path = stderr_path
        self._lock = threading.Lock()
        self._closed = False

    @property
    def pid(self) -> int | None:
        provisioner = getattr(self._manager, "provisioner", None)
        pid = getattr(provisioner, "pid", None)
        return int(pid) if isinstance(pid, int) and pid > 0 else None

    def execute(self, code: str, *, timeout: float) -> KernelExecutionResult:
        with self._lock:
            if self._closed or not self._is_alive():
                raise self._kernel_died("Kernel is not alive before execution")

            outputs: list[KernelOutput] = []

            def output_hook(message: dict[str, Any]) -> None:
                message_type = message.get("msg_type") or message.get("header", {}).get("msg_type", "")
                content = message.get("content", {})
                if message_type == "stream":
                    name = str(content.get("name", "stdout"))
                    outputs.append(
                        KernelOutput(
                            output_kind="stderr" if name == "stderr" else "stdout",
                            media_type="text/x-ansi",
                            data=str(content.get("text", "")),
                        )
                    )
                elif message_type in {"execute_result", "display_data"}:
                    output_kind = "result" if message_type == "execute_result" else "display"
                    data = content.get("data", {})
                    if isinstance(data, dict):
                        for media_type, value in data.items():
                            if isinstance(value, str):
                                outputs.append(KernelOutput(output_kind, str(media_type), value))
                elif message_type == "error":
                    traceback = content.get("traceback", [])
                    text = "\n".join(str(line) for line in traceback) if isinstance(traceback, list) else str(traceback)
                    if text:
                        outputs.append(KernelOutput("stderr", "text/x-ansi", text))

            try:
                reply = self._client.execute_interactive(
                    code,
                    allow_stdin=False,
                    stop_on_error=True,
                    timeout=timeout,
                    output_hook=output_hook,
                )
            except (queue.Empty, TimeoutError) as exc:
                if not self._is_alive():
                    raise self._kernel_died("Kernel died while executing") from exc
                raise KernelAdapterError(
                    f"Execution did not complete within {timeout:.1f}s",
                    layer="execution",
                    reason="timeout",
                    retryable=True,
                ) from exc
            except Exception as exc:
                if not self._is_alive():
                    raise self._kernel_died(f"Kernel died while executing: {exc}") from exc
                raise KernelAdapterError(
                    f"Kernel execution channel failed: {exc}",
                    layer="execution",
                    reason="channel_closed",
                    retryable=True,
                ) from exc

            content = reply.get("content", {}) if isinstance(reply, dict) else {}
            if content.get("status") == "ok":
                return KernelExecutionResult("succeeded", tuple(outputs))
            return KernelExecutionResult(
                "failed",
                tuple(outputs),
                error_name=str(content.get("ename", "")),
                error_value=str(content.get("evalue", "")),
            )

    def stop(self, *, timeout: float) -> None:
        with self._lock:
            if self._closed:
                return
            self._manager.shutdown_wait_time = max(timeout, 0.1)
            failure: KernelAdapterError | None = None
            failure_cause: Exception | None = None
            try:
                self._manager.shutdown_kernel(now=False, restart=False)
            except Exception as exc:
                try:
                    self._manager.shutdown_kernel(now=True, restart=False)
                except Exception as force_exc:
                    failure_cause = force_exc
                    failure = KernelAdapterError(
                        f"Graceful and forced kernel shutdown failed: {exc}; {force_exc}",
                        layer="kernel",
                        reason="cleanup_incomplete",
                        retryable=True,
                        diagnostics=_diagnostics(self._manager, self._stderr_path),
                    )
            self._close_resources()
            if failure is not None:
                raise failure from failure_cause

    def _is_alive(self) -> bool:
        try:
            return bool(self._manager.is_alive())
        except Exception:
            return False

    def _kernel_died(self, message: str) -> KernelAdapterError:
        return KernelAdapterError(
            message,
            layer="kernel",
            reason="kernel_died",
            retryable=True,
            diagnostics=_diagnostics(self._manager, self._stderr_path),
        )

    def _close_resources(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._client.stop_channels()
        except Exception:
            pass
        try:
            self._stderr_file.close()
        finally:
            _remove_file(self._stderr_path)


def _diagnostics(manager: KernelManager, stderr_path: str) -> ProcessDiagnostics:
    provisioner = getattr(manager, "provisioner", None)
    process = getattr(provisioner, "process", None)
    pid = getattr(provisioner, "pid", None)
    return_code = process.poll() if process is not None else None
    exit_code = return_code if isinstance(return_code, int) and return_code >= 0 else None
    signal = -return_code if isinstance(return_code, int) and return_code < 0 else None
    stderr_excerpt, truncated = _read_stderr(stderr_path)
    return ProcessDiagnostics(
        pid=int(pid) if isinstance(pid, int) and pid > 0 else None,
        exit_code=exit_code,
        signal=signal,
        stderr_excerpt=stderr_excerpt,
        stderr_truncated=truncated,
    )


def _read_stderr(path: str) -> tuple[str, bool]:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return "", False
    truncated = len(data) > MAX_STDERR_BYTES
    if truncated:
        data = data[-MAX_STDERR_BYTES:]
    return data.decode("utf-8", errors="replace"), truncated


def _remove_file(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
