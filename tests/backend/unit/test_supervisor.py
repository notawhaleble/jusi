from __future__ import annotations

import json
import threading

import pytest

from jusi.application.ports import (
    KernelAdapterError,
    KernelExecutionResult,
    KernelOutput,
    PluginCatalogDiscoveryError,
    PluginCatalogDiscoveryResult,
    PluginWorkerError,
    PluginWorkerOperationResult,
    PluginWorkerSpec,
    PluginHandoff,
    RuntimeConfigurationError,
    TerminalSurfaceRequest,
)
from jusi.application.plugin_workers import PluginWorkerManager
from jusi.application.supervisor import Supervisor, SupervisorError
from jusi.application.terminal_surfaces import TerminalSurfaceError
from jusi.domain.models import ProcessDiagnostics
from jusi.protocol import validate_event, validate_health_response


class FakeKernel:
    pid = 4321

    def __init__(
        self,
        result: KernelExecutionResult | None = None,
        stop_error: KernelAdapterError | None = None,
        outputs: tuple[KernelOutput, ...] | None = None,
    ) -> None:
        self.result = result or KernelExecutionResult("succeeded")
        self.outputs = (
            (KernelOutput("result", "text/plain", "2"),)
            if outputs is None
            else outputs
        )
        self.stopped = False
        self.stop_error = stop_error
        self.interrupt_count = 0

    def execute(self, code: str, *, timeout: float, on_output, on_input=None) -> KernelExecutionResult:  # type: ignore[no-untyped-def]
        assert code
        assert timeout is None or timeout > 0
        for output in self.outputs:
            on_output(output)
        return self.result

    def stop(self, *, timeout: float) -> None:
        assert timeout > 0
        if self.stop_error is not None:
            raise self.stop_error
        self.stopped = True

    def interrupt(self) -> None:
        self.interrupt_count += 1


class FakeFactory:
    def __init__(self, kernel: FakeKernel | None = None, error: KernelAdapterError | None = None) -> None:
        self.kernel = kernel or FakeKernel()
        self.error = error
        self.start_count = 0
        self.configurations: list[dict] = []

    def start(self, kernel_name: str, *, timeout: float, adapters=(), configuration=None) -> FakeKernel:  # type: ignore[no-untyped-def]
        assert kernel_name == "python3"
        assert timeout > 0
        self.start_count += 1
        self.adapters = adapters
        self.configurations.append(dict(configuration or {}))
        if self.error is not None:
            raise self.error
        return self.kernel


class SequenceFactory(FakeFactory):
    def __init__(self, kernels: list[FakeKernel]) -> None:
        self.kernels = kernels
        self.index = 0
        super().__init__(kernels[0])

    def start(self, kernel_name: str, *, timeout: float, adapters=(), configuration=None) -> FakeKernel:  # type: ignore[no-untyped-def]
        assert kernel_name == "python3"
        assert timeout > 0
        kernel = self.kernels[self.index]
        self.index += 1
        self.adapters = adapters
        self.configurations.append(dict(configuration or {}))
        return kernel


class FakeDiscovery:
    def __init__(self, *, fail_on_call: int | None = None, plugins: list[dict] | None = None) -> None:
        self.calls: list[str] = []
        self.fail_on_call = fail_on_call
        self.plugins = plugins or []

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
                "plugins": self.plugins,
            },
            process=ProcessDiagnostics(pid=9000 + len(self.calls), exit_code=0),
        )


class SequenceConfiguration:
    def __init__(self, values: list[dict]) -> None:
        self.values = values
        self.calls = 0

    def load(self) -> dict:
        value = self.values[self.calls]
        self.calls += 1
        return value


def make_supervisor(factory: FakeFactory, discovery: FakeDiscovery | None = None) -> Supervisor:
    return Supervisor(factory, discovery or FakeDiscovery())


def test_interrupt_bypasses_execution_lane_and_preserves_kernel() -> None:
    class BlockingKernel(FakeKernel):
        def __init__(self) -> None:
            super().__init__(KernelExecutionResult("interrupted", "KeyboardInterrupt", ""), outputs=())
            self.executing = threading.Event()
            self.released = threading.Event()

        def execute(self, code: str, *, timeout: float, on_output, on_input=None) -> KernelExecutionResult:  # type: ignore[no-untyped-def]
            self.executing.set()
            assert self.released.wait(2), "interrupt did not reach the active execution"
            return self.result

        def interrupt(self) -> None:
            super().interrupt()

    kernel = BlockingKernel()
    supervisor = make_supervisor(FakeFactory(kernel))
    started = supervisor.start_kernel(notebook_id="nb_test", kernel_name="python3", trace_id="trace_start")
    result: list[dict] = []
    execution_thread = threading.Thread(target=lambda: result.append(supervisor.execute(
        kernel_id=started["kernel"]["kernel_id"], notebook_id="nb_test", cell_id="cell_test",
        code="slow()", trace_id="trace_execute", timeout=2,
    )))
    execution_thread.start()
    assert kernel.executing.wait(1)
    execution_started = next(
        event for event in supervisor.events.events_after(0)
        if event["kind"] == "execution.started"
    )

    interrupted = supervisor.interrupt_execution(
        kernel_id=started["kernel"]["kernel_id"],
        execution_id=execution_started["payload"]["execution_id"],
        trace_id="trace_interrupt",
    )
    repeated = supervisor.interrupt_execution(
        kernel_id=started["kernel"]["kernel_id"],
        execution_id=execution_started["payload"]["execution_id"],
        trace_id="trace_interrupt_again",
    )
    kernel.released.set()
    execution_thread.join(2)

    assert not execution_thread.is_alive()
    assert interrupted["interrupt"]["result"] == "requested"
    assert repeated["interrupt"]["result"] == "already_requested"
    assert kernel.interrupt_count == 1
    assert result[0]["execution"]["outcome"] == "interrupted"
    assert supervisor.health()["kernel"]["state"] == "on"
    relevant = [
        (event["kind"], event["operation"])
        for event in supervisor.events.events_after(0)
        if event["trace_id"] in {"trace_execute", "trace_interrupt"}
    ]
    assert relevant[-1] == ("operation.completed", "execute")


class FakePluginWorkerHandle:
    pid = 8765

    def __init__(
        self,
        stop_error: PluginWorkerError | None = None,
        request_error: PluginWorkerError | None = None,
        operation_result: PluginWorkerOperationResult | None = None,
    ) -> None:
        self.stop_error = stop_error
        self.request_error = request_error
        self.operation_result = operation_result or PluginWorkerOperationResult({"accepted": True})
        self.stop_count = 0

    def request(self, operation: str, payload: dict, *, trace_id: str, timeout: float, request_id=None) -> PluginWorkerOperationResult:
        if self.request_error is not None:
            raise self.request_error
        return self.operation_result

    def stop(self, *, trace_id: str, timeout: float) -> str:
        self.stop_count += 1
        if self.stop_error is not None:
            raise self.stop_error
        return "stopped"


