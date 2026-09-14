from __future__ import annotations

import json
import os
import queue
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator

from jupyter_client import KernelManager

from jusi.application.ports import (
    KernelAdapterError,
    KernelAdapterSpec,
    KernelExecutionResult,
    KernelFactory,
    KernelOutput,
    PluginHandoff,
)
from jusi.domain.models import ProcessDiagnostics
from jusi.protocol import ProtocolValidationError, validate_plugin_kernel_message



MAX_STDERR_BYTES = 16 * 1024
MAX_PLUGIN_CONTROL_BYTES = 1024 * 1024
MAX_OUTPUT_EVENT_BYTES = 16 * 1024
ADAPTER_ATTESTATION_MIME = "application/vnd.jusi.adapters-ready.v1+json"
PLUGIN_HANDOFF_MIME = "application/vnd.jusi.handoff.v1+json"


def _plugin_control_size(value: object) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ProtocolValidationError("Plugin control message is not JSON-serializable") from exc


def _text_chunks(value: str, *, limit: int = MAX_OUTPUT_EVENT_BYTES) -> Iterator[str]:
    """Split UTF-8 text without changing the byte stream when chunks are joined."""
    encoded = value.encode("utf-8")
    if not encoded:
        yield ""
        return
    start = 0
    while start < len(encoded):
        end = min(start + limit, len(encoded))
        while end < len(encoded) and encoded[end] & 0xC0 == 0x80:
            end -= 1
        yield encoded[start:end].decode("utf-8")
        start = end


def _load_kernel_adapters(
    client: Any,
    adapters: tuple[KernelAdapterSpec, ...],
    configuration: dict[str, Any],
    *,
    timeout: float,
) -> None:
    modules = [adapter.module for adapter in adapters]
    source = "\n".join([
        "import importlib as _jusi_importlib",
        "import json as _jusi_json",
        "from IPython.display import display as _jusi_display",
        f"_jusi_modules = {json.dumps(modules)}",
        f"_jusi_runtime_config = _jusi_json.loads({json.dumps(json.dumps(configuration, ensure_ascii=False))})",
        "_jusi_adapters = []",
        "for _jusi_module_name in _jusi_modules:",
        "    _jusi_module = _jusi_importlib.import_module(_jusi_module_name)",
        "    _jusi_factory = getattr(_jusi_module, 'jusi_kernel_adapter_v1')",
        "    _jusi_manifest = dict(_jusi_factory())",
        "    _jusi_manifest['module'] = _jusi_module_name",
        "    _jusi_configure = getattr(_jusi_module, 'configure_jusi_runtime_v1', None)",
        "    if callable(_jusi_configure):",
        "        _jusi_configure(dict(_jusi_runtime_config))",
        "    _jusi_loader = getattr(_jusi_module, 'load_ipython_extension', None)",
        "    if callable(_jusi_loader):",
        "        _jusi_loader(get_ipython())",
        "    _jusi_adapters.append(_jusi_manifest)",
        f"_jusi_display({{{ADAPTER_ATTESTATION_MIME!r}: {{'protocol_version': 1, 'kind': 'plugin.adapters_ready', 'adapters': _jusi_adapters}}}}, raw=True)",
        "del _jusi_importlib, _jusi_json, _jusi_display, _jusi_modules, _jusi_runtime_config, _jusi_adapters",
        "del _jusi_module_name, _jusi_module, _jusi_factory, _jusi_manifest, _jusi_configure, _jusi_loader",
    ])
    attestations: list[object] = []
    errors: list[str] = []

    def output_hook(message: dict[str, Any]) -> None:
        message_type = message.get("msg_type") or message.get("header", {}).get("msg_type", "")
        content = message.get("content", {})
        if message_type in {"display_data", "execute_result"}:
            data = content.get("data", {})
            if isinstance(data, dict) and ADAPTER_ATTESTATION_MIME in data:
                attestations.append(data[ADAPTER_ATTESTATION_MIME])
        elif message_type == "error":
            traceback_lines = content.get("traceback", [])
            if isinstance(traceback_lines, list):
                errors.extend(str(line) for line in traceback_lines)

    try:
        reply = client.execute_interactive(
            source,
            allow_stdin=False,
            stop_on_error=True,
            timeout=timeout,
            output_hook=output_hook,
        )
    except Exception as exc:
        raise KernelAdapterError(
            f"Kernel adapter loading did not complete: {exc}",
            layer="kernel",
            reason="readiness_failed",
            retryable=True,
            details={"adapter_modules": modules},
        ) from exc
    content = reply.get("content", {}) if isinstance(reply, dict) else {}
    if content.get("status") != "ok":
        raise KernelAdapterError(
            "Kernel adapter loading failed",
            layer="kernel",
            reason="readiness_failed",
            retryable=False,
            details={
                "adapter_modules": modules,
                "error_name": str(content.get("ename", "")),
                "error_value": str(content.get("evalue", ""))[:1000],
                "traceback_excerpt": "\n".join(errors)[-4000:],
            },
        )
    if len(attestations) != 1:
        raise KernelAdapterError(
            "Kernel adapters produced no unique attestation",
            layer="kernel",
            reason="protocol_violation",
            retryable=False,
            details={"adapter_modules": modules, "attestation_count": len(attestations)},
        )
    try:
        if _plugin_control_size(attestations[0]) > MAX_PLUGIN_CONTROL_BYTES:
            raise ProtocolValidationError("Kernel adapter attestation exceeds the size limit")
        attestation = validate_plugin_kernel_message(attestations[0])
    except ProtocolValidationError as exc:
        raise KernelAdapterError(
            f"Kernel adapter attestation is invalid: {exc}",
            layer="kernel",
            reason="protocol_violation",
            retryable=False,
            details={"adapter_modules": modules},
        ) from exc
    expected = {
        adapter.module: {
            "plugin_id": adapter.plugin_id,
            "plugin_version": adapter.plugin_version,
            "families": sorted([
                {"family_id": family_id, "magic_name": magic_name}
                for family_id, magic_name in adapter.families
            ], key=lambda family: (family["family_id"], family["magic_name"])),
        }
        for adapter in adapters
    }
    observed = {
        item["module"]: {
            "plugin_id": item["plugin_id"],
            "plugin_version": item["plugin_version"],
            "families": sorted(
                item["families"],
                key=lambda family: (family["family_id"], family["magic_name"]),
            ),
        }
        for item in attestation["adapters"]
    }
    if observed != expected:
        raise KernelAdapterError(
            "Kernel adapter identity does not match the runtime plugin catalog",
            layer="kernel",
            reason="conflict",
            retryable=False,
            details={"adapter_modules": modules, "expected": expected, "observed": observed},
        )


