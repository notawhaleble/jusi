from __future__ import annotations

from dataclasses import dataclass
import threading
import uuid
from typing import Any

from jusi.application.ports import (
    PluginWorkerError,
    PluginWorkerFactory,
    PluginWorkerHandle,
    PluginWorkerSpec,
    PluginWorkerOperationResult,
)
from jusi.domain.models import NotebookRuntime, PluginWorkerResource


CAPABILITY_BY_OPERATION = {
    "execute": "execute",
    "followup": "followup",
    "complete": "complete",
    "editor_action": "editor_actions",
}


class PluginWorkerSelectionError(ValueError):
    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass
class _OwnedWorker:
    resource: PluginWorkerResource
    handle: PluginWorkerHandle


class PluginWorkerManager:
    """Own exact workers for one active immutable runtime catalog generation."""

    def __init__(self, factory: PluginWorkerFactory) -> None:
        self._factory = factory
        self._lock = threading.RLock()
        self._runtime: NotebookRuntime | None = None
        self._workers: dict[str, _OwnedWorker] = {}
        self._known_worker_ids: set[str] = set()

    def activate_runtime(self, runtime: NotebookRuntime) -> None:
        with self._lock:
            if self._workers:
                raise RuntimeError("Cannot replace a plugin-worker runtime before worker cleanup")
            self._runtime = runtime

    def start(
        self,
        *,
        runtime_id: str,
        plugin_id: str,
        family_id: str,
        client_id: str,
        execution_id: str,
        timeout: float,
    ) -> PluginWorkerResource:
        with self._lock:
            runtime, plugin, family = self._select(runtime_id, plugin_id, family_id)
            entry_point = plugin["worker_entry_point"]
            if entry_point is None:
                raise PluginWorkerSelectionError(
                    f"Plugin {plugin_id} has no worker entry point",
                    reason="unsupported",
                )
            plugin_worker_id = f"pwrk_{uuid.uuid4().hex}"
            self._known_worker_ids.add(plugin_worker_id)
            spec = PluginWorkerSpec(
                plugin_worker_id=plugin_worker_id,
                runtime_id=runtime.runtime_id,
                plugin_id=plugin_id,
                family_id=family_id,
                client_id=client_id,
                execution_id=execution_id,
                entry_point=entry_point,
            )
            handle = self._factory.start(spec, timeout=timeout)
            resource = PluginWorkerResource(
                plugin_worker_id=plugin_worker_id,
                runtime_id=runtime.runtime_id,
                plugin_id=plugin_id,
                plugin_version=plugin["plugin_version"],
                family_id=family_id,
                client_id=client_id,
                execution_id=execution_id,
                capabilities=tuple(family["capabilities"]),
                interaction=plugin["interaction"],
                pid=handle.pid,
            )
            self._workers[plugin_worker_id] = _OwnedWorker(resource, handle)
            return resource

    def request(
        self,
        plugin_worker_id: str,
        operation: str,
        payload: dict[str, Any],
        *,
        trace_id: str,
        timeout: float,
    ) -> PluginWorkerOperationResult:
        with self._lock:
            owned = self._workers.get(plugin_worker_id)
            if owned is None:
                reason = "conflict" if plugin_worker_id in self._known_worker_ids else "not_found"
                raise PluginWorkerSelectionError(
                    f"Plugin worker {plugin_worker_id} is not active",
                    reason=reason,
                )
            capability = CAPABILITY_BY_OPERATION.get(operation)
            if capability is None or capability not in owned.resource.capabilities:
                raise PluginWorkerSelectionError(
                    f"Plugin worker {plugin_worker_id} does not declare {operation}",
                    reason="unsupported",
                )
            handle = owned.handle
        try:
            return handle.request(operation, payload, trace_id=trace_id, timeout=timeout)
        except PluginWorkerError:
            with self._lock:
                self._workers.pop(plugin_worker_id, None)
            raise

    def stop(self, plugin_worker_id: str, *, trace_id: str, timeout: float) -> dict[str, Any]:
        with self._lock:
            owned = self._workers.pop(plugin_worker_id, None)
            known = plugin_worker_id in self._known_worker_ids
        if owned is None:
            if not known:
                raise PluginWorkerSelectionError(
                    f"Unknown plugin worker {plugin_worker_id}",
                    reason="not_found",
                )
            return {"plugin_worker_id": plugin_worker_id, "result": "already_absent"}
        try:
            result = owned.handle.stop(trace_id=trace_id, timeout=timeout)
        except PluginWorkerError:
            with self._lock:
                self._workers[plugin_worker_id] = owned
            raise
        return {"plugin_worker_id": plugin_worker_id, "result": result}

    def teardown_runtime(self, runtime_id: str, *, trace_id: str, timeout: float) -> list[dict[str, Any]]:
        with self._lock:
            runtime = self._runtime
            if runtime is None:
                return []
            if runtime.runtime_id != runtime_id:
                raise PluginWorkerSelectionError(
                    f"Plugin worker runtime {runtime_id} is not current",
                    reason="conflict",
                )
            owned = list(self._workers.values())
        results: list[dict[str, Any]] = []
        for item in owned:
            try:
                result = item.handle.stop(trace_id=trace_id, timeout=timeout)
                results.append({"plugin_worker_id": item.resource.plugin_worker_id, "result": result})
                with self._lock:
                    self._workers.pop(item.resource.plugin_worker_id, None)
            except PluginWorkerError as exc:
                results.append({
                    "plugin_worker_id": item.resource.plugin_worker_id,
                    "result": "failed",
                    "reason": exc.reason,
                    "message": str(exc),
                    "process": exc.diagnostics.to_dict() if exc.diagnostics is not None else None,
                })
        with self._lock:
            if not self._workers:
                self._runtime = None
        return results

    def _select(
        self,
        runtime_id: str,
        plugin_id: str,
        family_id: str,
    ) -> tuple[NotebookRuntime, dict[str, Any], dict[str, Any]]:
        runtime = self._runtime
        if runtime is None or runtime.runtime_id != runtime_id:
            raise PluginWorkerSelectionError(
                f"Notebook runtime {runtime_id} is not current",
                reason="conflict",
            )
        plugin = next(
            (item for item in runtime.plugin_catalog["plugins"] if item["plugin_id"] == plugin_id),
            None,
        )
        if plugin is None:
            raise PluginWorkerSelectionError(
                f"Plugin {plugin_id} is not in runtime catalog {runtime_id}",
                reason="not_found",
            )
        family = next((item for item in plugin["families"] if item["family_id"] == family_id), None)
        if family is None:
            raise PluginWorkerSelectionError(
                f"Plugin {plugin_id} does not claim family {family_id}",
                reason="not_found",
            )
        return runtime, plugin, family