class FakePluginWorkerFactory:
    def __init__(
        self,
        stop_error: PluginWorkerError | None = None,
        request_error: PluginWorkerError | None = None,
        start_error: PluginWorkerError | None = None,
        operation_result: PluginWorkerOperationResult | None = None,
    ) -> None:
        self.stop_error = stop_error
        self.request_error = request_error
        self.start_error = start_error
        self.operation_result = operation_result
        self.specs: list[PluginWorkerSpec] = []
        self.handles: list[FakePluginWorkerHandle] = []

    def start(self, spec: PluginWorkerSpec, *, timeout: float) -> FakePluginWorkerHandle:
        self.specs.append(spec)
        if self.start_error is not None:
            raise self.start_error
        handle = FakePluginWorkerHandle(self.stop_error, self.request_error, self.operation_result)
        self.handles.append(handle)
        return handle


class FakeTerminalSurfaces:
    def __init__(self) -> None:
        self.prepared: dict[str, tuple[object, object]] = {}
        self.closed: list[str] = []
        self.on_fatal = None
        self.detached: list[tuple[str, str]] = []

    def set_fatal_handler(self, handler) -> None:  # type: ignore[no-untyped-def]
        self.on_fatal = handler

    def prepare(self, resource: object, request: object) -> None:
        self.prepared[resource.surface_id] = (resource, request)  # type: ignore[attr-defined]

    def close(self, surface_id: str, *, timeout: float) -> dict:
        self.closed.append(surface_id)
        return {"surface_id": surface_id, "result": "stopped"}

    def detach(self, surface_id: str, attachment_id: str) -> None:
        self.detached.append((surface_id, attachment_id))


def plugin_entry() -> dict:
    return {
        "plugin_id": "exact_sql",
        "plugin_version": "1.0.0",
        "distribution": "jusi-exact-sql",
        "families": [{
            "family_id": "sql",
            "magic_name": "sql",
            "capabilities": ["execute", "complete"],
        }],
        "kernel_extensions": [],
        "worker_entry_point": "fixture.worker:create_worker",
        "media_types": ["text/plain"],
        "interaction": "request_response",
    }


def interactive_plugin_entry() -> dict:
    entry = plugin_entry()
    entry["interaction"] = "terminal_interactive"
    return entry


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


def test_health_does_not_wait_for_slow_kernel_start_io() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingFactory(FakeFactory):
        def start(self, kernel_name: str, *, timeout: float, adapters=(), configuration=None) -> FakeKernel:  # type: ignore[no-untyped-def]
            entered.set()
            assert release.wait(timeout=2)
            return super().start(
                kernel_name, timeout=timeout, adapters=adapters, configuration=configuration,
            )

    supervisor = make_supervisor(BlockingFactory())
    start_errors: list[BaseException] = []

    def start() -> None:
        try:
            supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")
        except BaseException as exc:  # pragma: no cover - asserted below
            start_errors.append(exc)

    start_thread = threading.Thread(target=start)
    start_thread.start()
    assert entered.wait(timeout=1)

    health_result: list[dict] = []
    health_thread = threading.Thread(target=lambda: health_result.append(supervisor.health()))
    health_thread.start()
    health_thread.join(timeout=0.5)
    try:
        assert not health_thread.is_alive()
        assert health_result[0]["kernel"] is None
        assert health_result[0]["runtime"] is None
    finally:
        release.set()
        start_thread.join(timeout=2)
        health_thread.join(timeout=2)
    assert not start_thread.is_alive()
    assert start_errors == []


def test_kernel_stop_cleans_runtime_owned_plugin_workers() -> None:
    kernel = FakeKernel()
    worker_factory = FakePluginWorkerFactory()
    workers = PluginWorkerManager(worker_factory)
    supervisor = Supervisor(FakeFactory(kernel), FakeDiscovery(plugins=[plugin_entry()]), workers)
    started = supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")
    worker = workers.start(
        runtime_id=started["runtime"]["runtime_id"],
        plugin_id="exact_sql",
        family_id="sql",
        client_id="client_one",
        execution_id="execution_one",
        timeout=2,
    )

    stopped = supervisor.stop_kernel(
        kernel_id=started["kernel"]["kernel_id"], trace_id="trace_stop", timeout=2,
    )

    assert stopped["kernel"]["state"] == "off"
    assert stopped["cleanup"]["plugin_workers"] == [
        {"plugin_worker_id": worker.plugin_worker_id, "result": "stopped"},
    ]
    assert worker_factory.handles[0].stop_count == 1


def test_full_restart_cleans_old_workers_before_publishing_fresh_runtime() -> None:
    kernels = [FakeKernel(), FakeKernel()]
    worker_factory = FakePluginWorkerFactory()
    workers = PluginWorkerManager(worker_factory)
    supervisor = Supervisor(SequenceFactory(kernels), FakeDiscovery(plugins=[plugin_entry()]), workers)
    started = supervisor.start_kernel(notebook_id="nb_old", kernel_name="python3", trace_id="trace_start")
    old_worker = workers.start(
        runtime_id=started["runtime"]["runtime_id"], plugin_id="exact_sql", family_id="sql",
        client_id="client_old", execution_id="execution_old", timeout=2,
    )

    restarted = supervisor.restart_notebook(
        runtime_id=started["runtime"]["runtime_id"],
        kernel_id=started["kernel"]["kernel_id"],
        notebook_id="nb_old",
        next_notebook_id="nb_new",
        kernel_name="python3",
        trace_id="trace_restart",
        timeout=2,
    )
    new_worker = workers.start(
        runtime_id=restarted["runtime"]["runtime_id"], plugin_id="exact_sql", family_id="sql",
        client_id="client_new", execution_id="execution_new", timeout=2,
    )

    assert restarted["runtime"]["runtime_id"] != started["runtime"]["runtime_id"]
    assert restarted["cleanup"]["plugin_workers"] == [
        {"plugin_worker_id": old_worker.plugin_worker_id, "result": "stopped"},
    ]
    assert new_worker.plugin_worker_id != old_worker.plugin_worker_id
    assert worker_factory.handles[0].stop_count == 1


