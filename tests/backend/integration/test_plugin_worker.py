from __future__ import annotations

import os
from pathlib import Path
import signal
import sys
import textwrap

import pytest

from jusi.application.ports import PluginWorkerError, PluginWorkerSpec
from jusi.infrastructure.plugin_worker import FreshProcessPluginWorkerFactory


def write_worker(root: Path, source: str) -> str:
    module_name = "fixture_exact_worker"
    (root / f"{module_name}.py").write_text(textwrap.dedent(source), encoding="utf-8")
    return f"{module_name}:create_worker"


def spec(entry_point: str, suffix: str = "one") -> PluginWorkerSpec:
    return PluginWorkerSpec(
        plugin_worker_id=f"pwrk_{suffix}",
        runtime_id=f"runtime_{suffix}",
        plugin_id="fixture_plugin",
        family_id="fixture_family",
        client_id=f"client_{suffix}",
        execution_id=f"execution_{suffix}",
        entry_point=entry_point,
    )


def factory(root: Path, **kwargs: object) -> FreshProcessPluginWorkerFactory:
    return FreshProcessPluginWorkerFactory(search_paths=[root], **kwargs)


def test_worker_is_fresh_isolated_and_stdout_cannot_corrupt_control(tmp_path: Path) -> None:
    entry_point = write_worker(tmp_path, """
        import os
        print("import stdout noise")

        class Worker:
            def __init__(self, context):
                self.context = context

            def handle(self, operation, payload):
                print("handler stdout with ansi: \\x1b[31mred\\x1b[0m")
                return {
                    "pid": os.getpid(),
                    "operation": operation,
                    "payload": payload,
                    "worker_id": self.context.plugin_worker_id,
                }

        def create_worker(context):
            return Worker(context)
    """)
    adapter = factory(tmp_path)
    first = adapter.start(spec(entry_point, "first"), timeout=2)
    first_result = first.request("complete", {"text": "sel"}, trace_id="trace_first", timeout=2)
    assert first_result.result["pid"] == first.pid
    assert first_result.result["worker_id"] == "pwrk_first"
    assert first_result.result["payload"] == {"text": "sel"}
    assert first.stop(trace_id="trace_stop", timeout=2) == "stopped"
    assert first.stop(trace_id="trace_stop_again", timeout=2) == "already_absent"

    second = adapter.start(spec(entry_point, "second"), timeout=2)
    second_result = second.request("followup", {}, trace_id="trace_second", timeout=2)
    assert second_result.result["pid"] != first_result.result["pid"]
    assert "fixture_exact_worker" not in sys.modules
    assert second.stop(trace_id="trace_stop_second", timeout=2) == "stopped"


def test_worker_sdk_returns_typed_private_terminal_surface_request(tmp_path: Path) -> None:
    entry_point = write_worker(tmp_path, """
        from jusi.plugin_api import WorkerResult, terminal_surface

        class Worker:
            def handle(self, operation, payload):
                return WorkerResult(
                    result={"accepted": True},
                    core_requests=(terminal_surface(
                        "terminal_main",
                        ("visidata", "--play", "/private/query.vd"),
                        cwd="/target/work",
                        environment_overrides={"TERM": "xterm-256color"},
                        signal=True,
                    ),),
                )

        def create_worker(context):
            return Worker()
    """)
    worker = factory(tmp_path).start(spec(entry_point, "surface"), timeout=2)
    result = worker.request("execute", {}, trace_id="trace_surface", timeout=2)
    assert result.result == {"accepted": True}
    assert len(result.core_requests) == 1
    request = result.core_requests[0]
    assert request.argv == ("visidata", "--play", "/private/query.vd")
    assert request.cwd == "/target/work"
    assert request.capabilities == ("input", "resize", "signal")
    assert worker.stop(trace_id="trace_stop_surface", timeout=2) == "stopped"


def test_handler_failure_fences_only_the_worker_and_preserves_diagnostics(tmp_path: Path) -> None:
    entry_point = write_worker(tmp_path, """
        class Worker:
            def handle(self, operation, payload):
                print("before failure")
                raise RuntimeError("provider exploded")

        def create_worker(context):
            return Worker()
    """)
    worker = factory(tmp_path).start(spec(entry_point), timeout=2)

    with pytest.raises(PluginWorkerError) as raised:
        worker.request("execute", {"query": "select 1"}, trace_id="trace_failure", timeout=2)

    assert raised.value.reason == "plugin_error"
    assert raised.value.retryable is False
    assert raised.value.diagnostics is not None
    assert raised.value.diagnostics.pid == worker.pid
    assert "provider exploded" in raised.value.diagnostics.stderr_excerpt
    assert worker.stop(trace_id="trace_cleanup", timeout=2) == "already_absent"


