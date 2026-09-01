from __future__ import annotations

import pytest

from jusi.application.plugin_workers import PluginWorkerManager, PluginWorkerSelectionError
from jusi.application.ports import PluginWorkerOperationResult, PluginWorkerSpec
from jusi.domain.models import NotebookRuntime


class FakeHandle:
    pid = 7001

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict, str]] = []
        self.stop_count = 0

    def request(self, operation: str, payload: dict, *, trace_id: str, timeout: float) -> PluginWorkerOperationResult:
        assert timeout > 0
        self.requests.append((operation, payload, trace_id))
        return PluginWorkerOperationResult({"accepted": True})

    def stop(self, *, trace_id: str, timeout: float) -> str:
        assert trace_id and timeout > 0
        self.stop_count += 1
        return "stopped"


class FakeFactory:
    def __init__(self) -> None:
        self.specs: list[PluginWorkerSpec] = []
        self.handles: list[FakeHandle] = []

    def start(self, spec: PluginWorkerSpec, *, timeout: float) -> FakeHandle:
        assert timeout > 0
        handle = FakeHandle()
        self.specs.append(spec)
        self.handles.append(handle)
        return handle


def runtime(*, worker_entry_point: str | None = "fixture.worker:create_worker") -> NotebookRuntime:
    return NotebookRuntime(
        runtime_id="runtime_current",
        notebook_id="notebook_current",
        discovery_id="discovery_current",
        kernel_id="kernel_current",
        plugin_catalog={
            "protocol_version": 1,
            "catalog_version": 1,
            "discovery_id": "discovery_current",
            "plugins": [{
                "plugin_id": "exact_sql",
                "plugin_version": "2.0.0",
                "distribution": "jusi-exact-sql",
                "families": [{
                    "family_id": "sql",
                    "magic_name": "sql",
                    "capabilities": ["execute", "complete"],
                }],
                "kernel_extensions": [],
                "worker_entry_point": worker_entry_point,
                "media_types": ["text/plain"],
                "interaction": "request_response",
            }],
        },
    )


def test_manager_selects_entry_point_only_from_current_catalog_and_checks_capability() -> None:
    worker_factory = FakeFactory()
    manager = PluginWorkerManager(worker_factory)
    manager.activate_runtime(runtime())
    resource = manager.start(
        runtime_id="runtime_current",
        plugin_id="exact_sql",
        family_id="sql",
        client_id="client_one",
        execution_id="execution_one",
        timeout=2,
    )
    assert worker_factory.specs[0].entry_point == "fixture.worker:create_worker"
    assert worker_factory.specs[0].plugin_worker_id == resource.plugin_worker_id
    assert resource.plugin_version == "2.0.0"
    assert manager.request(
        resource.plugin_worker_id, "complete", {"text": "sel"}, trace_id="trace_complete", timeout=2,
    ).result == {"accepted": True}

    with pytest.raises(PluginWorkerSelectionError, match="does not declare") as unsupported:
        manager.request(resource.plugin_worker_id, "followup", {}, trace_id="trace_followup", timeout=2)
    assert unsupported.value.reason == "unsupported"


def test_manager_rejects_stale_or_missing_catalog_identity_and_null_worker() -> None:
    manager = PluginWorkerManager(FakeFactory())
    manager.activate_runtime(runtime())
    arguments = dict(
        plugin_id="exact_sql", family_id="sql", client_id="client_one",
        execution_id="execution_one", timeout=2,
    )
    with pytest.raises(PluginWorkerSelectionError) as stale:
        manager.start(runtime_id="runtime_stale", **arguments)
    assert stale.value.reason == "conflict"
    with pytest.raises(PluginWorkerSelectionError) as missing:
        manager.start(runtime_id="runtime_current", **{**arguments, "plugin_id": "invented"})
    assert missing.value.reason == "not_found"

    empty_manager = PluginWorkerManager(FakeFactory())
    empty_manager.activate_runtime(runtime(worker_entry_point=None))
    with pytest.raises(PluginWorkerSelectionError) as unsupported:
        empty_manager.start(runtime_id="runtime_current", **arguments)
    assert unsupported.value.reason == "unsupported"


def test_runtime_teardown_fences_workers_and_cleanup_is_idempotent() -> None:
    worker_factory = FakeFactory()
    manager = PluginWorkerManager(worker_factory)
    manager.activate_runtime(runtime())
    first = manager.start(
        runtime_id="runtime_current", plugin_id="exact_sql", family_id="sql",
        client_id="client_one", execution_id="execution_one", timeout=2,
    )
    second = manager.start(
        runtime_id="runtime_current", plugin_id="exact_sql", family_id="sql",
        client_id="client_two", execution_id="execution_two", timeout=2,
    )

    cleanup = manager.teardown_runtime("runtime_current", trace_id="trace_restart", timeout=2)
    assert cleanup == [
        {"plugin_worker_id": first.plugin_worker_id, "result": "stopped"},
        {"plugin_worker_id": second.plugin_worker_id, "result": "stopped"},
    ]
    assert [handle.stop_count for handle in worker_factory.handles] == [1, 1]
    assert manager.teardown_runtime("runtime_current", trace_id="trace_again", timeout=2) == []
    assert manager.stop(first.plugin_worker_id, trace_id="trace_stop", timeout=2)["result"] == "already_absent"