def test_incomplete_worker_cleanup_prevents_restart_without_stopping_kernel() -> None:
    kernel_factory = FakeFactory()
    cleanup_error = PluginWorkerError(
        "worker would not stop", reason="cleanup_incomplete", retryable=True,
        diagnostics=ProcessDiagnostics(pid=8765),
    )
    worker_factory = FakePluginWorkerFactory(cleanup_error)
    workers = PluginWorkerManager(worker_factory)
    supervisor = Supervisor(kernel_factory, FakeDiscovery(plugins=[plugin_entry()]), workers)
    started = supervisor.start_kernel(notebook_id="nb_old", kernel_name="python3", trace_id="trace_start")
    worker = workers.start(
        runtime_id=started["runtime"]["runtime_id"], plugin_id="exact_sql", family_id="sql",
        client_id="client_old", execution_id="execution_old", timeout=2,
    )

    with pytest.raises(SupervisorError) as raised:
        supervisor.restart_notebook(
            runtime_id=started["runtime"]["runtime_id"], kernel_id=started["kernel"]["kernel_id"],
            notebook_id="nb_old", next_notebook_id="nb_new", kernel_name="python3",
            trace_id="trace_restart", timeout=2,
        )

    assert raised.value.failure.layer == "plugin_worker"
    assert raised.value.failure.reason == "cleanup_incomplete"
    assert raised.value.failure.resource.resource_id == worker.plugin_worker_id
    assert raised.value.failure.details["teardown_completed"] is False
    assert supervisor.health()["kernel"]["state"] == "on"
    assert kernel_factory.start_count == 1


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


def test_plugin_handoff_must_match_the_current_runtime_catalog() -> None:
    valid_handoff = PluginHandoff(
        plugin_id="exact_sql", plugin_version="1.0.0",
        family_id="sql", magic_name="sql", payload={"query": "select 1"},
    )
    valid_kernel = FakeKernel(KernelExecutionResult("succeeded", handoffs=(valid_handoff,)))
    worker_factory = FakePluginWorkerFactory()
    supervisor = Supervisor(
        FakeFactory(valid_kernel),
        FakeDiscovery(plugins=[plugin_entry()]),
        PluginWorkerManager(worker_factory),
    )
    started = supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")
    result = supervisor.execute(
        kernel_id=started["kernel"]["kernel_id"], notebook_id="nb", cell_id="cell",
        code="%%sql", trace_id="trace_execute",
    )
    assert result["execution"]["outcome"] == "succeeded"
    assert result["execution"]["client_id"].startswith("cli_")
    assert worker_factory.specs[0].client_id == result["execution"]["client_id"]
    assert worker_factory.handles[0].stop_count == 0
    assert supervisor.health()["clients"][0]["plugin_id"] == "exact_sql"
    validate_health_response({"ok": True, **supervisor.health()})

    mismatched = PluginHandoff(
        plugin_id="other_sql", plugin_version="1.0.0",
        family_id="sql", magic_name="sql", payload={},
    )
    supervisor._kernel_handle = FakeKernel(KernelExecutionResult("succeeded", handoffs=(mismatched,)))
    with pytest.raises(SupervisorError) as raised:
        supervisor.execute(
            kernel_id=started["kernel"]["kernel_id"], notebook_id="nb", cell_id="cell_two",
            code="%%sql", trace_id="trace_mismatch",
        )
    assert raised.value.status_code == 409
    assert raised.value.failure.layer == "protocol"
    assert raised.value.failure.scope == "execution"
    assert raised.value.failure.details["plugin_id"] == "other_sql"
    assert supervisor.health()["kernel"]["state"] == "on"


def test_start_passes_catalog_declared_kernel_adapters_to_the_factory() -> None:
    plugin = plugin_entry()
    plugin["kernel_extensions"] = ["fixture_sql.kernel"]
    factory = FakeFactory()
    supervisor = Supervisor(factory, FakeDiscovery(plugins=[plugin]))

    supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")

    assert len(factory.adapters) == 1
    adapter = factory.adapters[0]
    assert adapter.plugin_id == "exact_sql"
    assert adapter.plugin_version == "1.0.0"
    assert adapter.module == "fixture_sql.kernel"
    assert adapter.families == (("sql", "sql"),)


def test_fatal_plugin_worker_failure_closes_only_its_client() -> None:
    handoff = PluginHandoff(
        plugin_id="exact_sql", plugin_version="1.0.0",
        family_id="sql", magic_name="sql", payload={"query": "secret query"},
    )
    worker_error = PluginWorkerError(
        "provider process died",
        reason="process_exited",
        retryable=False,
        diagnostics=ProcessDiagnostics(pid=8765, exit_code=23, stderr_excerpt="fatal adapter error"),
    )
    worker_factory = FakePluginWorkerFactory(request_error=worker_error)
    supervisor = Supervisor(
        FakeFactory(FakeKernel(KernelExecutionResult("succeeded", handoffs=(handoff,)))),
        FakeDiscovery(plugins=[plugin_entry()]),
        PluginWorkerManager(worker_factory),
    )
    started = supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")

    with pytest.raises(SupervisorError) as raised:
        supervisor.execute(
            kernel_id=started["kernel"]["kernel_id"], notebook_id="nb", cell_id="cell",
            code="%%sql", trace_id="trace_execute",
        )

    failure = raised.value.failure
    assert failure.layer == "plugin_worker"
    assert failure.reason == "process_exited"
    assert failure.scope == "client"
    assert failure.process is not None and failure.process.exit_code == 23
    assert supervisor.health()["kernel"]["state"] == "on"
    assert supervisor.health()["clients"] == []
    events = supervisor.events.events_after(0)
    kinds = [event["kind"] for event in events]
    assert "failure.occurred" in kinds
    assert "client.created" not in kinds
    assert "client.closed" not in kinds
    assert "secret query" not in str(failure.to_dict())


def test_plugin_factory_failure_is_observable_without_creating_a_client() -> None:
    handoff = PluginHandoff(
        plugin_id="exact_sql", plugin_version="1.0.0",
        family_id="sql", magic_name="sql", payload={},
    )
    start_error = PluginWorkerError(
        "could not import exact worker",
        reason="spawn_failed",
        retryable=True,
        diagnostics=ProcessDiagnostics(pid=9911, exit_code=20, stderr_excerpt="ImportError: broken plugin"),
    )
    supervisor = Supervisor(
        FakeFactory(FakeKernel(KernelExecutionResult("succeeded", handoffs=(handoff,)))),
        FakeDiscovery(plugins=[plugin_entry()]),
        PluginWorkerManager(FakePluginWorkerFactory(start_error=start_error)),
    )
    started = supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")

    with pytest.raises(SupervisorError) as raised:
        supervisor.execute(
            kernel_id=started["kernel"]["kernel_id"], notebook_id="nb", cell_id="cell",
            code="%%sql", trace_id="trace_execute",
        )

    assert raised.value.failure.layer == "plugin_worker"
    assert raised.value.failure.reason == "spawn_failed"
    assert raised.value.failure.scope == "client"
    assert raised.value.failure.process is not None
    assert raised.value.failure.process.stderr_excerpt == "ImportError: broken plugin"
    assert supervisor.health()["kernel"]["state"] == "on"
    assert supervisor.health()["clients"] == []
    assert "client.created" not in [event["kind"] for event in supervisor.events.events_after(0)]


