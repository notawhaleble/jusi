from __future__ import annotations

import queue
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from jusi.application.ports import KernelAdapterError, KernelAdapterSpec
import jusi.infrastructure.jupyter_kernel as jupyter_kernel
from jusi.infrastructure.jupyter_kernel import (
    MAX_OUTPUT_EVENT_BYTES,
    MAX_PLUGIN_CONTROL_BYTES,
    MAX_STDERR_BYTES,
    ManagedJupyterKernel,
    PLUGIN_HANDOFF_MIME,
)


class FakeManager:
    def __init__(self, *, alive: bool = True, return_code=None, shutdown_fails: bool = False) -> None:  # type: ignore[no-untyped-def]
        self.alive = alive
        self.shutdown_fails = shutdown_fails
        self.provisioner = SimpleNamespace(pid=9876, process=SimpleNamespace(poll=lambda: return_code))
        self.shutdown_calls: list[tuple[bool, bool]] = []
        self.interrupt_calls = 0

    def is_alive(self) -> bool:
        return self.alive

    def shutdown_kernel(self, *, now: bool, restart: bool) -> None:
        self.shutdown_calls.append((now, restart))
        if self.shutdown_fails:
            raise RuntimeError("shutdown failed")

    def interrupt_kernel(self) -> None:
        self.interrupt_calls += 1


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

    outputs = []
    result = kernel.execute("1 + 1", timeout=1, on_output=outputs.append)

    assert result.outcome == "succeeded"
    assert outputs[0].media_type == "text/x-ansi"
    assert outputs[0].data == "\u001b[32mhello\u001b[0m\n"
    assert outputs[1].media_type == "text/plain"
    assert outputs[1].data == "2"

    kernel.stop(timeout=1)
    assert manager.shutdown_calls == [(False, False)]
    assert client.stopped
    assert not Path(stderr_path).exists()


@pytest.mark.parametrize(("reply_status", "expected_outcome"), (("error", "interrupted"), ("ok", "succeeded")))
def test_jupyter_interrupt_uses_control_path_while_execution_waits(
    reply_status: str, expected_outcome: str,
) -> None:
    import threading

    class BlockingClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.executing = threading.Event()
            self.released = threading.Event()

        def execute_interactive(self, code: str, **kwargs):  # type: ignore[no-untyped-def]
            self.executing.set()
            assert self.released.wait(2)
            return {"content": {"status": reply_status, "ename": "KeyboardInterrupt", "evalue": ""}}

    stderr_file = tempfile.NamedTemporaryFile(delete=False)
    manager = FakeManager()
    client = BlockingClient()
    kernel = ManagedJupyterKernel(manager, client, stderr_file, stderr_file.name)  # type: ignore[arg-type]
    results = []
    thread = threading.Thread(target=lambda: results.append(kernel.execute("1 + 1", timeout=2, on_output=lambda _: None)))
    thread.start()
    assert client.executing.wait(1)

    kernel.interrupt()
    client.released.set()
    thread.join(2)

    assert manager.interrupt_calls == 1
    assert results[0].outcome == expected_outcome
    kernel.stop(timeout=1)


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

    outputs = []
    result = kernel.execute("raise ValueError('bad value')", timeout=1, on_output=outputs.append)

    assert result.outcome == "failed"
    assert result.error_name == "ValueError"
    assert result.error_value == "bad value"
    assert outputs[0].output_kind == "stderr"
    assert outputs[0].media_type == "text/x-ansi"
    assert outputs[0].data == "\u001b[31mValueError\u001b[0m\nbad value"
    assert manager.is_alive()
    kernel.stop(timeout=1)


