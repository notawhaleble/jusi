from __future__ import annotations

import threading
import uuid
from typing import Any

from jusi.application.events import EventLog
from jusi.application.ports import (
    KernelAdapterError,
    KernelAdapterSpec,
    KernelFactory,
    KernelHandle,
    PluginCatalogDiscovery,
    PluginCatalogDiscoveryError,
)
from jusi.application.plugin_workers import PluginWorkerManager
from jusi.domain.models import (
    ExecutionResource,
    Failure,
    KernelResource,
    NotebookRuntime,
    Operation,
    ResourceRef,
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class SupervisorError(RuntimeError):
    def __init__(self, status_code: int, failure: Failure) -> None:
        super().__init__(failure.message)
        self.status_code = status_code
        self.failure = failure


class Supervisor:
    def __init__(
        self,
        kernel_factory: KernelFactory,
        plugin_discovery: PluginCatalogDiscovery,
        plugin_workers: PluginWorkerManager | None = None,
    ) -> None:
        self.supervisor_id = new_id("sup")
        self.events = EventLog(self.supervisor_id)
        self._kernel_factory = kernel_factory
        self._plugin_discovery = plugin_discovery
        self._plugin_workers = plugin_workers
        self._operation_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._current_kernel: KernelResource | None = None
        self._kernel_handle: KernelHandle | None = None
        self._known_kernel_ids: set[str] = set()
        self._current_runtime: NotebookRuntime | None = None
        self._known_runtime_ids: set[str] = set()
        self.events.append(
            trace_id=new_id("trace"),
            layer="service",
            operation="service_start",
            kind="service.ready",
            resource=ResourceRef("supervisor", self.supervisor_id),
            payload={"supervisor_id": self.supervisor_id},
        )

    def health(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "status": "ready",
                "supervisor_id": self.supervisor_id,
                "earliest_event_sequence": self.events.earliest_sequence,
                "event_sequence": self.events.latest_sequence,
                "kernel": self._current_kernel.to_dict() if self._current_kernel is not None else None,
                "runtime": self._current_runtime.to_dict() if self._current_runtime is not None else None,
            }

    def record_failure(self, failure: Failure) -> None:
        self._emit_failure(failure)

    def inspect_kernel(self, kernel_id: str) -> dict[str, Any]:
        with self._state_lock:
            if self._current_kernel is not None and self._current_kernel.kernel_id == kernel_id:
                return self._current_kernel.to_dict()
            if kernel_id in self._known_kernel_ids:
                return {"kernel_id": kernel_id, "state": "off"}
            raise self._request_failure(
                status_code=404,
                trace_id=new_id("trace"),
                layer="supervisor",
                operation="inspect",
                reason="not_found",
                message=f"Unknown kernel {kernel_id}",
                retryable=False,
                scope="request",
                resource=ResourceRef("kernel", kernel_id),
            )

    def start_kernel(
        self,
        *,
        notebook_id: str,
        kernel_name: str,
        trace_id: str,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        with self._operation_lock:
            if self._current_kernel is not None and self._current_kernel.state == "on":
                raise self._request_failure(
                    status_code=409,
                    trace_id=trace_id,
                    layer="supervisor",
                    operation="start_kernel",
                    reason="conflict",
                    message=f"Kernel {self._current_kernel.kernel_id} is already on",
                    retryable=False,
                    scope="request",
                    resource=ResourceRef("kernel", self._current_kernel.kernel_id),
                )

            operation = self._begin_operation("start_kernel", trace_id)
            if self._current_runtime is not None and self._plugin_workers is not None:
                worker_cleanup = self._plugin_workers.teardown_runtime(
                    self._current_runtime.runtime_id, trace_id=trace_id, timeout=timeout,
                )
                failed_workers = [item for item in worker_cleanup if item["result"] == "failed"]
                if failed_workers:
                    failure = self._failure(
                        trace_id=trace_id,
                        layer="plugin_worker",
                        operation="start_kernel",
                        reason="cleanup_incomplete",
                        message="Could not clean up workers from the previous notebook runtime",
                        retryable=True,
                        scope="plugin_worker",
                        resource=ResourceRef("plugin_worker", failed_workers[0]["plugin_worker_id"]),
                        details={"workers": worker_cleanup},
                    )
                    self._emit_failure(failure)
                    self._complete_operation(operation, "failed", resource=failure.resource, failure=failure)
                    raise SupervisorError(503, failure)
            with self._state_lock:
                self._current_runtime = None
            runtime_id = new_id("run")
            discovery_id = new_id("dsc")
            with self._state_lock:
                self._known_runtime_ids.add(runtime_id)
            try:
                discovery = self._plugin_discovery.discover(discovery_id=discovery_id, timeout=timeout)
            except PluginCatalogDiscoveryError as exc:
                failure = self._discovery_failure(
                    exc,
                    trace_id=trace_id,
                    operation="start_kernel",
                    discovery_id=discovery_id,
                    teardown_completed=None,
                )
                self._emit_failure(failure)
                self._complete_operation(
                    operation,
                    "failed",
                    resource=ResourceRef("plugin_discovery", discovery_id),
                    failure=failure,
                )
                raise SupervisorError(503, failure) from exc
            kernel_id = new_id("krn")
            kernel_ref = ResourceRef("kernel", kernel_id)
            with self._state_lock:
                self._known_kernel_ids.add(kernel_id)
            try:
                handle = self._kernel_factory.start(
                    kernel_name, timeout=timeout, adapters=self._kernel_adapter_specs(discovery.catalog),
                )
            except KernelAdapterError as exc:
                failure = self._failure(
                    trace_id=trace_id,
                    layer=exc.layer,
                    operation="start_kernel",
                    reason=exc.reason,
                    message=str(exc),
                    retryable=exc.retryable,
                    scope="kernel",
                    resource=kernel_ref,
                    process=exc.diagnostics,
                    details=exc.details,
                )
                self._emit_failure(failure)
                self._complete_operation(operation, "failed", resource=kernel_ref, failure=failure)
                raise SupervisorError(503, failure) from exc

            kernel = KernelResource(
                kernel_id=kernel_id,
                notebook_id=notebook_id,
                kernel_name=kernel_name,
                state="on",
                pid=handle.pid,
            )
            runtime = NotebookRuntime(
                runtime_id=runtime_id,
                notebook_id=notebook_id,
                discovery_id=discovery_id,
                kernel_id=kernel_id,
                plugin_catalog=discovery.catalog,
            )
            if self._plugin_workers is not None:
                self._plugin_workers.activate_runtime(runtime)
            with self._state_lock:
                self._current_kernel = kernel
                self._kernel_handle = handle
                self._current_runtime = runtime
            self.events.append(
                trace_id=trace_id,
                layer="kernel",
                operation="start_kernel",
                kind="kernel.state_changed",
                resource=kernel_ref,
                payload={"kernel_id": kernel_id, "previous_state": "off", "state": "on"},
            )
            self._complete_operation(operation, "succeeded", resource=kernel_ref)
            return {"operation": operation.to_dict(), "runtime": runtime.to_dict(), "kernel": kernel.to_dict()}

    def execute(
        self,
        *,
        kernel_id: str,
        notebook_id: str,
        cell_id: str,
        code: str,
        trace_id: str,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        with self._operation_lock:
            execution_details = {
                "code_bytes": len(code.encode("utf-8")),
                "code_line_count": code.count("\n") + 1 if code else 0,
            }
            kernel, handle = self._require_live_kernel(kernel_id, trace_id, "execute")
            if kernel.notebook_id != notebook_id:
                raise self._request_failure(
                    status_code=409,
                    trace_id=trace_id,
                    layer="supervisor",
                    operation="execute",
                    reason="conflict",
                    message="notebook_id does not own the requested kernel",
                    retryable=False,
                    scope="request",
                    resource=ResourceRef("kernel", kernel_id),
                )

            operation = self._begin_operation("execute", trace_id, resource=ResourceRef("kernel", kernel_id))
            execution = ExecutionResource(
                execution_id=new_id("exe"),
                kernel_id=kernel_id,
                notebook_id=notebook_id,
                cell_id=cell_id,
                client_id=new_id("cli"),
            )
            execution_ref = ResourceRef("execution", execution.execution_id)
            self.events.append(
                trace_id=trace_id,
                layer="execution",
                operation="execute",
                kind="execution.started",
                resource=execution_ref,
                payload=execution.to_dict(),
            )
            try:
                result = handle.execute(code, timeout=timeout)
            except KernelAdapterError as exc:
                kernel_failure = self._failure(
                    trace_id=trace_id,
                    layer=exc.layer,
                    operation="execute",
                    reason=exc.reason,
                    message=str(exc),
                    retryable=exc.retryable,
                    scope="kernel" if exc.layer == "kernel" else "execution",
                    resource=ResourceRef("kernel", kernel_id) if exc.layer == "kernel" else execution_ref,
                    process=exc.diagnostics,
                    details={**execution_details, **exc.details},
                )
                self._emit_failure(kernel_failure)
                if exc.layer == "kernel":
                    execution_failure = self._failure(
                        trace_id=trace_id,
                        layer="execution",
                        operation="execute",
                        reason="cancelled",
                        message="Execution ended because its kernel died",
                        retryable=True,
                        scope="execution",
                        resource=execution_ref,
                        details=execution_details,
                        caused_by_failure_id=kernel_failure.failure_id,
                    )
                    self._emit_failure(execution_failure)
                    try:
                        handle.stop(timeout=1.0)
                    except KernelAdapterError:
                        # The authoritative state is already off because death was
                        # observed. Cleanup diagnostics remain attached to the
                        # originating kernel failure for this initial slice.
                        pass
                    with self._state_lock:
                        kernel.state = "off"
                        self._kernel_handle = None
                    self.events.append(
                        trace_id=trace_id,
                        layer="kernel",
                        operation="execute",
                        kind="kernel.state_changed",
                        resource=ResourceRef("kernel", kernel_id),
                        payload={"kernel_id": kernel_id, "previous_state": "on", "state": "off"},
                    )
                execution.complete("failed")
                self.events.append(
                    trace_id=trace_id,
                    layer="execution",
                    operation="execute",
                    kind="execution.completed",
                    resource=execution_ref,
                    payload=execution.to_dict(),
                )
                self._complete_operation(operation, "failed", resource=execution_ref, failure=kernel_failure)
                raise SupervisorError(503, kernel_failure) from exc

            if result.handoffs:
                handoff = result.handoffs[0]
                runtime = self._current_runtime
                if runtime is None or not self._handoff_matches_catalog(runtime, handoff):
                    failure = self._failure(
                        trace_id=trace_id,
                        layer="protocol",
                        operation="execute",
                        reason="invalid_request",
                        message="Kernel plugin handoff does not match the authoritative runtime catalog",
                        retryable=False,
                        scope="execution",
                        resource=execution_ref,
                        details={
                            **execution_details,
                            "plugin_id": handoff.plugin_id,
                            "plugin_version": handoff.plugin_version,
                            "family_id": handoff.family_id,
                            "magic_name": handoff.magic_name,
                        },
                    )
                    self._emit_failure(failure)
                    execution.complete("failed")
                    self.events.append(
                        trace_id=trace_id,
                        layer="execution",
                        operation="execute",
                        kind="execution.completed",
                        resource=execution_ref,
                        payload=execution.to_dict(),
                    )
                    self._complete_operation(operation, "failed", resource=execution_ref, failure=failure)
                    raise SupervisorError(409, failure)

            for output in result.outputs:
                self.events.append(
                    trace_id=trace_id,
                    layer="execution",
                    operation="execute",
                    kind="execution.output",
                    resource=execution_ref,
                    payload={
                        "execution_id": execution.execution_id,
                        "client_id": execution.client_id,
                        "output_kind": output.output_kind,
                        "media_type": output.media_type,
                        "data": output.data,
                    },
                )

            if result.outcome == "succeeded":
                execution.complete("succeeded")
                failure = None
                operation_outcome = "succeeded"
            else:
                execution.complete("failed")
                failure = self._failure(
                    trace_id=trace_id,
                    layer="execution",
                    operation="execute",
                    reason="execution_error",
                    message=result.error_value or result.error_name or "Execution failed",
                    retryable=False,
                    scope="execution",
                    resource=execution_ref,
                    details={
                        **execution_details,
                        "error_name": result.error_name,
                        "error_value": result.error_value,
                    },
                )
                self._emit_failure(failure)
                operation_outcome = "failed"

            self.events.append(
                trace_id=trace_id,
                layer="execution",
                operation="execute",
                kind="execution.completed",
                resource=execution_ref,
                payload=execution.to_dict(),
            )
            self._complete_operation(operation, operation_outcome, resource=execution_ref, failure=failure)
            response = {"operation": operation.to_dict(), "execution": execution.to_dict()}
            if failure is not None:
                response["failure"] = failure.to_dict()
            return response

    def stop_kernel(self, *, kernel_id: str, trace_id: str, timeout: float = 5.0) -> dict[str, Any]:
        with self._operation_lock:
            operation = self._begin_operation("stop_kernel", trace_id, resource=ResourceRef("kernel", kernel_id))
            if self._current_kernel is None or self._current_kernel.kernel_id != kernel_id or self._current_kernel.state == "off":
                if kernel_id not in self._known_kernel_ids:
                    failure = self._failure(
                        trace_id=trace_id,
                        layer="supervisor",
                        operation="stop_kernel",
                        reason="not_found",
                        message=f"Unknown kernel {kernel_id}",
                        retryable=False,
                        scope="request",
                        resource=ResourceRef("kernel", kernel_id),
                    )
                    self._emit_failure(failure)
                    self._complete_operation(operation, "failed", resource=ResourceRef("kernel", kernel_id), failure=failure)
                    raise SupervisorError(404, failure)
                cleanup = {"resource": ResourceRef("kernel", kernel_id).to_dict(), "result": "already_absent"}
                self._complete_operation(operation, "succeeded", resource=ResourceRef("kernel", kernel_id), cleanup=cleanup)
                return {"operation": operation.to_dict(), "kernel": {"kernel_id": kernel_id, "state": "off"}, "cleanup": cleanup}

            kernel = self._current_kernel
            handle = self._kernel_handle
            assert handle is not None
            worker_cleanup: list[dict[str, Any]] = []
            if self._current_runtime is not None and self._plugin_workers is not None:
                worker_cleanup = self._plugin_workers.teardown_runtime(
                    self._current_runtime.runtime_id, trace_id=trace_id, timeout=timeout,
                )
            try:
                handle.stop(timeout=timeout)
            except KernelAdapterError as exc:
                failure = self._failure(
                    trace_id=trace_id,
                    layer=exc.layer,
                    operation="stop_kernel",
                    reason=exc.reason,
                    message=str(exc),
                    retryable=exc.retryable,
                    scope="kernel",
                    resource=ResourceRef("kernel", kernel_id),
                    process=exc.diagnostics,
                )
                self._emit_failure(failure)
                self._complete_operation(operation, "failed", resource=ResourceRef("kernel", kernel_id), failure=failure)
                raise SupervisorError(503, failure) from exc

            with self._state_lock:
                kernel.state = "off"
                self._kernel_handle = None
            self.events.append(
                trace_id=trace_id,
                layer="kernel",
                operation="stop_kernel",
                kind="kernel.state_changed",
                resource=ResourceRef("kernel", kernel_id),
                payload={"kernel_id": kernel_id, "previous_state": "on", "state": "off"},
            )
            cleanup = {"resource": ResourceRef("kernel", kernel_id).to_dict(), "result": "stopped"}
            if worker_cleanup:
                cleanup["plugin_workers"] = worker_cleanup
            failed_workers = [item for item in worker_cleanup if item["result"] == "failed"]
            if failed_workers:
                failure = self._failure(
                    trace_id=trace_id,
                    layer="plugin_worker",
                    operation="stop_kernel",
                    reason="cleanup_incomplete",
                    message="Kernel stopped, but plugin-worker cleanup was incomplete",
                    retryable=True,
                    scope="plugin_worker",
                    resource=ResourceRef("plugin_worker", failed_workers[0]["plugin_worker_id"]),
                    details={"kernel_state": "off", "workers": worker_cleanup},
                )
                self._emit_failure(failure)
                self._complete_operation(operation, "failed", resource=failure.resource, failure=failure, cleanup=cleanup)
                raise SupervisorError(503, failure)
            self._complete_operation(operation, "succeeded", resource=ResourceRef("kernel", kernel_id), cleanup=cleanup)
            return {"operation": operation.to_dict(), "kernel": kernel.to_dict(), "cleanup": cleanup}

    def restart_notebook(
        self,
        *,
        runtime_id: str,
        kernel_id: str,
        notebook_id: str,
        next_notebook_id: str,
        kernel_name: str,
        trace_id: str,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        with self._operation_lock:
            runtime = self._current_runtime
            kernel = self._current_kernel
            if (
                runtime is None
                or runtime.runtime_id != runtime_id
                or runtime.notebook_id != notebook_id
                or runtime.kernel_id != kernel_id
                or kernel is None
                or kernel.kernel_id != kernel_id
            ):
                known = runtime_id in self._known_runtime_ids
                raise self._request_failure(
                    status_code=409 if known else 404,
                    trace_id=trace_id,
                    layer="supervisor",
                    operation="restart_notebook",
                    reason="conflict" if known else "not_found",
                    message=f"Notebook runtime {runtime_id} is not current",
                    retryable=False,
                    scope="request",
                    resource=ResourceRef("notebook_runtime", runtime_id),
                )
            if next_notebook_id == notebook_id:
                raise self._request_failure(
                    status_code=409,
                    trace_id=trace_id,
                    layer="supervisor",
                    operation="restart_notebook",
                    reason="conflict",
                    message="Full restart requires a fresh frontend notebook identity",
                    retryable=False,
                    scope="request",
                    resource=ResourceRef("notebook_runtime", runtime_id),
                )

            operation = self._begin_operation(
                "restart_notebook",
                trace_id,
                resource=ResourceRef("notebook_runtime", runtime_id),
            )
            worker_cleanup: list[dict[str, Any]] = []
            if self._plugin_workers is not None:
                worker_cleanup = self._plugin_workers.teardown_runtime(
                    runtime_id, trace_id=trace_id, timeout=timeout,
                )
                failed_workers = [item for item in worker_cleanup if item["result"] == "failed"]
                if failed_workers:
                    cleanup = {
                        "resource": ResourceRef("notebook_runtime", runtime_id).to_dict(),
                        "kernel_id": kernel_id,
                        "result": "failed",
                        "plugin_workers": worker_cleanup,
                    }
                    failure = self._failure(
                        trace_id=trace_id,
                        layer="plugin_worker",
                        operation="restart_notebook",
                        reason="cleanup_incomplete",
                        message="Full restart stopped because plugin-worker cleanup was incomplete",
                        retryable=True,
                        scope="plugin_worker",
                        resource=ResourceRef("plugin_worker", failed_workers[0]["plugin_worker_id"]),
                        details={"teardown_completed": False, "workers": worker_cleanup},
                    )
                    self._emit_failure(failure)
                    self._complete_operation(operation, "failed", resource=failure.resource, failure=failure, cleanup=cleanup)
                    raise SupervisorError(503, failure)
            cleanup_result = "already_absent"
            if kernel.state == "on":
                handle = self._kernel_handle
                assert handle is not None
                try:
                    handle.stop(timeout=timeout)
                except KernelAdapterError as exc:
                    failure = self._failure(
                        trace_id=trace_id,
                        layer=exc.layer,
                        operation="restart_notebook",
                        reason=exc.reason,
                        message=str(exc),
                        retryable=exc.retryable,
                        scope="kernel",
                        resource=ResourceRef("kernel", kernel_id),
                        process=exc.diagnostics,
                        details={"teardown_completed": False, "next_notebook_id": next_notebook_id},
                    )
                    self._emit_failure(failure)
                    self._complete_operation(
                        operation,
                        "failed",
                        resource=ResourceRef("notebook_runtime", runtime_id),
                        failure=failure,
                    )
                    raise SupervisorError(503, failure) from exc
                with self._state_lock:
                    kernel.state = "off"
                    self._kernel_handle = None
                cleanup_result = "stopped"
                self.events.append(
                    trace_id=trace_id,
                    layer="kernel",
                    operation="restart_notebook",
                    kind="kernel.state_changed",
                    resource=ResourceRef("kernel", kernel_id),
                    payload={"kernel_id": kernel_id, "previous_state": "on", "state": "off"},
                )

            cleanup = {
                "resource": ResourceRef("notebook_runtime", runtime_id).to_dict(),
                "kernel_id": kernel_id,
                "result": cleanup_result,
            }
            if worker_cleanup:
                cleanup["plugin_workers"] = worker_cleanup
            with self._state_lock:
                self._current_runtime = None

            next_runtime_id = new_id("run")
            discovery_id = new_id("dsc")
            next_kernel_id = new_id("krn")
            with self._state_lock:
                self._known_runtime_ids.add(next_runtime_id)
                self._known_kernel_ids.add(next_kernel_id)
            try:
                discovery = self._plugin_discovery.discover(discovery_id=discovery_id, timeout=timeout)
            except PluginCatalogDiscoveryError as exc:
                failure = self._discovery_failure(
                    exc,
                    trace_id=trace_id,
                    operation="restart_notebook",
                    discovery_id=discovery_id,
                    teardown_completed=True,
                    next_notebook_id=next_notebook_id,
                )
                self._emit_failure(failure)
                self._complete_operation(
                    operation,
                    "failed",
                    resource=ResourceRef("plugin_discovery", discovery_id),
                    failure=failure,
                    cleanup=cleanup,
                )
                raise SupervisorError(503, failure) from exc

            try:
                next_handle = self._kernel_factory.start(
                    kernel_name, timeout=timeout, adapters=self._kernel_adapter_specs(discovery.catalog),
                )
            except KernelAdapterError as exc:
                failure = self._failure(
                    trace_id=trace_id,
                    layer=exc.layer,
                    operation="restart_notebook",
                    reason=exc.reason,
                    message=str(exc),
                    retryable=exc.retryable,
                    scope="kernel",
                    resource=ResourceRef("kernel", next_kernel_id),
                    process=exc.diagnostics,
                    details={
                        **exc.details,
                        "teardown_completed": True,
                        "next_notebook_id": next_notebook_id,
                    },
                )
                self._emit_failure(failure)
                self._complete_operation(
                    operation,
                    "failed",
                    resource=ResourceRef("kernel", next_kernel_id),
                    failure=failure,
                    cleanup=cleanup,
                )
                raise SupervisorError(503, failure) from exc

            next_kernel = KernelResource(
                kernel_id=next_kernel_id,
                notebook_id=next_notebook_id,
                kernel_name=kernel_name,
                state="on",
                pid=next_handle.pid,
            )
            next_runtime = NotebookRuntime(
                runtime_id=next_runtime_id,
                notebook_id=next_notebook_id,
                discovery_id=discovery_id,
                kernel_id=next_kernel_id,
                plugin_catalog=discovery.catalog,
            )
            if self._plugin_workers is not None:
                self._plugin_workers.activate_runtime(next_runtime)
            with self._state_lock:
                self._current_kernel = next_kernel
                self._kernel_handle = next_handle
                self._current_runtime = next_runtime
            self.events.append(
                trace_id=trace_id,
                layer="kernel",
                operation="restart_notebook",
                kind="kernel.state_changed",
                resource=ResourceRef("kernel", next_kernel_id),
                payload={"kernel_id": next_kernel_id, "previous_state": "off", "state": "on"},
            )
            self._complete_operation(
                operation,
                "succeeded",
                resource=ResourceRef("notebook_runtime", next_runtime_id),
                cleanup=cleanup,
            )
            return {
                "operation": operation.to_dict(),
                "runtime": next_runtime.to_dict(),
                "kernel": next_kernel.to_dict(),
                "cleanup": cleanup,
            }

    def close(self) -> None:
        with self._state_lock:
            kernel = self._current_kernel
        if kernel is not None and kernel.state == "on":
            try:
                self.stop_kernel(kernel_id=kernel.kernel_id, trace_id=new_id("trace"))
            except SupervisorError:
                handle = self._kernel_handle
                if handle is not None:
                    try:
                        handle.stop(timeout=1.0)
                    except Exception:
                        pass

    def _require_live_kernel(self, kernel_id: str, trace_id: str, operation: str) -> tuple[KernelResource, KernelHandle]:
        if (
            self._current_kernel is None
            or self._current_kernel.kernel_id != kernel_id
            or self._current_kernel.state != "on"
            or self._kernel_handle is None
        ):
            raise self._request_failure(
                status_code=409 if kernel_id in self._known_kernel_ids else 404,
                trace_id=trace_id,
                layer="supervisor",
                operation=operation,
                reason="conflict" if kernel_id in self._known_kernel_ids else "not_found",
                message=f"Kernel {kernel_id} is not on",
                retryable=False,
                scope="request",
                resource=ResourceRef("kernel", kernel_id),
            )
        return self._current_kernel, self._kernel_handle

    def _begin_operation(self, kind: str, trace_id: str, *, resource: ResourceRef | None = None) -> Operation:
        operation = Operation(new_id("op"), trace_id, kind)
        self.events.append(
            trace_id=trace_id,
            layer="supervisor",
            operation=kind,
            kind="operation.started",
            resource=resource or ResourceRef("supervisor", self.supervisor_id),
            payload=operation.to_dict(),
        )
        return operation

    def _complete_operation(
        self,
        operation: Operation,
        outcome: str,
        *,
        resource: ResourceRef,
        failure: Failure | None = None,
        cleanup: dict[str, Any] | None = None,
    ) -> None:
        operation.complete(outcome)  # type: ignore[arg-type]
        payload = operation.to_dict()
        if failure is not None:
            payload["failure_id"] = failure.failure_id
        if cleanup is not None:
            payload["cleanup"] = cleanup
        self.events.append(
            trace_id=operation.trace_id,
            layer="supervisor",
            operation=operation.kind,
            kind="operation.completed",
            resource=resource,
            payload=payload,
        )

    def _failure(
        self,
        *,
        trace_id: str,
        layer: str,
        operation: str,
        reason: str,
        message: str,
        retryable: bool,
        scope: str,
        resource: ResourceRef,
        process=None,
        details: dict[str, Any] | None = None,
        caused_by_failure_id: str = "",
    ) -> Failure:
        return Failure(
            failure_id=new_id("fail"),
            trace_id=trace_id,
            layer=layer,
            operation=operation,
            reason=reason,
            message=message,
            retryable=retryable,
            scope=scope,
            resource=resource,
            process=process,
            details=details or {},
            caused_by_failure_id=caused_by_failure_id,
        )

    def _discovery_failure(
        self,
        exc: PluginCatalogDiscoveryError,
        *,
        trace_id: str,
        operation: str,
        discovery_id: str,
        teardown_completed: bool | None,
        next_notebook_id: str = "",
    ) -> Failure:
        details: dict[str, Any] = {}
        if teardown_completed is not None:
            details["teardown_completed"] = teardown_completed
        if next_notebook_id:
            details["next_notebook_id"] = next_notebook_id
        if exc.entry_point:
            details["entry_point"] = exc.entry_point
        if exc.distribution:
            details["distribution"] = exc.distribution
        return self._failure(
            trace_id=trace_id,
            layer="plugin_discovery",
            operation=operation,
            reason=exc.reason,
            message=str(exc),
            retryable=exc.retryable,
            scope="plugin_discovery",
            resource=ResourceRef("plugin_discovery", discovery_id),
            process=exc.diagnostics,
            details=details,
        )

    @staticmethod
    def _kernel_adapter_specs(catalog: dict[str, Any]) -> tuple[KernelAdapterSpec, ...]:
        return tuple(
            KernelAdapterSpec(
                plugin_id=plugin["plugin_id"],
                plugin_version=plugin["plugin_version"],
                module=module,
                families=tuple(
                    (family["family_id"], family["magic_name"])
                    for family in plugin["families"]
                ),
            )
            for plugin in catalog["plugins"]
            for module in plugin["kernel_extensions"]
        )

    @staticmethod
    def _handoff_matches_catalog(runtime: NotebookRuntime, handoff: Any) -> bool:
        plugin = next(
            (
                item for item in runtime.plugin_catalog["plugins"]
                if item["plugin_id"] == handoff.plugin_id
                and item["plugin_version"] == handoff.plugin_version
            ),
            None,
        )
        if plugin is None:
            return False
        return any(
            family["family_id"] == handoff.family_id
            and family["magic_name"] == handoff.magic_name
            for family in plugin["families"]
        )

    def _emit_failure(self, failure: Failure) -> None:
        self.events.append(
            trace_id=failure.trace_id,
            layer=failure.layer,
            operation=failure.operation,
            kind="failure.occurred",
            resource=failure.resource,
            payload=failure.to_dict(),
        )

    def _request_failure(self, *, status_code: int, **kwargs) -> SupervisorError:
        failure = self._failure(**kwargs)
        self._emit_failure(failure)
        return SupervisorError(status_code, failure)