def test_kernel_stop_retires_durable_plugin_client_after_worker_cleanup() -> None:
    handoff = PluginHandoff(
        plugin_id="exact_sql", plugin_version="1.0.0",
        family_id="sql", magic_name="sql", payload={},
    )
    worker_factory = FakePluginWorkerFactory()
    supervisor = Supervisor(
        FakeFactory(FakeKernel(KernelExecutionResult("succeeded", handoffs=(handoff,)))),
        FakeDiscovery(plugins=[plugin_entry()]),
        PluginWorkerManager(worker_factory),
    )
    started = supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")
    supervisor.execute(
        kernel_id=started["kernel"]["kernel_id"], notebook_id="nb", cell_id="cell",
        code="%%sql", trace_id="trace_execute",
    )
    client_id = supervisor.health()["clients"][0]["client_id"]

    supervisor.stop_kernel(
        kernel_id=started["kernel"]["kernel_id"], trace_id="trace_stop",
    )

    assert worker_factory.handles[0].stop_count == 1
    assert supervisor.health()["clients"] == []
    closed = next(
        event for event in supervisor.events.events_after(0)
        if event["kind"] == "client.closed" and event["payload"]["client_id"] == client_id
    )
    assert closed["payload"]["reason"] == "runtime_cleanup"


def test_explicit_client_close_is_scoped_and_idempotent() -> None:
    handoff = PluginHandoff(
        plugin_id="exact_sql", plugin_version="1.0.0",
        family_id="sql", magic_name="sql", payload={},
    )
    worker_factory = FakePluginWorkerFactory()
    supervisor = Supervisor(
        FakeFactory(FakeKernel(KernelExecutionResult("succeeded", handoffs=(handoff,)))),
        FakeDiscovery(plugins=[plugin_entry()]),
        PluginWorkerManager(worker_factory),
    )
    started = supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")
    supervisor.execute(
        kernel_id=started["kernel"]["kernel_id"], notebook_id="nb", cell_id="cell",
        code="%%sql", trace_id="trace_execute",
    )
    client_id = supervisor.health()["clients"][0]["client_id"]

    closed = supervisor.close_client(client_id=client_id, trace_id="trace_close")
    repeated = supervisor.close_client(client_id=client_id, trace_id="trace_close_again")

    assert closed["cleanup"] == {
        "resource": {"kind": "client", "id": client_id},
        "result": "stopped",
        "plugin_worker_id": worker_factory.specs[0].plugin_worker_id,
    }
    assert repeated["cleanup"]["result"] == "already_absent"
    assert worker_factory.handles[0].stop_count == 1
    assert supervisor.health()["clients"] == []
    assert supervisor.health()["kernel"]["state"] == "on"
    close_events = [event for event in supervisor.events.events_after(0) if event["trace_id"].startswith("trace_close")]
    for event in close_events:
        validate_event(event)
    assert [event["kind"] for event in close_events] == [
        "operation.started", "client.closed", "operation.completed",
        "operation.started", "operation.completed",
    ]
    assert close_events[1]["payload"]["reason"] == "explicit_close"


def test_interactive_client_and_required_surface_publish_and_retire_atomically() -> None:
    handoff = PluginHandoff(
        plugin_id="exact_sql", plugin_version="1.0.0",
        family_id="sql", magic_name="sql", payload={},
    )
    request = TerminalSurfaceRequest(
        request_id="terminal_main",
        argv=("vd", "--play", "private-secret-path"),
        cwd="/target/work",
        environment_overrides={"PRIVATE_TOKEN": "secret"},
        capabilities=("input", "resize", "signal"),
    )
    worker_factory = FakePluginWorkerFactory(
        operation_result=PluginWorkerOperationResult({}, (request,)),
    )
    terminal_surfaces = FakeTerminalSurfaces()
    supervisor = Supervisor(
        FakeFactory(FakeKernel(KernelExecutionResult("succeeded", handoffs=(handoff,)))),
        FakeDiscovery(plugins=[interactive_plugin_entry()]),
        PluginWorkerManager(worker_factory),
        terminal_surfaces,  # type: ignore[arg-type]
    )
    started = supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")
    executed = supervisor.execute(
        kernel_id=started["kernel"]["kernel_id"], notebook_id="nb", cell_id="cell",
        code="%%sql", trace_id="trace_execute",
    )

    health = supervisor.health()
    assert len(health["clients"]) == len(health["surfaces"]) == 1
    surface = health["surfaces"][0]
    assert surface["client_id"] == executed["execution"]["client_id"]
    assert surface["capabilities"] == ["input", "resize", "signal"]
    assert surface["transport"]["endpoint"] == f"/v1/surfaces/{surface['surface_id']}/terminal"
    serialized = repr(health) + repr(supervisor.events.events_after(0))
    assert "private-secret-path" not in serialized
    assert "PRIVATE_TOKEN" not in serialized
    assert "secret" not in serialized

    supervisor.close_client(client_id=surface["client_id"], trace_id="trace_close")
    assert supervisor.health()["surfaces"] == []
    close_kinds = [
        event["kind"] for event in supervisor.events.events_after(0)
        if event["trace_id"] == "trace_close"
    ]
    assert close_kinds == ["operation.started", "surface.closed", "client.closed", "operation.completed"]


def test_fatal_terminal_surface_loss_retires_only_its_client_through_core_failure() -> None:
    handoff = PluginHandoff(
        plugin_id="exact_sql", plugin_version="1.0.0",
        family_id="sql", magic_name="sql", payload={},
    )
    worker_factory = FakePluginWorkerFactory(
        operation_result=PluginWorkerOperationResult(
            {}, (TerminalSurfaceRequest("terminal_main", ("vd",)),),
        ),
    )
    terminal_surfaces = FakeTerminalSurfaces()
    supervisor = Supervisor(
        FakeFactory(FakeKernel(KernelExecutionResult("succeeded", handoffs=(handoff,)))),
        FakeDiscovery(plugins=[interactive_plugin_entry()]),
        PluginWorkerManager(worker_factory),
        terminal_surfaces,  # type: ignore[arg-type]
    )
    started = supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")
    supervisor.execute(
        kernel_id=started["kernel"]["kernel_id"], notebook_id="nb", cell_id="cell",
        code="%%sql", trace_id="trace_execute",
    )
    surface, _ = next(iter(terminal_surfaces.prepared.values()))
    assert terminal_surfaces.on_fatal is not None
    terminal_surfaces.on_fatal(
        surface,
        TerminalSurfaceError(
            "target application exited", reason="channel_closed",
            diagnostics=ProcessDiagnostics(pid=9911, exit_code=7, stderr_excerpt="fatal details"),
        ),
    )

    health = supervisor.health()
    assert health["kernel"]["state"] == "on"
    assert health["clients"] == []
    assert health["surfaces"] == []
    fatal_events = [
        event for event in supervisor.events.events_after(0)
        if event["operation"] == "run_terminal_surface"
    ]
    for event in fatal_events:
        validate_event(event)
    assert [event["kind"] for event in fatal_events] == [
        "failure.occurred", "surface.closed", "client.closed",
    ]
    failure = fatal_events[0]["payload"]
    assert failure["layer"] == "client"
    assert failure["reason"] == "channel_closed"
    assert failure["process"]["exit_code"] == 7
    assert fatal_events[1]["payload"]["failure_id"] == failure["failure_id"]
    assert fatal_events[2]["payload"]["failure_id"] == failure["failure_id"]
    assert worker_factory.handles[0].stop_count == 1


