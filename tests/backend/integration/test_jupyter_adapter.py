from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from jusi.application.ports import KernelAdapterError
import jusi.infrastructure.jupyter_kernel as jupyter_kernel
from jusi.infrastructure.jupyter_kernel import MAX_STDERR_BYTES, ManagedJupyterKernel


class FakeManager:
    def __init__(self, *, alive: bool = True, return_code=None, shutdown_fails: bool = False) -> None:  # type: ignore[no-untyped-def]
        self.alive = alive
        self.shutdown_fails = shutdown_fails
        self.provisioner = SimpleNamespace(pid=9876, process=SimpleNamespace(poll=lambda: return_code))
        self.shutdown_calls: list[tuple[bool, bool]] = []

    def is_alive(self) -> bool:
        return self.alive

    def shutdown_kernel(self, *, now: bool, restart: bool) -> None:
        self.shutdown_calls.append((now, restart))
        if self.shutdown_fails:
            raise RuntimeError("shutdown failed")


class FakeClient:
    def __init__(self) -> None:
        self.stopped = False

    def execute_interactive(self, code: str, **kwargs):  # type: ignore[no-untyped-def]
        assert code == "1 + 1"
        hook = kwargs["output_hook"]
        hook({"msg_type": "stream", "content": {"name": "stdout", "text": "\u001b[32mhello\u001b[0m\n"}})
        hook({"msg_type": "execute_result", "content": {"data": {"text/plain": "2"}}})
        return {"content": {"status": "ok"}}

    def stop_channels(self) -> None:
        self.stopped = True


def test_jupyter_adapter_preserves_ansi_and_result_media() -> None:
    stderr_file = tempfile.NamedTemporaryFile(delete=False)
    stderr_path = stderr_file.name
    manager = FakeManager()
    client = FakeClient()
    kernel = ManagedJupyterKernel(manager, client, stderr_file, stderr_path)  # type: ignore[arg-type]

    result = kernel.execute("1 + 1", timeout=1)

    assert result.outcome == "succeeded"
    assert result.outputs[0].media_type == "text/x-ansi"
    assert result.outputs[0].data == "\u001b[32mhello\u001b[0m\n"
    assert result.outputs[1].media_type == "text/plain"
    assert result.outputs[1].data == "2"

    kernel.stop(timeout=1)
    assert manager.shutdown_calls == [(False, False)]
    assert client.stopped
    assert not Path(stderr_path).exists()


def test_execution_error_preserves_ansi_traceback_without_killing_kernel() -> None:
    class ErrorClient(FakeClient):
        def execute_interactive(self, code: str, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["output_hook"](
                {
                    "msg_type": "error",
                    "content": {"traceback": ["\u001b[31mValueError\u001b[0m", "bad value"]},
                }
            )
            return {"content": {"status": "error", "ename": "ValueError", "evalue": "bad value"}}

    stderr_file = tempfile.NamedTemporaryFile(delete=False)
    stderr_path = stderr_file.name
    manager = FakeManager()
    client = ErrorClient()
    kernel = ManagedJupyterKernel(manager, client, stderr_file, stderr_path)  # type: ignore[arg-type]

    result = kernel.execute("raise ValueError('bad value')", timeout=1)

    assert result.outcome == "failed"
    assert result.error_name == "ValueError"
    assert result.error_value == "bad value"
    assert result.outputs[0].output_kind == "stderr"
    assert result.outputs[0].media_type == "text/x-ansi"
    assert result.outputs[0].data == "\u001b[31mValueError\u001b[0m\nbad value"
    assert manager.is_alive()
    kernel.stop(timeout=1)


def test_dead_kernel_reports_process_and_bounded_stderr_then_cleans_up() -> None:
    stderr_file = tempfile.NamedTemporaryFile(delete=False)
    stderr_path = stderr_file.name
    stderr_file.write(b"prefix-" + b"x" * MAX_STDERR_BYTES + b"-fatal\n")
    stderr_file.flush()
    manager = FakeManager(alive=False, return_code=-9)
    client = FakeClient()
    kernel = ManagedJupyterKernel(manager, client, stderr_file, stderr_path)  # type: ignore[arg-type]

    with pytest.raises(KernelAdapterError) as captured:
        kernel.execute("large body", timeout=1)

    diagnostics = captured.value.diagnostics
    assert captured.value.layer == "kernel"
    assert captured.value.reason == "kernel_died"
    assert diagnostics is not None
    assert diagnostics.pid == 9876
    assert diagnostics.signal == 9
    assert diagnostics.stderr_truncated is True
    assert diagnostics.stderr_excerpt.endswith("-fatal\n")

    kernel.stop(timeout=1)
    assert client.stopped
    assert not Path(stderr_path).exists()


def test_failed_shutdown_preserves_diagnostics_and_still_closes_owned_resources() -> None:
    stderr_file = tempfile.NamedTemporaryFile(delete=False)
    stderr_path = stderr_file.name
    stderr_file.write(b"shutdown diagnostic\n")
    stderr_file.flush()
    manager = FakeManager(return_code=17, shutdown_fails=True)
    client = FakeClient()
    kernel = ManagedJupyterKernel(manager, client, stderr_file, stderr_path)  # type: ignore[arg-type]

    with pytest.raises(KernelAdapterError) as captured:
        kernel.stop(timeout=1)

    assert captured.value.reason == "cleanup_incomplete"
    assert captured.value.diagnostics is not None
    assert captured.value.diagnostics.exit_code == 17
    assert captured.value.diagnostics.stderr_excerpt == "shutdown diagnostic\n"
    assert manager.shutdown_calls == [(False, False), (True, False)]
    assert client.stopped
    assert not Path(stderr_path).exists()
    kernel.stop(timeout=1)


def test_readiness_failure_captures_process_stderr_and_cleans_partial_start(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    state = SimpleNamespace(stderr_path=None, stopped=False, shutdown=False)

    class NotReadyClient:
        def start_channels(self) -> None:
            return None

        def wait_for_ready(self, *, timeout: float) -> None:
            assert timeout == 1
            raise TimeoutError("not ready")

        def stop_channels(self) -> None:
            state.stopped = True

    class NotReadyManager:
        has_kernel = False

        def __init__(self, *, kernel_name: str) -> None:
            assert kernel_name == "missing"
            self.provisioner = SimpleNamespace(pid=7654, process=SimpleNamespace(poll=lambda: 23))

        def start_kernel(self, *, stdout, stderr) -> None:  # type: ignore[no-untyped-def]
            del stdout
            self.has_kernel = True
            state.stderr_path = stderr.name
            stderr.write(b"kernel boot failed\n")
            stderr.flush()

        def client(self) -> NotReadyClient:
            return NotReadyClient()

        def shutdown_kernel(self, *, now: bool) -> None:
            assert now is True
            state.shutdown = True

    monkeypatch.setattr(jupyter_kernel, "KernelManager", NotReadyManager)

    with pytest.raises(KernelAdapterError) as captured:
        jupyter_kernel.ManagedJupyterKernelFactory().start("missing", timeout=1)

    assert captured.value.reason == "readiness_failed"
    assert captured.value.diagnostics is not None
    assert captured.value.diagnostics.pid == 7654
    assert captured.value.diagnostics.exit_code == 23
    assert captured.value.diagnostics.stderr_excerpt == "kernel boot failed\n"
    assert state.stopped is True
    assert state.shutdown is True
    assert state.stderr_path is not None and not Path(state.stderr_path).exists()
