from __future__ import annotations

import threading
import uuid
from typing import Any

from jusi.application.events import EventLog
from jusi.application.ports import KernelAdapterError, KernelFactory, KernelHandle
from jusi.domain.models import (
    ExecutionResource,
    Failure,
    KernelResource,
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
    def __init__(self, kernel_factory: KernelFactory) -> None:
        self.supervisor_id = new_id("sup")
        self.events = EventLog(self.supervisor_id)
        self._kernel_factory = kernel_factory
        self._lock = threading.RLock()
        self._current_kernel: KernelResource | None = None
        self._kernel_handle: KernelHandle | None = None
        self._known_kernel_ids: set[str] = set()
        self.events.append(
            trace_id=new_id("trace"),
            layer="service",
            operation="service_start",
            kind="service.ready",
            resource=ResourceRef("supervisor", self.supervisor_id),
            payload={"supervisor_id": self.supervisor_id},
        )

    def health(self) -> dict[str, Any]:
        with self._lock:
            return {
                "status": "ready",
                "supervisor_id": self.supervisor_id,
                "earliest_event_sequence": self.events.earliest_sequence,
                "event_sequence": self.events.latest_sequence,
                "kernel": self._current_kernel.to_dict() if self._current_kernel is not None else None,
            }

    def record_failure(self, failure: Failure) -> None:
        self._emit_failure(failure)

    def inspect_kernel(self, kernel_id: str) -> dict[str, Any]:
        with self._lock:
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
        with self._lock:
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
            kernel_id = new_id("krn")
            kernel_ref = ResourceRef("kernel", kernel_id)
            self._known_kernel_ids.add(kernel_id)
            try:
                handle = self._kernel_factory.start(kernel_name, timeout=timeout)
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
            self._current_kernel = kernel
            self._kernel_handle = handle
            self.events.append(
                trace_id=trace_id,
                layer="kernel",
                operation="start_kernel",
                kind="kernel.state_changed",
                resource=kernel_ref,
                payload={"kernel_id": kernel_id, "previous_state": "off", "state": "on"},
            )
            self._complete_operation(operation, "succeeded", resource=kernel_ref)
            return {"operation": operation.to_dict(), "kernel": kernel.to_dict()}

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
        with self._lock:
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
                )
                self._emit_failure(kernel_failure)
                if exc.layer == "kernel":
                    try:
                        handle.stop(timeout=1.0)
                    except KernelAdapterError:
                        # The authoritative state is already off because death was
                        # observed. Cleanup diagnostics remain attached to the
                        # originating kernel failure for this initial slice.
                        pass
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
                    details={"error_name": result.error_name, "error_value": result.error_value},
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
        with self._lock:
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
            self._complete_operation(operation, "succeeded", resource=ResourceRef("kernel", kernel_id), cleanup=cleanup)
            return {"operation": operation.to_dict(), "kernel": kernel.to_dict(), "cleanup": cleanup}

    def close(self) -> None:
        with self._lock:
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