def test_kernel_stop_closes_target_surface_before_its_plugin_worker() -> None:
    handoff = PluginHandoff(
        plugin_id="exact_sql", plugin_version="1.0.0",
        family_id="sql", magic_name="sql", payload={},
    )
    worker_factory = FakePluginWorkerFactory(
        operation_result=PluginWorkerOperationResult(
            {}, (TerminalSurfaceRequest("terminal_main", ("vd",)),),
        ),
    )
    terminal_surfaces = FakeTerminalSurfaces()
    supervisor = Supervisor(
        FakeFactory(FakeKernel(KernelExecutionResult("succeeded", handoffs=(handoff,)))),
        FakeDiscovery(plugins=[interactive_plugin_entry()]),
        PluginWorkerManager(worker_factory),
        terminal_surfaces,  # type: ignore[arg-type]
    )
    started = supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")
    supervisor.execute(
        kernel_id=started["kernel"]["kernel_id"], notebook_id="nb", cell_id="cell",
        code="%%sql", trace_id="trace_execute",
    )
    order: list[str] = []
    original_surface_close = terminal_surfaces.close
    original_worker_stop = worker_factory.handles[0].stop

    def close_surface(surface_id: str, *, timeout: float) -> dict:
        order.append("surface")
        return original_surface_close(surface_id, timeout=timeout)

    def stop_worker(*, trace_id: str, timeout: float) -> str:
        order.append("worker")
        return original_worker_stop(trace_id=trace_id, timeout=timeout)

    terminal_surfaces.close = close_surface  # type: ignore[method-assign]
    worker_factory.handles[0].stop = stop_worker  # type: ignore[method-assign]
    supervisor.stop_kernel(kernel_id=started["kernel"]["kernel_id"], trace_id="trace_stop")
    assert order[:2] == ["surface", "worker"]


def test_terminal_transport_detach_changes_no_authoritative_resource_lifetime() -> None:
    handoff = PluginHandoff(
        plugin_id="exact_sql", plugin_version="1.0.0",
        family_id="sql", magic_name="sql", payload={},
    )
    worker_factory = FakePluginWorkerFactory(
        operation_result=PluginWorkerOperationResult(
            {}, (TerminalSurfaceRequest("terminal_main", ("vd",)),),
        ),
    )
    terminal_surfaces = FakeTerminalSurfaces()
    supervisor = Supervisor(
        FakeFactory(FakeKernel(KernelExecutionResult("succeeded", handoffs=(handoff,)))),
        FakeDiscovery(plugins=[interactive_plugin_entry()]),
        PluginWorkerManager(worker_factory),
        terminal_surfaces,  # type: ignore[arg-type]
    )
    started = supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")
    supervisor.execute(
        kernel_id=started["kernel"]["kernel_id"], notebook_id="nb", cell_id="cell",
        code="%%sql", trace_id="trace_execute",
    )
    before = supervisor.health()
    surface_id = before["surfaces"][0]["surface_id"]
    supervisor.detach_terminal_surface(surface_id, "att_lost")
    after = supervisor.health()
    assert terminal_surfaces.detached == [(surface_id, "att_lost")]
    assert after["kernel"] == before["kernel"]
    assert after["clients"] == before["clients"]
    assert after["surfaces"] == before["surfaces"]
    assert worker_factory.handles[0].stop_count == 0


@pytest.mark.parametrize(
    ("plugin", "operation_result"),
    [
        (interactive_plugin_entry(), PluginWorkerOperationResult({})),
        (
            plugin_entry(),
            PluginWorkerOperationResult({}, (TerminalSurfaceRequest("terminal_main", ("vd",)),)),
        ),
    ],
)
def test_required_surface_contract_fails_before_client_publication(
    plugin: dict,
    operation_result: PluginWorkerOperationResult,
) -> None:
    handoff = PluginHandoff(
        plugin_id="exact_sql", plugin_version="1.0.0",
        family_id="sql", magic_name="sql", payload={},
    )
    worker_factory = FakePluginWorkerFactory(operation_result=operation_result)
    supervisor = Supervisor(
        FakeFactory(FakeKernel(KernelExecutionResult("succeeded", handoffs=(handoff,)))),
        FakeDiscovery(plugins=[plugin]),
        PluginWorkerManager(worker_factory),
    )
    started = supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")

    with pytest.raises(SupervisorError) as raised:
        supervisor.execute(
            kernel_id=started["kernel"]["kernel_id"], notebook_id="nb", cell_id="cell",
            code="%%sql", trace_id="trace_execute",
        )

    assert raised.value.failure.reason == "protocol_violation"
    assert raised.value.failure.scope == "client"
    assert supervisor.health()["clients"] == []
    assert supervisor.health()["surfaces"] == []
    assert worker_factory.handles[0].stop_count == 1


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
        def execute(self, code: str, *, timeout: float, on_output, on_input=None) -> KernelExecutionResult:  # type: ignore[no-untyped-def]
            on_output(KernelOutput("stdout", "text/x-ansi", "before death\n"))
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
    execution_events = [
        event for event in supervisor.events.events_after(0)
        if event["trace_id"] == "trace_execute"
    ]
    assert [event["kind"] for event in execution_events[:4]] == [
        "operation.started",
        "execution.started",
        "execution.output",
        "failure.occurred",
    ]
    assert execution_events[2]["payload"]["data"] == "before death\n"
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