def test_output_before_timeout_is_delivered_incrementally() -> None:
    class TimeoutClient(FakeClient):
        def execute_interactive(self, code: str, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["output_hook"]({
                "msg_type": "stream",
                "content": {"name": "stdout", "text": "before timeout\n"},
            })
            raise queue.Empty

    stderr_file = tempfile.NamedTemporaryFile(delete=False)
    manager = FakeManager()
    kernel = ManagedJupyterKernel(manager, TimeoutClient(), stderr_file, stderr_file.name)  # type: ignore[arg-type]
    outputs = []

    with pytest.raises(KernelAdapterError) as captured:
        kernel.execute("slow()", timeout=0.1, on_output=outputs.append)

    assert captured.value.layer == "execution"
    assert captured.value.reason == "timeout"
    assert [output.data for output in outputs] == ["before timeout\n"]
    assert manager.is_alive()
    kernel.stop(timeout=1)


def test_large_text_output_is_utf8_safe_and_byte_bounded() -> None:
    payload = "x" * (MAX_OUTPUT_EVENT_BYTES - 1) + "界" + "\u001b[31mred\u001b[0m"

    class LargeOutputClient(FakeClient):
        def execute_interactive(self, code: str, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["output_hook"]({
                "msg_type": "stream",
                "content": {"name": "stdout", "text": payload},
            })
            return {"content": {"status": "ok"}}

    stderr_file = tempfile.NamedTemporaryFile(delete=False)
    kernel = ManagedJupyterKernel(
        FakeManager(), LargeOutputClient(), stderr_file, stderr_file.name,  # type: ignore[arg-type]
    )
    outputs = []

    result = kernel.execute("large_output()", timeout=1, on_output=outputs.append)

    assert result.outcome == "succeeded"
    assert len(outputs) == 2
    assert all(len(output.data.encode("utf-8")) <= MAX_OUTPUT_EVENT_BYTES for output in outputs)
    assert "".join(output.data for output in outputs) == payload
    assert all(output.output_kind == "stdout" for output in outputs)
    assert all(output.media_type == "text/x-ansi" for output in outputs)
    kernel.stop(timeout=1)


def test_execution_captures_structured_handoff_without_rendering_it() -> None:
    class HandoffClient(FakeClient):
        def execute_interactive(self, code: str, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["output_hook"]({
                "msg_type": "display_data",
                "content": {"data": {
                    PLUGIN_HANDOFF_MIME: {
                        "protocol_version": 1,
                        "kind": "plugin.handoff",
                        "plugin_id": "fixture_provider",
                        "plugin_version": "1.2.3",
                        "family_id": "fixture",
                        "magic_name": "fixture",
                        "payload": {"value": 7},
                    },
                }},
            })
            return {"content": {"status": "ok"}}

    stderr_file = tempfile.NamedTemporaryFile(delete=False)
    kernel = ManagedJupyterKernel(FakeManager(), HandoffClient(), stderr_file, stderr_file.name)  # type: ignore[arg-type]
    outputs = []
    result = kernel.execute("%%fixture", timeout=1, on_output=outputs.append)
    assert outputs == []
    assert result.handoffs[0].plugin_id == "fixture_provider"
    assert result.handoffs[0].payload == {"value": 7}
    kernel.stop(timeout=1)


def test_execution_rejects_oversized_plugin_handoff_without_killing_kernel() -> None:
    class OversizedHandoffClient(FakeClient):
        def execute_interactive(self, code: str, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["output_hook"]({
                "msg_type": "display_data",
                "content": {"data": {
                    PLUGIN_HANDOFF_MIME: {
                        "protocol_version": 1,
                        "kind": "plugin.handoff",
                        "plugin_id": "fixture_provider",
                        "plugin_version": "1.2.3",
                        "family_id": "fixture",
                        "magic_name": "fixture",
                        "payload": {"value": "x" * MAX_PLUGIN_CONTROL_BYTES},
                    },
                }},
            })
            return {"content": {"status": "ok"}}

    stderr_file = tempfile.NamedTemporaryFile(delete=False)
    manager = FakeManager()
    kernel = ManagedJupyterKernel(manager, OversizedHandoffClient(), stderr_file, stderr_file.name)  # type: ignore[arg-type]
    with pytest.raises(KernelAdapterError) as captured:
        kernel.execute("%%fixture", timeout=1, on_output=lambda output: None)
    assert captured.value.layer == "protocol"
    assert captured.value.reason == "protocol_violation"
    assert captured.value.details["errors"] == ["Plugin handoff exceeds the size limit"]
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
        kernel.execute("large body", timeout=1, on_output=lambda output: None)

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

        def start_kernel(self, *, stdout, stderr, env) -> None:  # type: ignore[no-untyped-def]
            del stdout
            state.artifacts = env["JUSI_KERNEL_ARTIFACT_DIRECTORY"]
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
    assert not Path(state.artifacts).exists()


def test_real_kernel_loads_and_attests_catalog_adapter(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "fixture_kernel_adapter.py").write_text(
        """
def jusi_kernel_adapter_v1():
    return {
        "plugin_id": "fixture_provider",
        "plugin_version": "1.2.3",
        "families": [{"family_id": "fixture", "magic_name": "fixture"}],
    }

_runtime_config = {}

def configure_jusi_runtime_v1(config):
    global _runtime_config
    _runtime_config = config

def load_ipython_extension(ipython):
    ipython.user_ns["fixture_adapter_loaded"] = 41
    ipython.user_ns["fixture_adapter_config"] = _runtime_config
""",
        encoding="utf-8",
    )
    existing = __import__("os").environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path) + ((":" + existing) if existing else ""))
    adapter = KernelAdapterSpec(
        plugin_id="fixture_provider",
        plugin_version="1.2.3",
        module="fixture_kernel_adapter",
        families=(("fixture", "fixture"),),
    )
    kernel = jupyter_kernel.ManagedJupyterKernelFactory().start(
        "python3",
        timeout=8,
        adapters=(adapter,),
        configuration={"sql": {"main": {"provider": "sqlite"}}},
    )
    try:
        outputs = []
        result = kernel.execute("fixture_adapter_loaded + 1", timeout=5, on_output=outputs.append)
        assert result.outcome == "succeeded"
        assert any(output.data == "42" for output in outputs)
        outputs = []
        kernel.execute(
            "fixture_adapter_config['sql']['main']['provider']",
            timeout=5,
            on_output=outputs.append,
        )
        assert any(output.data == "'sqlite'" for output in outputs)
    finally:
        kernel.stop(timeout=5)


def test_real_kernel_rejects_adapter_identity_mismatch(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "fixture_bad_adapter.py").write_text(
        """
def jusi_kernel_adapter_v1():
    return {
        "plugin_id": "wrong_provider",
        "plugin_version": "9.9.9",
        "families": [{"family_id": "fixture", "magic_name": "fixture"}],
    }
""",
        encoding="utf-8",
    )
    existing = __import__("os").environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path) + ((":" + existing) if existing else ""))
    expected = KernelAdapterSpec(
        plugin_id="fixture_provider",
        plugin_version="1.2.3",
        module="fixture_bad_adapter",
        families=(("fixture", "fixture"),),
    )
    with pytest.raises(KernelAdapterError) as captured:
        jupyter_kernel.ManagedJupyterKernelFactory().start(
            "python3", timeout=8, adapters=(expected,),
        )
    assert captured.value.reason == "conflict"
    assert captured.value.details["expected"]["fixture_bad_adapter"]["plugin_id"] == "fixture_provider"
    assert captured.value.details["observed"]["fixture_bad_adapter"]["plugin_id"] == "wrong_provider"


