from __future__ import annotations

import pytest

from jusi.application.ports import (
    KernelAdapterError,
    KernelExecutionResult,
    KernelOutput,
)
from jusi.application.supervisor import Supervisor, SupervisorError


class FakeKernel:
    pid = 4321

    def __init__(self, result: KernelExecutionResult | None = None) -> None:
        self.result = result or KernelExecutionResult(
            "succeeded",
            (KernelOutput("result", "text/plain", "2"),),
        )
        self.stopped = False

    def execute(self, code: str, *, timeout: float) -> KernelExecutionResult:
        assert code
        assert timeout > 0
        return self.result

    def stop(self, *, timeout: float) -> None:
        assert timeout > 0
        self.stopped = True


class FakeFactory:
    def __init__(self, kernel: FakeKernel | None = None, error: KernelAdapterError | None = None) -> None:
        self.kernel = kernel or FakeKernel()
        self.error = error

    def start(self, kernel_name: str, *, timeout: float) -> FakeKernel:
        assert kernel_name == "python3"
        assert timeout > 0
        if self.error is not None:
            raise self.error
        return self.kernel


def test_walking_skeleton_event_order_and_idempotent_stop() -> None:
    kernel = FakeKernel()
    supervisor = Supervisor(FakeFactory(kernel))

    started = supervisor.start_kernel(
        notebook_id="nb_walk",
        kernel_name="python3",
        trace_id="trace_start",
    )
    kernel_id = started["kernel"]["kernel_id"]
    executed = supervisor.execute(
        kernel_id=kernel_id,
        notebook_id="nb_walk",
        cell_id="cell_walk",
        code="1 + 1",
        trace_id="trace_execute",
    )
    stopped = supervisor.stop_kernel(kernel_id=kernel_id, trace_id="trace_stop")
    repeated = supervisor.stop_kernel(kernel_id=kernel_id, trace_id="trace_stop_again")

    assert executed["execution"]["outcome"] == "succeeded"
    assert stopped["cleanup"]["result"] == "stopped"
    assert repeated["cleanup"]["result"] == "already_absent"
    assert kernel.stopped

    events = supervisor.events.events_after(0)
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert [event["kind"] for event in events[:10]] == [
        "service.ready",
        "operation.started",
        "kernel.state_changed",
        "operation.completed",
        "operation.started",
        "execution.started",
        "execution.output",
        "execution.completed",
        "operation.completed",
        "operation.started",
    ]
    output = next(event for event in events if event["kind"] == "execution.output")
    assert output["payload"]["data"] == "2"
    assert output["payload"]["media_type"] == "text/plain"


def test_execution_error_is_local_and_kernel_remains_on() -> None:
    kernel = FakeKernel(KernelExecutionResult("failed", error_name="ValueError", error_value="bad value"))
    supervisor = Supervisor(FakeFactory(kernel))
    started = supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")

    result = supervisor.execute(
        kernel_id=started["kernel"]["kernel_id"],
        notebook_id="nb",
        cell_id="cell",
        code="raise ValueError('bad value')",
        trace_id="trace_execute",
    )

    assert result["execution"]["outcome"] == "failed"
    assert result["failure"]["layer"] == "execution"
    assert supervisor.health()["kernel"]["state"] == "on"


def test_start_failure_stays_off_and_is_typed() -> None:
    supervisor = Supervisor(
        FakeFactory(
            error=KernelAdapterError(
                "no kernelspec",
                layer="kernel",
                reason="spawn_failed",
                retryable=False,
            )
        )
    )

    with pytest.raises(SupervisorError) as captured:
        supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")

    assert captured.value.failure.layer == "kernel"
    assert captured.value.failure.reason == "spawn_failed"
    assert supervisor.health()["kernel"] is None


def test_observed_kernel_death_turns_kernel_off_and_attempts_cleanup() -> None:
    class DeadKernel(FakeKernel):
        def execute(self, code: str, *, timeout: float) -> KernelExecutionResult:
            raise KernelAdapterError(
                "kernel exited",
                layer="kernel",
                reason="kernel_died",
                retryable=True,
            )

    kernel = DeadKernel()
    supervisor = Supervisor(FakeFactory(kernel))
    started = supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")

    with pytest.raises(SupervisorError) as captured:
        supervisor.execute(
            kernel_id=started["kernel"]["kernel_id"],
            notebook_id="nb",
            cell_id="cell",
            code="1 + 1",
            trace_id="trace_execute",
        )

    assert captured.value.failure.reason == "kernel_died"
    assert supervisor.health()["kernel"]["state"] == "off"
    assert kernel.stopped