def test_full_restart_reloads_private_target_configuration_snapshot() -> None:
    factory = SequenceFactory([FakeKernel(), FakeKernel()])
    configuration = SequenceConfiguration([
        {"sql": {"main": {"provider": "sqlite", "token": "first-secret"}}},
        {"sql": {"main": {"provider": "sqlite", "token": "second-secret"}}},
    ])
    supervisor = Supervisor(
        factory,
        FakeDiscovery(),
        runtime_configuration=configuration,
    )

    started = supervisor.start_kernel(
        notebook_id="nb_old", kernel_name="python3", trace_id="trace_start",
    )
    restarted = supervisor.restart_notebook(
        runtime_id=started["runtime"]["runtime_id"],
        kernel_id=started["kernel"]["kernel_id"],
        notebook_id="nb_old",
        next_notebook_id="nb_new",
        kernel_name="python3",
        trace_id="trace_restart",
    )

    assert configuration.calls == 2
    assert factory.configurations[0]["sql"]["main"]["token"] == "first-secret"
    assert factory.configurations[1]["sql"]["main"]["token"] == "second-secret"
    public_state = json.dumps({
        "started": started,
        "restarted": restarted,
        "health": supervisor.health(),
        "events": supervisor.events.events_after(0),
    })
    assert "first-secret" not in public_state
    assert "second-secret" not in public_state


def test_configuration_failure_is_redacted_and_prevents_discovery_and_spawn() -> None:
    class BrokenConfiguration:
        def load(self) -> dict:
            raise RuntimeConfigurationError(
                "Runtime configuration is not valid TOML",
                path="/target/config/jusi.toml",
                line=7,
                column=19,
            )

    factory = FakeFactory()
    discovery = FakeDiscovery()
    supervisor = Supervisor(
        factory,
        discovery,
        runtime_configuration=BrokenConfiguration(),
    )

    with pytest.raises(SupervisorError) as captured:
        supervisor.start_kernel(
            notebook_id="nb_config", kernel_name="python3", trace_id="trace_config",
        )

    failure = captured.value.failure
    assert captured.value.status_code == 400
    assert failure.layer == "service"
    assert failure.operation == "start_kernel"
    assert failure.reason == "invalid_request"
    assert failure.resource.resource_id == "nb_config"
    assert failure.details == {
        "path": "/target/config/jusi.toml",
        "line": 7,
        "column": 19,
    }
    assert discovery.calls == []
    assert factory.start_count == 0
    assert supervisor.health()["kernel"] is None


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


def test_pending_input_is_execution_owned_and_replies_are_fenced_and_private() -> None:
    class InputKernel(FakeKernel):
        def __init__(self):
            super().__init__()
            self.prompts = [threading.Event(), threading.Event()]
            self.replies = [threading.Event(), threading.Event()]
            self.values = []

        def execute(self, code, *, timeout, on_output, on_input=None):
            assert on_input is not None
            for index in range(2):
                on_input(f"inp_{index}", f"prompt {index}: ", False)
                self.prompts[index].set()
                assert self.replies[index].wait(2)
            return KernelExecutionResult("succeeded")

        def submit_input(self, input_request_id, value):
            self.values.append(value)
            self.replies[int(input_request_id[-1])].set()

    kernel = InputKernel()
    supervisor = make_supervisor(FakeFactory(kernel))
    started = supervisor.start_kernel(notebook_id="nb_input", kernel_name="python3", trace_id="trace_start")
    kernel_id = started["kernel"]["kernel_id"]
    result = []
    thread = threading.Thread(target=lambda: result.append(supervisor.execute(
        kernel_id=kernel_id, notebook_id="nb_input", cell_id="cell_input",
        code="input()", trace_id="trace_execute",
    )))
    thread.start()
    try:
        assert kernel.prompts[0].wait(1)
        health = supervisor.health()
        validate_health_response({"ok": True, **health})
        pending = health["pending_input"]
        identity = {key: pending[key] for key in ("kernel_id", "execution_id", "input_request_id")}
        assert health["kernel"]["state"] == "on"
        for key in identity:
            with pytest.raises(SupervisorError) as captured:
                supervisor.submit_input(**{**identity, key: "wrong_identity"}, value="PRIVATE_REPLY", trace_id="trace_wrong")
            assert captured.value.failure.reason == "conflict"
            assert supervisor.health()["pending_input"] == pending
        supervisor.submit_input(**identity, value="PRIVATE_REPLY", trace_id="trace_reply")
        assert kernel.prompts[1].wait(1)
        with pytest.raises(SupervisorError):
            supervisor.submit_input(**identity, value="duplicate", trace_id="trace_duplicate")
        second = supervisor.health()["pending_input"]
        assert second["input_request_id"] == "inp_1"
        assert second["execution_id"] == pending["execution_id"]
        supervisor.submit_input(**{**identity, "input_request_id": "inp_1"}, value="", trace_id="trace_empty")
        thread.join(2)
        assert not thread.is_alive()
        assert result[0]["execution"]["outcome"] == "succeeded"
        assert kernel.values == ["PRIVATE_REPLY", ""]
        assert supervisor.health()["pending_input"] is None
        with pytest.raises(SupervisorError):
            supervisor.submit_input(**identity, value="late", trace_id="trace_late")
        events = supervisor.events.events_after(0)
        for event in events:
            validate_event(event)
        assert "PRIVATE_REPLY" not in json.dumps(events)
        assert sum(event["kind"] == "execution.started" for event in events) == 1
        assert [event["kind"] for event in events if event["kind"].startswith("execution.input_")] == [
            "execution.input_requested", "execution.input_replied", "execution.input_requested", "execution.input_replied",
        ]
    finally:
        for event in kernel.replies:
            event.set()
        thread.join(3)
        supervisor.stop_kernel(kernel_id=kernel_id, trace_id="trace_stop")