class ManagedJupyterKernelFactory(KernelFactory):
    def start(
        self,
        kernel_name: str,
        *,
        timeout: float,
        adapters: tuple[KernelAdapterSpec, ...] = (),
        configuration: dict[str, Any] | None = None,
    ):
        stderr_file = tempfile.NamedTemporaryFile(prefix="jusi-kernel-", suffix=".stderr", delete=False)
        stderr_path = stderr_file.name
        manager = KernelManager(kernel_name=kernel_name)
        client = None
        artifacts = tempfile.TemporaryDirectory(prefix="jusi-artifacts-")
        try:
            manager.start_kernel(stdout=subprocess.DEVNULL, stderr=stderr_file,
                                 env={**os.environ, "JUSI_KERNEL_ARTIFACT_DIRECTORY": artifacts.name})
            client = manager.client()
            client.start_channels()
            client.wait_for_ready(timeout=timeout)
            if adapters:
                _load_kernel_adapters(client, adapters, dict(configuration or {}), timeout=timeout)
            return ManagedJupyterKernel(manager, client, stderr_file, stderr_path, artifacts)
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
            artifacts.cleanup()
            stderr_file.close()
            _remove_file(stderr_path)
            if isinstance(exc, KernelAdapterError):
                raise KernelAdapterError(
                    str(exc),
                    layer=exc.layer,
                    reason=exc.reason,
                    retryable=exc.retryable,
                    diagnostics=diagnostics,
                    details=exc.details,
                ) from exc
            reason = "readiness_failed" if kernel_was_started else "spawn_failed"
            raise KernelAdapterError(
                f"Kernel {kernel_name!r} failed to become ready: {exc}",
                layer="kernel",
                reason=reason,
                retryable=True,
                diagnostics=diagnostics,
                details=getattr(exc, "details", {}),
            ) from exc


