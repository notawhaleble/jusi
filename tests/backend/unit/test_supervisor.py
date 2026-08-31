from __future__ import annotations

import pytest

from jusi.application.ports import (
    KernelAdapterError,
    KernelExecutionResult,
    KernelOutput,
    PluginCatalogDiscoveryError,
    PluginCatalogDiscoveryResult,
)
from jusi.application.supervisor import Supervisor, SupervisorError
from jusi.domain.models import ProcessDiagnostics
from jusi.protocol import validate_event


class FakeKernel:
    pid = 4321

    def __init__(
        self,
        result: KernelExecutionResult | None = None,
        stop_error: KernelAdapterError | None = None,
    ) -> None:
        self.result = result or KernelExecutionResult(
            "succeeded",
            (KernelOutput("result", "text/plain", "2"),),
        )
        self.stopped = False
        self.stop_error = stop_error

    def execute(self, code: str, *, timeout: float) -> KernelExecutionResult:
        assert code
        assert timeout > 0
        return self.result

    def stop(self, *, timeout: float) -> None:
        assert timeout > 0
        if self.stop_error is not None:
            raise self.stop_error
        self.stopped = True


class FakeFactory:
    def __init__(self, kernel: FakeKernel | None = None, error: KernelAdapterError | None = None) -> None:
        self.kernel = kernel or FakeKernel()
        self.error = error
        self.start_count = 0

    def start(self, kernel_name: str, *, timeout: float) -> FakeKernel:
        assert kernel_name == "python3"
        assert timeout > 0
        self.start_count += 1
        if self.error is not None:
            raise self.error
        return self.kernel


class SequenceFactory(FakeFactory):
    def __init__(self, kernels: list[FakeKernel]) -> None:
        self.kernels = kernels
        self.index = 0
        super().__init__(kernels[0])

    def start(self, kernel_name: str, *, timeout: float) -> FakeKernel:
        assert kernel_name == "python3"
        assert timeout > 0
        kernel = self.kernels[self.index]
        self.index += 1
        return kernel


class FakeDiscovery:
    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.calls: list[str] = []
        self.fail_on_call = fail_on_call

    def discover(self, *, discovery_id: str, timeout: float) -> PluginCatalogDiscoveryResult:
        assert timeout > 0
        self.calls.append(discovery_id)
        if self.fail_on_call == len(self.calls):
            raise PluginCatalogDiscoveryError(
                "broken plugin",
                reason="plugin_error",
                retryable=True,
                entry_point="broken",
                distribution="jusi-broken",
            )
        return PluginCatalogDiscoveryResult(
            catalog={
                "protocol_version": 1,
                "catalog_version": 1,
                "discovery_id": discovery_id,
                "plugins": [],
            },
            process=ProcessDiagnostics(pid=9000 + len(self.calls), exit_code=0),
        )


def make_supervisor(factory: FakeFactory, discovery: FakeDiscovery | None = None) -> Supervisor:
    return Supervisor(factory, discovery or FakeDiscovery())


def test_walking_skeleton_event_order_and_idempotent_stop() -> None:
    kernel = FakeKernel()
    supervisor = make_supervisor(FakeFactory(kernel))

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
    for event in events:
        validate_event(event)
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


def test_health_is_an_authoritative_snapshot_with_replay_window() -> None:
    supervisor = make_supervisor(FakeFactory())
    initial = supervisor.health()
    assert initial["supervisor_id"] == supervisor.supervisor_id
    assert initial["earliest_event_sequence"] == 1
    assert initial["event_sequence"] == 1
    assert initial["kernel"] is None

    started = supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")
    current = supervisor.health()
    assert current["earliest_event_sequence"] == 1
    assert current["event_sequence"] == 4
    assert current["kernel"] == started["kernel"]