def test_followups_preserve_client_and_fence_stale_and_failed_workers() -> None:
    plugin = plugin_entry()
    plugin["families"][0]["capabilities"].append("followup")
    handoff = PluginHandoff(plugin_id="exact_sql", plugin_version="1.0.0",
                            family_id="sql", magic_name="sql", payload={})
    workers = FakePluginWorkerFactory()
    supervisor = Supervisor(
        FakeFactory(FakeKernel(KernelExecutionResult("succeeded", handoffs=(handoff,)))),
        FakeDiscovery(plugins=[plugin]), PluginWorkerManager(workers),
    )
    started = supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")
    def execute(cell):
        return supervisor.execute(kernel_id=started["kernel"]["kernel_id"], notebook_id="nb",
                                  cell_id=cell, code="%%sql", trace_id="trace_execute")["execution"]["client_id"]
    first, other = execute("first"), execute("other")
    before = supervisor.health()["clients"]
    # Application results are opaque; even an application error is not client death.
    workers.handles[0].operation_result = PluginWorkerOperationResult({"error": "invalid query"})
    result = supervisor.followup(client_id=first, body="", trace_id="trace_followup")
    assert result["result"] == {"error": "invalid query"}
    assert supervisor.health()["clients"] == before
    events = [e for e in supervisor.events.events_after(0) if e["trace_id"] == "trace_followup"]
    assert [e["kind"] for e in events] == ["operation.started", "operation.completed"]
    for event in events:
        validate_event(event)
        assert event["resource"] == {"kind": "client", "id": first}
    workers.handles[0].request_error = PluginWorkerError(
        "worker died", reason="process_exited", retryable=False,
        diagnostics=ProcessDiagnostics(pid=8765, exit_code=17, stderr_excerpt="worker diagnostic"),
    )
    with pytest.raises(SupervisorError) as failed:
        supervisor.followup(client_id=first, body="private body", trace_id="trace_failure")
    assert failed.value.failure.process.exit_code == 17
    assert "private body" not in str(supervisor.events.events_after(0))
    assert [c["client_id"] for c in supervisor.health()["clients"]] == [other]
    assert supervisor.health()["kernel"]["state"] == "on"
    closed = next(e for e in supervisor.events.events_after(0) if e["kind"] == "client.closed")
    assert closed["payload"]["reason"] == "fatal_failure"
    replacement = execute("first")
    with pytest.raises(SupervisorError) as stale:
        supervisor.followup(client_id=first, body="stale", trace_id="trace_stale")
    assert stale.value.status_code == 409
    assert replacement != first and len(supervisor.health()["clients"]) == 2
    with pytest.raises(SupervisorError) as unknown:
        supervisor.followup(client_id="client_unknown", body="", trace_id="trace_unknown")
    assert unknown.value.status_code == 404


def test_followup_capability_rejection_keeps_client() -> None:
    handoff = PluginHandoff(plugin_id="exact_sql", plugin_version="1.0.0",
                            family_id="sql", magic_name="sql", payload={})
    workers = FakePluginWorkerFactory()
    supervisor = Supervisor(
        FakeFactory(FakeKernel(KernelExecutionResult("succeeded", handoffs=(handoff,)))),
        FakeDiscovery(plugins=[plugin_entry()]), PluginWorkerManager(workers),
    )
    started = supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")
    execution = supervisor.execute(kernel_id=started["kernel"]["kernel_id"], notebook_id="nb",
                                   cell_id="cell", code="%%sql", trace_id="trace_execute")
    client_id = execution["execution"]["client_id"]
    with pytest.raises(SupervisorError) as failed:
        supervisor.followup(client_id=client_id, body="", trace_id="trace_followup")
    assert failed.value.failure.reason == "unsupported"
    assert len(supervisor.health()["clients"]) == 1
    assert workers.handles[0].stop_count == 0


def test_followup_cannot_replace_its_terminal_surface() -> None:
    plugin = interactive_plugin_entry()
    plugin["families"][0]["capabilities"].append("followup")
    handoff = PluginHandoff(plugin_id="exact_sql", plugin_version="1.0.0",
                            family_id="sql", magic_name="sql", payload={})
    workers = FakePluginWorkerFactory(operation_result=PluginWorkerOperationResult(
        {}, (TerminalSurfaceRequest(request_id="terminal_main", argv=("application",)),),
    ))
    surfaces = FakeTerminalSurfaces()
    supervisor = Supervisor(
        FakeFactory(FakeKernel(KernelExecutionResult("succeeded", handoffs=(handoff,)))),
        FakeDiscovery(plugins=[plugin]), PluginWorkerManager(workers), surfaces,
    )
    started = supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")
    executed = supervisor.execute(kernel_id=started["kernel"]["kernel_id"], notebook_id="nb",
                                  cell_id="cell", code="%%sql", trace_id="trace_execute")
    surface_id = supervisor.health()["surfaces"][0]["surface_id"]
    with pytest.raises(SupervisorError) as failed:
        supervisor.followup(client_id=executed["execution"]["client_id"], body="next", trace_id="trace_followup")
    assert failed.value.failure.reason == "protocol_violation"
    assert workers.handles[0].stop_count == 1
    assert surface_id in surfaces.closed
    assert supervisor.health()["surfaces"] == supervisor.health()["clients"] == []
    assert supervisor.health()["kernel"]["state"] == "on"
    for event in supervisor.events.events_after(0):
        validate_event(event)


def test_kernel_completion_is_prefix_only_and_busy_requests_do_not_queue() -> None:
    class CompletingKernel(FakeKernel):
        def complete(self, prefix, *, timeout):
            assert prefix == "α\ns" and timeout > 0
            return {"items": [{"text": "sleep", "start": 2, "end": 3}]}
    supervisor = make_supervisor(FakeFactory(CompletingKernel()))
    started = supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")
    command = dict(kernel_id=started["kernel"]["kernel_id"], notebook_id="nb", cell_id="cell",
                   body="α\nsSUFFIX", cursor_pos=3, trace_id="trace_complete")
    result = supervisor.complete(**command)
    assert result["completion"]["items"][0]["text"] == "sleep"
    assert not any(e["kind"] == "execution.started" for e in supervisor.events.events_after(0))
    acquired, release = threading.Event(), threading.Event()
    def hold_lane():
        with supervisor._operation_lock:
            acquired.set()
            assert release.wait(2)
    thread = threading.Thread(target=hold_lane)
    thread.start()
    assert acquired.wait(1)
    try:
        with pytest.raises(SupervisorError) as busy:
            supervisor.complete(**command)
        assert busy.value.failure.reason == "conflict"
    finally:
        release.set()
        thread.join(2)
    for event in supervisor.events.events_after(0):
        validate_event(event)
    assert "SUFFIX" not in str(supervisor.events.events_after(0))