def test_real_kernel_input_wait_is_unbounded_and_explicit_interrupt_still_works(monkeypatch, tmp_path):
    import concurrent.futures
    import threading
    import time

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("IPYTHONDIR", str(tmp_path / "ipython"))
    kernel = jupyter_kernel.ManagedJupyterKernelFactory().start("python3", timeout=8)
    requests = queue.Queue()
    outputs = []
    timeline = []

    def on_output(output):
        outputs.append(output)
        timeline.append(output.data)

    def on_input(*request):
        timeline.append(request[1])
        requests.put(request)
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(
            kernel.execute, "print('before'); input('wait: ')", timeout=1.0, on_output=on_output,
            on_input=on_input,
        )
        request_id, prompt, password = requests.get(timeout=3)
        assert prompt == "wait: " and password is False
        assert "".join(timeline) == "before\nwait: "
        # Wait longer than the execution budget; this is deliberate user think time.
        assert not threading.Event().wait(1.2)
        assert not future.done()
        kernel.submit_input(request_id, "literal reply")
        assert future.result(timeout=3).outcome == "succeeded"
        assert any(output.data == "'literal reply'" for output in outputs)
        with pytest.raises(KernelAdapterError, match="no longer pending"):
            kernel.submit_input(request_id, "duplicate")

        # Advance only the adapter clock, proving that neither active work nor
        # an input wait expires after the former 10/300-second limits.
        from types import SimpleNamespace
        elapsed = [0.0]
        monkeypatch.setattr(jupyter_kernel, "time", SimpleNamespace(monotonic=lambda: time.monotonic() + elapsed[0]))

        def advance_after_output(output):
            outputs.append(output)
            if "working" in output.data:
                elapsed[0] += 600

        future = executor.submit(
            kernel.execute, "import time; print('working', flush=True); time.sleep(0.2); input('unbounded: ')",
            timeout=None, on_output=advance_after_output, on_input=on_input,
        )
        request_id, _, _ = requests.get(timeout=3)
        elapsed[0] += 600
        assert not threading.Event().wait(0.15)
        assert not future.done()
        kernel.submit_input(request_id, "still here")
        assert future.result(timeout=3).outcome == "succeeded"

        future = executor.submit(
            kernel.execute, "input('wait forever: ')", timeout=None, on_output=outputs.append,
            on_input=on_input,
        )
        requests.get(timeout=3)
        assert not future.done()
        kernel.interrupt()
        assert future.result(timeout=3).outcome == "interrupted"
        assert kernel.execute("1 + 1", timeout=2, on_output=outputs.append,
                              on_input=lambda *request: None).outcome == "succeeded"
    finally:
        if kernel._executing:
            kernel.interrupt()
        executor.shutdown(wait=True)
        kernel.stop(timeout=3)