def test_execution_error_is_local_and_kernel_remains_on() -> None:
    kernel = FakeKernel(KernelExecutionResult("failed", error_name="ValueError", error_value="bad value"))
    supervisor = make_supervisor(FakeFactory(kernel))
    started = supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")

    code = "raise ValueError('bad value')"
    result = supervisor.execute(
        kernel_id=started["kernel"]["kernel_id"],
        notebook_id="nb",
        cell_id="cell",
        code=code,
        trace_id="trace_execute",
    )

    assert result["execution"]["outcome"] == "failed"
    assert result["failure"]["layer"] == "execution"
    assert result["failure"]["details"] == {
        "code_bytes": len(code.encode("utf-8")),
        "code_line_count": 1,
        "error_name": "ValueError",
        "error_value": "bad value",
    }
    assert supervisor.health()["kernel"]["state"] == "on"


def test_start_failure_stays_off_and_is_typed() -> None:
    supervisor = make_supervisor(
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


def test_discovery_failure_prevents_kernel_start_and_publishes_no_runtime() -> None:
    factory = FakeFactory()
    supervisor = make_supervisor(factory, FakeDiscovery(fail_on_call=1))

    with pytest.raises(SupervisorError) as captured:
        supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")

    assert captured.value.failure.layer == "plugin_discovery"
    assert captured.value.failure.operation == "start_kernel"
    assert captured.value.failure.resource.kind == "plugin_discovery"
    assert captured.value.failure.details == {
        "entry_point": "broken",
        "distribution": "jusi-broken",
    }
    assert factory.start_count == 0
    assert supervisor.health()["kernel"] is None
    assert supervisor.health()["runtime"] is None


def test_observed_kernel_death_turns_kernel_off_and_attempts_cleanup() -> None:
    class DeadKernel(FakeKernel):
        def execute(self, code: str, *, timeout: float) -> KernelExecutionResult:
            raise KernelAdapterError(
                "kernel exited",
                layer="kernel",
                reason="kernel_died",
                retryable=True,
                diagnostics=ProcessDiagnostics(pid=4321, signal=9, stderr_excerpt="fatal\n"),
            )

    kernel = DeadKernel()
    supervisor = make_supervisor(FakeFactory(kernel))
    started = supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")

    code = "α\n" + "body" * 100
    with pytest.raises(SupervisorError) as captured:
        supervisor.execute(
            kernel_id=started["kernel"]["kernel_id"],
            notebook_id="nb",
            cell_id="cell",
            code=code,
            trace_id="trace_execute",
        )

    assert captured.value.failure.reason == "kernel_died"
    assert captured.value.failure.process == ProcessDiagnostics(pid=4321, signal=9, stderr_excerpt="fatal\n")
    assert captured.value.failure.details == {
        "code_bytes": len(code.encode("utf-8")),
        "code_line_count": 2,
    }
    assert supervisor.health()["kernel"]["state"] == "off"
    assert kernel.stopped
    death_events = supervisor.events.events_after(0)
    for event in death_events:
        validate_event(event)
    failures = [event for event in death_events if event["kind"] == "failure.occurred"]
    assert [event["payload"]["layer"] for event in failures] == ["kernel", "execution"]
    assert failures[1]["payload"]["caused_by_failure_id"] == failures[0]["payload"]["failure_id"]
    failure_index = death_events.index(failures[0])
    execution_failure_index = death_events.index(failures[1])
    off_index = next(
        index
        for index, event in enumerate(death_events)
        if event["kind"] == "kernel.state_changed" and event["payload"]["state"] == "off"
    )
    assert failure_index < execution_failure_index < off_index
    assert death_events[failure_index]["payload"]["process"]["signal"] == 9
    assert death_events[failure_index]["payload"]["details"]["code_bytes"] == len(code.encode("utf-8"))


def test_full_restart_replaces_runtime_discovery_kernel_and_notebook_identity() -> None:
    old_kernel = FakeKernel()
    new_kernel = FakeKernel()
    discovery = FakeDiscovery()
    supervisor = make_supervisor(SequenceFactory([old_kernel, new_kernel]), discovery)
    started = supervisor.start_kernel(notebook_id="nb_old", kernel_name="python3", trace_id="trace_start")

    restarted = supervisor.restart_notebook(
        runtime_id=started["runtime"]["runtime_id"],
        kernel_id=started["kernel"]["kernel_id"],
        notebook_id="nb_old",
        next_notebook_id="nb_new",
        kernel_name="python3",
        trace_id="trace_restart",
    )

    assert old_kernel.stopped is True
    assert new_kernel.stopped is False
    assert restarted["cleanup"]["result"] == "stopped"
    assert restarted["runtime"]["runtime_id"] != started["runtime"]["runtime_id"]
    assert restarted["runtime"]["discovery_id"] != started["runtime"]["discovery_id"]
    assert restarted["kernel"]["kernel_id"] != started["kernel"]["kernel_id"]
    assert restarted["runtime"]["notebook_id"] == "nb_new"
    assert restarted["runtime"]["plugin_catalog"]["discovery_id"] == restarted["runtime"]["discovery_id"]
    assert len(discovery.calls) == 2
    assert supervisor.inspect_kernel(started["kernel"]["kernel_id"])["state"] == "off"
    assert supervisor.health()["runtime"] == restarted["runtime"]

    restart_events = [event for event in supervisor.events.events_after(0) if event["trace_id"] == "trace_restart"]
    for event in restart_events:
        validate_event(event)
    assert [event["kind"] for event in restart_events] == [
        "operation.started",
        "kernel.state_changed",
        "kernel.state_changed",
        "operation.completed",
    ]
    assert [event["payload"]["state"] for event in restart_events if event["kind"] == "kernel.state_changed"] == ["off", "on"]


def test_restart_discovery_failure_after_teardown_leaves_kernel_off_without_stale_runtime() -> None:
    old_kernel = FakeKernel()
    discovery = FakeDiscovery(fail_on_call=2)
    supervisor = make_supervisor(SequenceFactory([old_kernel, FakeKernel()]), discovery)
    started = supervisor.start_kernel(notebook_id="nb_old", kernel_name="python3", trace_id="trace_start")

    with pytest.raises(SupervisorError) as captured:
        supervisor.restart_notebook(
            runtime_id=started["runtime"]["runtime_id"],
            kernel_id=started["kernel"]["kernel_id"],
            notebook_id="nb_old",
            next_notebook_id="nb_new",
            kernel_name="python3",
            trace_id="trace_restart",
        )

    assert old_kernel.stopped is True
    assert captured.value.failure.layer == "plugin_discovery"
    assert captured.value.failure.scope == "plugin_discovery"
    assert captured.value.failure.details == {
        "teardown_completed": True,
        "next_notebook_id": "nb_new",
        "entry_point": "broken",
        "distribution": "jusi-broken",
    }
    assert supervisor.health()["kernel"]["state"] == "off"
    assert supervisor.health()["runtime"] is None


def test_restart_teardown_failure_preserves_current_runtime_and_kernel() -> None:
    stop_error = KernelAdapterError(
        "cannot stop",
        layer="kernel",
        reason="cleanup_incomplete",
        retryable=True,
    )
    old_kernel = FakeKernel(stop_error=stop_error)
    discovery = FakeDiscovery()
    supervisor = make_supervisor(SequenceFactory([old_kernel, FakeKernel()]), discovery)
    started = supervisor.start_kernel(notebook_id="nb_old", kernel_name="python3", trace_id="trace_start")

    with pytest.raises(SupervisorError) as captured:
        supervisor.restart_notebook(
            runtime_id=started["runtime"]["runtime_id"],
            kernel_id=started["kernel"]["kernel_id"],
            notebook_id="nb_old",
            next_notebook_id="nb_new",
            kernel_name="python3",
            trace_id="trace_restart",
        )

    assert captured.value.failure.details["teardown_completed"] is False
    assert len(discovery.calls) == 1
    assert supervisor.health()["kernel"]["state"] == "on"
    assert supervisor.health()["runtime"] == started["runtime"]