def test_plugin_completion_context_identity_ranges_and_lifetime() -> None:
    handoff = PluginHandoff(plugin_id="exact_sql", plugin_version="1.0.0",
                            family_id="sql", magic_name="sql", payload={})
    workers = FakePluginWorkerFactory()
    supervisor = Supervisor(
        FakeFactory(FakeKernel(KernelExecutionResult("succeeded", handoffs=(handoff,)))),
        FakeDiscovery(plugins=[plugin_entry()]), PluginWorkerManager(workers),
    )
    started = supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")
    execution = supervisor.execute(kernel_id=started["kernel"]["kernel_id"], notebook_id="nb",
                                   cell_id="cell", code="%%sql", trace_id="trace_execute")
    client_id = execution["execution"]["client_id"]
    calls = []
    def complete(operation, payload, *, trace_id, timeout, request_id=None):
        calls.append((operation, payload))
        return PluginWorkerOperationResult({"items": [{"text": "public", "start": payload["cursor_pos"], "end": payload["cursor_pos"]}]})
    workers.handles[0].request = complete
    command = dict(kernel_id=started["kernel"]["kernel_id"], notebook_id="nb", cell_id="cell",
                   client_id=client_id, body="α\nselect * from SUFFIX", cursor_pos=len("α\nselect * from "), trace_id="trace_complete")
    supervisor.complete(**command)
    operation, payload = calls[0]
    assert operation == "complete"
    assert payload == {"body": command["body"], "prefix": "α\nselect * from ",
                       "cursor_pos": command["cursor_pos"], "cursor_row": 1, "cursor_col": 14}
    assert len(supervisor.health()["clients"]) == 1
    with pytest.raises(SupervisorError):
        supervisor.complete(**{**command, "cell_id": "other"})
    assert len(calls) == 1
    workers.handles[0].request = lambda *args, **kwargs: PluginWorkerOperationResult(
        {"items": [{"text": "bad", "start": 0, "end": command["cursor_pos"] + 1}]})
    with pytest.raises(SupervisorError) as failed:
        supervisor.complete(**command)
    assert failed.value.failure.reason == "protocol_violation"
    assert supervisor.health()["clients"] == []
    assert supervisor.health()["kernel"]["state"] == "on"
    with pytest.raises(SupervisorError) as stale:
        supervisor.complete(**command)
    assert stale.value.failure.reason == "conflict"


@pytest.mark.parametrize("restart", [False, True])
def test_teardown_interrupts_unbounded_execution_without_input(restart: bool) -> None:
    import concurrent.futures

    class RunningKernel(FakeKernel):
        def __init__(self):
            super().__init__()
            self.running = threading.Event()
            self.released = threading.Event()

        def execute(self, code, *, timeout, on_output, on_input=None):
            assert timeout is None
            self.running.set()
            assert self.released.wait(3)
            return KernelExecutionResult("interrupted")

        def interrupt(self):
            super().interrupt()
            self.released.set()

    kernel = RunningKernel()
    supervisor = make_supervisor(SequenceFactory([kernel, FakeKernel()]))
    started = supervisor.start_kernel(notebook_id="nb_long", kernel_name="python3", trace_id="trace_start")
    kernel_id = started["kernel"]["kernel_id"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        executing = pool.submit(supervisor.execute, kernel_id=kernel_id, notebook_id="nb_long",
                                cell_id="cell_long", code="sleep(60)", trace_id="trace_execute")
        try:
            assert kernel.running.wait(1)
            assert supervisor.health()["pending_input"] is None
            if restart:
                teardown = pool.submit(supervisor.restart_notebook, kernel_id=kernel_id,
                    runtime_id=started["runtime"]["runtime_id"], notebook_id="nb_long",
                    next_notebook_id="nb_next", kernel_name="python3", trace_id="trace_restart")
            else:
                teardown = pool.submit(supervisor.stop_kernel, kernel_id=kernel_id, trace_id="trace_stop")
            teardown.result(timeout=2)
            assert executing.result(timeout=1)["execution"]["outcome"] == "interrupted"
            assert kernel.interrupt_count == 1 and kernel.stopped
        finally:
            kernel.released.set()
    current = supervisor.health()["kernel"]
    supervisor.stop_kernel(kernel_id=current["kernel_id"], trace_id="trace_cleanup")


def test_client_work_snapshot_exact_interrupt_and_recoverable_export():
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from jusi.application.ports import PluginOperationError
    from jusi.protocol import validate_health_response
    plugin = plugin_entry()
    plugin["families"][0]["capabilities"] += ["followup", "interrupt", "editor_actions"]
    handoff = PluginHandoff(plugin_id="exact_sql", plugin_version="1.0.0", family_id="sql", magic_name="sql", payload={})
    workers = FakePluginWorkerFactory()
    supervisor = Supervisor(FakeFactory(FakeKernel(KernelExecutionResult("succeeded", handoffs=(handoff,)))),
                            FakeDiscovery(plugins=[plugin]), PluginWorkerManager(workers))
    started = supervisor.start_kernel(notebook_id="nb", kernel_name="python3", trace_id="trace_start")
    client_id = supervisor.execute(kernel_id=started["kernel"]["kernel_id"], notebook_id="nb", cell_id="cell",
                                   code="%%sql", trace_id="trace_execute")["execution"]["client_id"]
    handle = workers.handles[0]
    ready, cancelled = threading.Event(), threading.Event()
    target = []
    def request(operation, payload, *, trace_id, timeout, request_id):
        target.append(request_id)
        ready.set()
        assert cancelled.wait(3)
        raise PluginOperationError("Interrupted", reason="cancelled", retryable=False)
    def interrupt(request_id, *, trace_id, timeout):
        assert request_id == target[0]
        cancelled.set()
        return {"result": "requested"}
    original_request = handle.request
    handle.request, handle.interrupt = request, interrupt
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(supervisor.followup, client_id=client_id, body="private input", trace_id="trace_followup")
            assert ready.wait(2)
            snapshot = validate_health_response({"ok": True, **supervisor.health()})
            assert snapshot["client_operations"] == [{"client_id": client_id, "operation_id": target[0], "kind": "followup"}]
            for wrong_client, wrong_operation in [("client_wrong", target[0]), (client_id, "operation_old")]:
                with pytest.raises(SupervisorError) as stale:
                    supervisor.interrupt_client(client_id=wrong_client, operation_id=wrong_operation, trace_id="trace_stale")
                assert stale.value.failure.reason == "conflict" and not cancelled.is_set()
            supervisor.interrupt_client(client_id=client_id, operation_id=target[0], trace_id="trace_interrupt")
            assert future.result(timeout=2)["operation"]["outcome"] == "cancelled"
    finally:
        cancelled.set()
        handle.request = original_request
    assert supervisor.health()["client_operations"] == [] and handle.stop_count == 0
    assert not any(e["kind"] == "failure.occurred" for e in supervisor.events.events_after(0)
                   if e["trace_id"] in {"trace_interrupt", "trace_followup"})
    handle.operation_result = PluginWorkerOperationResult({"action": "open", "path": "/remote/data"})
    with pytest.raises(SupervisorError) as malformed:
        supervisor.editor_action(client_id=client_id, action="open", selection={}, trace_id="trace_bad_export")
    assert malformed.value.failure.reason == "protocol_violation" and handle.stop_count == 0
    handle.operation_result = PluginWorkerOperationResult({"action": "copy", "text": "private selection", "regtype": "v"})
    result = supervisor.editor_action(client_id=client_id, action="copy", selection={}, trace_id="trace_export")
    assert result["editor_action"]["text"] == "private selection"
    assert "private selection" not in str(supervisor.events.events_after(0))
    supervisor.close_client(client_id=client_id, trace_id="trace_close")