class ManagedJupyterKernel:
    def __init__(self, manager: KernelManager, client: Any, stderr_file, stderr_path: str, artifacts=None) -> None:
        self._artifacts = artifacts
        self._manager = manager
        self._client = client
        self._stderr_file = stderr_file
        self._stderr_path = stderr_path
        self._lock = threading.Lock()
        self._closed = False
        self._control_lock = threading.Lock()
        self._executing = False
        self._pending_input: str | None = None
        self._input_value: str | None = None
        self._interrupt_requested = threading.Event()

    @property
    def pid(self) -> int | None:
        provisioner = getattr(self._manager, "provisioner", None)
        pid = getattr(provisioner, "pid", None)
        return int(pid) if isinstance(pid, int) and pid > 0 else None

    def complete(self, prefix: str, *, timeout: float) -> dict[str, Any]:
        with self._lock:
            if self._closed or not self._is_alive():
                raise self._kernel_died("Kernel is not alive before completion")
            try:
                request_id = self._client.complete(code=prefix, cursor_pos=len(prefix))
                deadline = time.monotonic() + timeout
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise queue.Empty
                    reply = self._client.get_shell_msg(timeout=remaining)
                    if reply.get("parent_header", {}).get("msg_id") != request_id:
                        continue
                    content = reply.get("content", {})
                    if reply.get("header", {}).get("msg_type") != "complete_reply" or content.get("status") != "ok":
                        raise KernelAdapterError("Kernel completion failed", layer="execution",
                                                 reason="protocol_violation", retryable=False)
                    matches = content.get("matches")
                    if not isinstance(matches, list) or any(not isinstance(x, str) for x in matches):
                        raise KernelAdapterError("Invalid kernel completion matches", layer="execution",
                                                 reason="protocol_violation", retryable=False)
                    return {"items": [{"text": text, "start": content.get("cursor_start"),
                                       "end": content.get("cursor_end")} for text in matches[:500]]}
            except KernelAdapterError:
                raise
            except Exception as exc:
                if not self._is_alive():
                    raise self._kernel_died("Kernel died during completion") from exc
                raise KernelAdapterError("Kernel completion did not reply in time", layer="execution",
                                         reason="timeout" if isinstance(exc, queue.Empty) else "channel_closed",
                                         retryable=True) from exc

    def execute(
        self,
        code: str,
        *,
        timeout: float | None,
        on_output: Callable[[KernelOutput], None],
        on_input: Callable[[str, str, bool], None] | None = None,
    ) -> KernelExecutionResult:
        with self._lock:
            if self._closed or not self._is_alive():
                raise self._kernel_died("Kernel is not alive before execution")
            with self._control_lock:
                self._executing = True
                self._interrupt_requested.clear()

            handoffs: list[PluginHandoff] = []
            handoff_errors: list[str] = []

            def emit_output(output_kind: str, media_type: str, value: object) -> None:
                for chunk in _text_chunks(str(value)):
                    on_output(KernelOutput(output_kind, media_type, chunk))

            def output_hook(message: dict[str, Any]) -> None:
                message_type = message.get("msg_type") or message.get("header", {}).get("msg_type", "")
                content = message.get("content", {})
                if message_type == "stream":
                    name = str(content.get("name", "stdout"))
                    emit_output(
                        "stderr" if name == "stderr" else "stdout",
                        "text/x-ansi",
                        content.get("text", ""),
                    )
                elif message_type in {"execute_result", "display_data"}:
                    output_kind = "result" if message_type == "execute_result" else "display"
                    data = content.get("data", {})
                    if isinstance(data, dict):
                        for media_type, value in data.items():
                            if media_type == PLUGIN_HANDOFF_MIME:
                                try:
                                    if _plugin_control_size(value) > MAX_PLUGIN_CONTROL_BYTES:
                                        raise ProtocolValidationError("Plugin handoff exceeds the size limit")
                                    handoff = validate_plugin_kernel_message(value)
                                    if handoff["kind"] != "plugin.handoff":
                                        raise ProtocolValidationError("Kernel output is not a plugin handoff")
                                    handoffs.append(PluginHandoff(
                                        plugin_id=handoff["plugin_id"],
                                        plugin_version=handoff["plugin_version"],
                                        family_id=handoff["family_id"],
                                        magic_name=handoff["magic_name"],
                                        payload=dict(handoff["payload"]),
                                    ))
                                except ProtocolValidationError as exc:
                                    handoff_errors.append(str(exc))
                                continue
                            if isinstance(value, str):
                                emit_output(output_kind, str(media_type), value)
                elif message_type == "error":
                    traceback = content.get("traceback", [])
                    text = "\n".join(str(line) for line in traceback) if isinstance(traceback, list) else str(traceback)
                    if text:
                        emit_output("stderr", "text/x-ansi", text)

            try:
                if on_input is None:
                    reply = self._client.execute_interactive(
                        code, allow_stdin=False, stop_on_error=True,
                        timeout=timeout, output_hook=output_hook,
                    )
                else:
                    reply = self._execute_with_input(code, timeout, output_hook, on_input)
            except KernelAdapterError:
                self._finish_execution()
                raise
            except (queue.Empty, TimeoutError) as exc:
                if self._finish_execution():
                    raise KernelAdapterError(
                        "Execution was interrupted",
                        layer="execution",
                        reason="interrupted",
                        retryable=False,
                    ) from exc
                if not self._is_alive():
                    raise self._kernel_died("Kernel died while executing") from exc
                raise KernelAdapterError(
                    str(exc) or "Execution reply timed out",
                    layer="execution",
                    reason="timeout",
                    retryable=True,
                ) from exc
            except Exception as exc:
                self._finish_execution()
                if not self._is_alive():
                    raise self._kernel_died(f"Kernel died while executing: {exc}") from exc
                raise KernelAdapterError(
                    f"Kernel execution channel failed: {exc}",
                    layer="execution",
                    reason="channel_closed",
                    retryable=True,
                ) from exc

            content = reply.get("content", {}) if isinstance(reply, dict) else {}
            interrupt_requested = self._finish_execution()
            if handoff_errors or len(handoffs) > 1:
                raise KernelAdapterError(
                    "Kernel emitted an invalid or ambiguous plugin handoff",
                    layer="protocol",
                    reason="protocol_violation",
                    retryable=False,
                    details={
                        "handoff_count": len(handoffs),
                        "errors": handoff_errors[:4],
                    },
                )
            if content.get("status") == "ok":
                return KernelExecutionResult("succeeded", handoffs=tuple(handoffs))
            if interrupt_requested:
                return KernelExecutionResult(
                    "interrupted",
                    error_name=str(content.get("ename", "")),
                    error_value=str(content.get("evalue", "")),
                )
            return KernelExecutionResult(
                "failed",
                error_name=str(content.get("ename", "")),
                error_value=str(content.get("evalue", "")),
                handoffs=tuple(handoffs),
            )

    def submit_input(self, input_request_id: str, value: str) -> None:
        # Only queue here. The execution thread owns every Jupyter socket read/write.
        with self._control_lock:
            if (not self._executing or self._interrupt_requested.is_set()
                    or self._pending_input != input_request_id or self._input_value is not None):
                raise KernelAdapterError(
                    "Input request is no longer pending", layer="execution",
                    reason="conflict", retryable=False,
                )
            self._input_value = value

    def _execute_with_input(
        self, code: str, timeout: float | None,
        output_hook: Callable[[dict[str, Any]], None],
        on_input: Callable[[str, str, bool], None],
    ) -> dict[str, Any]:
        msg_id = self._client.execute(code, allow_stdin=True, stop_on_error=True)
        remaining = timeout
        previous = time.monotonic()
        waiting = False
        timeout_error = None
        drain_deadline = None
        while True:
            now = time.monotonic()
            if remaining is not None and (not waiting or self._interrupt_requested.is_set()):
                remaining -= now - previous
            previous = now
            if not self._is_alive():
                raise self._kernel_died("Kernel died while executing")
            if timeout_error is None and remaining is not None and remaining <= 0:
                timeout_error = "Execution timed out"
                self._manager.interrupt_kernel()
                drain_deadline = now + 2.0
            if drain_deadline is not None and now >= drain_deadline:
                raise TimeoutError(timeout_error)

            with self._control_lock:
                if self._interrupt_requested.is_set() or timeout_error:
                    self._pending_input = None
                    self._input_value = None
                    waiting = False
                elif self._input_value is not None:
                    value = self._input_value
                    self._pending_input = None
                    self._input_value = None
                    self._client.input(value)
                    waiting = False

            # Drain already-arrived output before presenting a prompt on the
            # independent stdin channel. Bound the batch so control stays responsive.
            idle = False
            for index in range(64):
                try:
                    message = self._client.get_iopub_msg(timeout=0.05 if index == 0 else 0)
                except queue.Empty:
                    break
                if message.get("parent_header", {}).get("msg_id") != msg_id:
                    continue
                output_hook(message)
                if (message.get("header", {}).get("msg_type") == "status"
                        and message.get("content", {}).get("execution_state") == "idle"):
                    idle = True
                    break
            if idle:
                break
            try:
                request = self._client.get_stdin_msg(timeout=0)
            except queue.Empty:
                request = None
            if (request is not None and request.get("parent_header", {}).get("msg_id") == msg_id
                    and request.get("header", {}).get("msg_type") == "input_request"
                    and not self._interrupt_requested.is_set() and timeout_error is None):
                content = request.get("content", {})
                prompt, password = content.get("prompt"), content.get("password")
                if not isinstance(prompt, str) or not isinstance(password, bool):
                    self._manager.interrupt_kernel()
                    raise KernelAdapterError("Invalid kernel input request", layer="execution",
                                             reason="protocol_violation", retryable=False)
                request_id = "inp_" + uuid.uuid4().hex
                with self._control_lock:
                    self._pending_input = request_id
                    self._input_value = None
                waiting = True
                on_input(request_id, prompt, password)
        deadline = None if remaining is None else time.monotonic() + max(remaining, 2.0 if timeout_error else 0.1)
        while True:
            if not self._is_alive():
                raise self._kernel_died("Kernel died while waiting for execution reply")
            reply_timeout = 0.1 if deadline is None else deadline - time.monotonic()
            if reply_timeout <= 0:
                raise TimeoutError(timeout_error or "Timeout waiting for execution reply")
            try:
                reply = self._client.get_shell_msg(timeout=min(reply_timeout, 0.1))
            except queue.Empty:
                continue
            if reply.get("parent_header", {}).get("msg_id") == msg_id:
                if timeout_error:
                    raise TimeoutError(timeout_error)
                return reply

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

    def interrupt(self) -> None:
        # Execution holds `_lock` while waiting on Jupyter. Interrupt must be a
        # separate control path or it could only run after execution returned.
        if self._closed or not self._is_alive():
            raise self._kernel_died("Kernel is not alive before interrupt")
        try:
            with self._control_lock:
                if not self._executing:
                    raise KernelAdapterError(
                        "Kernel no longer has an active execution",
                        layer="execution",
                        reason="conflict",
                        retryable=False,
                    )
                self._interrupt_requested.set()
                self._manager.interrupt_kernel()
        except KernelAdapterError:
            raise
        except Exception as exc:
            self._interrupt_requested.clear()
            if not self._is_alive():
                raise self._kernel_died("Kernel died while interrupting execution") from exc
            raise KernelAdapterError(
                f"Kernel interrupt failed: {exc}",
                layer="kernel",
                reason="channel_closed",
                retryable=True,
                diagnostics=_diagnostics(self._manager, self._stderr_path),
            ) from exc

    def _finish_execution(self) -> bool:
        with self._control_lock:
            requested = self._interrupt_requested.is_set()
            self._interrupt_requested.clear()
            self._executing = False
            self._pending_input = None
            self._input_value = None
            return requested

    def terminate(self) -> None:
        # Only the exact locally owned process; no channel/execute lock. This
        # escape path is reserved for explicit teardown after interrupt failed.
        process = getattr(getattr(self._manager, "provisioner", None), "process", None)
        if process is None:
            raise KernelAdapterError("Owned kernel process is unavailable for forced teardown",
                layer="kernel", reason="cleanup_incomplete", retryable=True)
        if process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass

    def check_alive(self) -> None:
        if self._closed or not self._is_alive():
            raise self._kernel_died("Managed kernel process exited")

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
            if self._artifacts is not None:
                self._artifacts.cleanup()


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