def test_completion_timeout_preserves_kernel_and_late_reply_cannot_supply_next_result() -> None:
    class CompletionClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.requests = []
            self.replies = []

        def complete(self, *, code, cursor_pos):
            assert code == "α" and cursor_pos == 1
            self.requests.append(code)
            return f"completion_{len(self.requests)}"

        def get_shell_msg(self, *, timeout):
            assert timeout > 0
            if not self.replies:
                raise queue.Empty
            return self.replies.pop(0)

    stderr_file = tempfile.NamedTemporaryFile(delete=False)
    client = CompletionClient()
    kernel = ManagedJupyterKernel(FakeManager(), client, stderr_file, stderr_file.name)
    try:
        with pytest.raises(KernelAdapterError) as timed_out:
            kernel.complete("α", timeout=0.1)
        assert timed_out.value.reason == "timeout" and timed_out.value.layer == "execution"
        for request_id, match in [("completion_1", "stale"), ("completion_2", "αvalue")]:
            client.replies.append({"header": {"msg_type": "complete_reply"},
                                   "parent_header": {"msg_id": request_id},
                                   "content": {"status": "ok", "matches": [match], "cursor_start": 0, "cursor_end": 1}})
        assert kernel.complete("α", timeout=0.1) == {"items": [{"text": "αvalue", "start": 0, "end": 1}]}
        assert kernel.execute("1 + 1", timeout=1, on_output=lambda _: None).outcome == "succeeded"
    finally:
        kernel.stop(timeout=1)


def test_liveness_check_uses_owned_process_with_inherited_running_event_loop():
    """asyncio.to_thread copies Jupyter's cached loop from the service context."""
    import asyncio
    import contextvars
    import subprocess
    import sys

    from jupyter_client import KernelManager
    from jupyter_client.provisioning.local_provisioner import LocalProvisioner
    from jupyter_core.utils import ensure_event_loop

    process = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"],
                               stdin=subprocess.PIPE)
    manager = KernelManager()
    provisioner = LocalProvisioner(parent=manager)
    provisioner.process = process
    provisioner.pid = process.pid
    manager.provisioner = provisioner
    stderr_file = tempfile.NamedTemporaryFile(delete=False)
    kernel = ManagedJupyterKernel(manager, FakeClient(), stderr_file, stderr_file.name)

    async def inspect_from_service():
        assert ensure_event_loop() is asyncio.get_running_loop()
        await asyncio.wait_for(asyncio.to_thread(kernel.check_alive), timeout=3)

    try:
        contextvars.Context().run(asyncio.run, inspect_from_service())
        assert process.poll() is None
        process.terminate()
        process.wait(timeout=3)
        with pytest.raises(KernelAdapterError) as captured:
            contextvars.Context().run(asyncio.run, inspect_from_service())
        assert captured.value.reason == "kernel_died"
        assert captured.value.diagnostics.signal == 15
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
        process.stdin.close()
        kernel._close_resources()