def test_one_worker_failure_does_not_damage_another_worker(tmp_path: Path) -> None:
    entry_point = write_worker(tmp_path, """
        class Worker:
            def __init__(self, context):
                self.worker_id = context.plugin_worker_id

            def handle(self, operation, payload):
                if payload.get("crash"):
                    raise RuntimeError("owned failure")
                return {"worker_id": self.worker_id, "alive": True}

        def create_worker(context):
            return Worker(context)
    """)
    adapter = factory(tmp_path)
    failed = adapter.start(spec(entry_point, "failed"), timeout=2)
    survivor = adapter.start(spec(entry_point, "survivor"), timeout=2)

    with pytest.raises(PluginWorkerError):
        failed.request("execute", {"crash": True}, trace_id="trace_failed", timeout=2)

    assert survivor.request("execute", {}, trace_id="trace_survivor", timeout=2).result == {
        "worker_id": "pwrk_survivor",
        "alive": True,
    }
    assert survivor.stop(trace_id="trace_stop_survivor", timeout=2) == "stopped"


def test_request_timeout_terminates_worker_and_marks_side_effects_unknown(tmp_path: Path) -> None:
    entry_point = write_worker(tmp_path, """
        import time

        class Worker:
            def handle(self, operation, payload):
                time.sleep(10)
                return {}

        def create_worker(context):
            return Worker()
    """)
    worker = factory(tmp_path).start(spec(entry_point), timeout=2)

    with pytest.raises(PluginWorkerError) as raised:
        worker.request("execute", {}, trace_id="trace_timeout", timeout=0.1)

    assert raised.value.reason == "timeout"
    assert raised.value.retryable is False
    assert raised.value.details == {"side_effects_may_have_occurred": True}
    assert worker.stop(trace_id="trace_cleanup", timeout=2) == "already_absent"


def test_start_failure_and_signal_preserve_process_diagnostics(tmp_path: Path) -> None:
    broken = write_worker(tmp_path, "raise RuntimeError('broken worker import')\n")
    with pytest.raises(PluginWorkerError) as import_error:
        factory(tmp_path).start(spec(broken, "broken"), timeout=2)
    assert import_error.value.reason == "process_exited"
    assert import_error.value.diagnostics is not None
    assert import_error.value.diagnostics.exit_code == 20
    assert "broken worker import" in import_error.value.diagnostics.stderr_excerpt

    if os.name != "nt":
        signalled = write_worker(tmp_path, """
            import os
            import signal
            os.kill(os.getpid(), signal.SIGKILL)
        """)
        with pytest.raises(PluginWorkerError) as signal_error:
            factory(tmp_path).start(spec(signalled, "signal"), timeout=2)
        assert signal_error.value.reason == "process_signalled"
        assert signal_error.value.diagnostics is not None
        assert signal_error.value.diagnostics.signal == signal.SIGKILL


def test_stderr_is_bounded_and_spawn_failure_is_typed(tmp_path: Path) -> None:
    entry_point = write_worker(tmp_path, """
        import os
        os.write(2, b'x' * 20000)
        raise RuntimeError('after noise')
    """)
    with pytest.raises(PluginWorkerError) as raised:
        factory(tmp_path, stderr_limit=128).start(spec(entry_point), timeout=2)
    diagnostics = raised.value.diagnostics
    assert diagnostics is not None
    assert diagnostics.stderr_truncated is True
    assert len(diagnostics.stderr_excerpt.encode("utf-8")) <= 128

    with pytest.raises(PluginWorkerError) as spawn_error:
        FreshProcessPluginWorkerFactory(python_executable=os.fspath(tmp_path / "missing-python")).start(
            spec(entry_point, "spawn"), timeout=2,
        )
    assert spawn_error.value.reason == "spawn_failed"
    assert spawn_error.value.retryable is True


def test_non_json_and_oversized_results_fail_inside_owned_worker(tmp_path: Path) -> None:
    invalid = write_worker(tmp_path, """
        class Worker:
            def handle(self, operation, payload):
                return {"bad": object()}

        def create_worker(context):
            return Worker()
    """)
    worker = factory(tmp_path).start(spec(invalid, "invalid"), timeout=2)
    with pytest.raises(PluginWorkerError) as invalid_error:
        worker.request("complete", {}, trace_id="trace_invalid", timeout=2)
    assert invalid_error.value.reason == "plugin_error"

    oversized = write_worker(tmp_path, """
        class Worker:
            def handle(self, operation, payload):
                return {"value": "x" * 4096}

        def create_worker(context):
            return Worker()
    """)
    large_worker = factory(tmp_path, frame_limit=512).start(spec(oversized, "large"), timeout=2)
    with pytest.raises(PluginWorkerError) as size_error:
        large_worker.request("complete", {}, trace_id="trace_large", timeout=2)
    assert size_error.value.reason == "plugin_error"
