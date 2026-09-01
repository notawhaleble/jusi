from __future__ import annotations

import json
import os
import queue
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

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
ADAPTER_ATTESTATION_MIME = "application/vnd.jusi.adapters-ready.v1+json"
PLUGIN_HANDOFF_MIME = "application/vnd.jusi.handoff.v1+json"


def _plugin_control_size(value: object) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ProtocolValidationError("Plugin control message is not JSON-serializable") from exc


def _load_kernel_adapters(client: Any, adapters: tuple[KernelAdapterSpec, ...], *, timeout: float) -> None:
    modules = [adapter.module for adapter in adapters]
    source = "\n".join([
        "import importlib as _jusi_importlib",
        "from IPython.display import display as _jusi_display",
        f"_jusi_modules = {json.dumps(modules)}",
        "_jusi_adapters = []",
        "for _jusi_module_name in _jusi_modules:",
        "    _jusi_module = _jusi_importlib.import_module(_jusi_module_name)",
        "    _jusi_factory = getattr(_jusi_module, 'jusi_kernel_adapter_v1')",
        "    _jusi_manifest = dict(_jusi_factory())",
        "    _jusi_manifest['module'] = _jusi_module_name",
        "    _jusi_loader = getattr(_jusi_module, 'load_ipython_extension', None)",
        "    if callable(_jusi_loader):",
        "        _jusi_loader(get_ipython())",
        "    _jusi_adapters.append(_jusi_manifest)",
        f"_jusi_display({{{ADAPTER_ATTESTATION_MIME!r}: {{'protocol_version': 1, 'kind': 'plugin.adapters_ready', 'adapters': _jusi_adapters}}}}, raw=True)",
        "del _jusi_importlib, _jusi_display, _jusi_modules, _jusi_adapters",
        "del _jusi_module_name, _jusi_module, _jusi_factory, _jusi_manifest, _jusi_loader",
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
    ):
        stderr_file = tempfile.NamedTemporaryFile(prefix="jusi-kernel-", suffix=".stderr", delete=False)
        stderr_path = stderr_file.name
        manager = KernelManager(kernel_name=kernel_name)
        client = None
        try:
            manager.start_kernel(stdout=subprocess.DEVNULL, stderr=stderr_file)
            client = manager.client()
            client.start_channels()
            client.wait_for_ready(timeout=timeout)
            if adapters:
                _load_kernel_adapters(client, adapters, timeout=timeout)
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

    def execute(
        self,
        code: str,
        *,
        timeout: float,
        on_output: Callable[[KernelOutput], None],
    ) -> KernelExecutionResult:
        with self._lock:
            if self._closed or not self._is_alive():
                raise self._kernel_died("Kernel is not alive before execution")

            handoffs: list[PluginHandoff] = []
            handoff_errors: list[str] = []

            def output_hook(message: dict[str, Any]) -> None:
                message_type = message.get("msg_type") or message.get("header", {}).get("msg_type", "")
                content = message.get("content", {})
                if message_type == "stream":
                    name = str(content.get("name", "stdout"))
                    on_output(
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
                                on_output(KernelOutput(output_kind, str(media_type), value))
                elif message_type == "error":
                    traceback = content.get("traceback", [])
                    text = "\n".join(str(line) for line in traceback) if isinstance(traceback, list) else str(traceback)
                    if text:
                        on_output(KernelOutput("stderr", "text/x-ansi", text))

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
            return KernelExecutionResult(
                "failed",
                error_name=str(content.get("ename", "")),
                error_value=str(content.get("evalue", "")),
                handoffs=tuple(handoffs),
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
