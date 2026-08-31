from __future__ import annotations

import asyncio
import json
from typing import Any

import tornado.web
from tornado.iostream import StreamClosedError

from jusi.application.events import EventCursorExpired
from jusi.application.supervisor import Supervisor, SupervisorError, new_id
from jusi.domain.models import Failure, ResourceRef
from jusi.protocol import ProtocolValidationError, validate_command


class BaseHandler(tornado.web.RequestHandler):
    def initialize(self, supervisor: Supervisor) -> None:
        self.supervisor = supervisor

    def write_json(self, status_code: int, payload: dict[str, Any]) -> None:
        self.set_status(status_code)
        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.finish(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))

    def parse_command(self, expected_kind: str) -> dict[str, Any] | None:
        try:
            raw = json.loads(self.request.body.decode("utf-8"))
            return validate_command(raw, expected_kind)
        except (UnicodeDecodeError, json.JSONDecodeError, ProtocolValidationError) as exc:
            trace_id = new_id("trace")
            if isinstance(locals().get("raw"), dict):
                candidate = raw.get("trace_id")
                if isinstance(candidate, str) and candidate:
                    trace_id = candidate
            failure = Failure(
                failure_id=new_id("fail"),
                trace_id=trace_id,
                layer="protocol",
                operation=expected_kind,
                reason="invalid_request",
                message=str(exc),
                retryable=False,
                scope="request",
                resource=ResourceRef("supervisor", self.supervisor.supervisor_id),
            )
            self.supervisor.record_failure(failure)
            self.write_json(400, {"ok": False, "failure": failure.to_dict()})
            return None

    def write_supervisor_error(self, exc: SupervisorError) -> None:
        self.write_json(exc.status_code, {"ok": False, "failure": exc.failure.to_dict()})


class HealthHandler(BaseHandler):
    def get(self) -> None:
        self.write_json(200, {"ok": True, **self.supervisor.health()})


class KernelsHandler(BaseHandler):
    async def post(self) -> None:
        command = self.parse_command("start_kernel")
        if command is None:
            return
        try:
            result = await asyncio.to_thread(
                self.supervisor.start_kernel,
                notebook_id=command["notebook_id"],
                kernel_name=command["kernel_name"],
                trace_id=command["trace_id"],
            )
        except SupervisorError as exc:
            self.write_supervisor_error(exc)
            return
        self.write_json(201, {"ok": True, **result})


class KernelHandler(BaseHandler):
    def get(self, kernel_id: str) -> None:
        try:
            kernel = self.supervisor.inspect_kernel(kernel_id)
        except SupervisorError as exc:
            self.write_supervisor_error(exc)
            return
        self.write_json(200, {"ok": True, "kernel": kernel})

    async def delete(self, kernel_id: str) -> None:
        command = self.parse_command("stop_kernel")
        if command is None:
            return
        if command["kernel_id"] != kernel_id:
            failure = Failure(
                failure_id=new_id("fail"),
                trace_id=command["trace_id"],
                layer="protocol",
                operation="stop_kernel",
                reason="invalid_request",
                message="kernel_id in the body must match the URL",
                retryable=False,
                scope="request",
                resource=ResourceRef("kernel", kernel_id),
            )
            self.supervisor.record_failure(failure)
            self.write_json(400, {"ok": False, "failure": failure.to_dict()})
            return
        try:
            result = await asyncio.to_thread(
                self.supervisor.stop_kernel,
                kernel_id=kernel_id,
                trace_id=command["trace_id"],
            )
        except SupervisorError as exc:
            self.write_supervisor_error(exc)
            return
        self.write_json(200, {"ok": True, **result})


class ExecutionsHandler(BaseHandler):
    async def post(self, kernel_id: str) -> None:
        command = self.parse_command("execute")
        if command is None:
            return
        if command["kernel_id"] != kernel_id:
            failure = Failure(
                failure_id=new_id("fail"),
                trace_id=command["trace_id"],
                layer="protocol",
                operation="execute",
                reason="invalid_request",
                message="kernel_id in the body must match the URL",
                retryable=False,
                scope="request",
                resource=ResourceRef("kernel", kernel_id),
            )
            self.supervisor.record_failure(failure)
            self.write_json(400, {"ok": False, "failure": failure.to_dict()})
            return
        try:
            result = await asyncio.to_thread(
                self.supervisor.execute,
                kernel_id=kernel_id,
                notebook_id=command["notebook_id"],
                cell_id=command["cell_id"],
                code=command["code"],
                trace_id=command["trace_id"],
            )
        except SupervisorError as exc:
            self.write_supervisor_error(exc)
            return
        self.write_json(200, {"ok": True, **result})


class EventsHandler(BaseHandler):
    def initialize(self, supervisor: Supervisor) -> None:
        super().initialize(supervisor)
        self._connection_closed = False

    def on_connection_close(self) -> None:
        self._connection_closed = True

    async def get(self) -> None:
        try:
            after = int(self.get_query_argument("after", "0"))
            if after < 0:
                raise ValueError
        except ValueError:
            self.write_json(400, {"ok": False, "error": "after must be a non-negative integer"})
            return
        try:
            pending = self.supervisor.events.events_after(after)
        except EventCursorExpired as exc:
            self.write_json(409, {"ok": False, "error": "event_cursor_expired", "earliest_sequence": exc.earliest})
            return

        self.set_header("Content-Type", "text/event-stream; charset=utf-8")
        self.set_header("Cache-Control", "no-cache")
        self.set_header("X-Accel-Buffering", "no")
        self.write(": connected\n\n")
        await self.flush()

        cursor = after
        try:
            while not self._connection_closed:
                if not pending:
                    pending = await asyncio.to_thread(self.supervisor.events.wait_after, cursor, timeout=1.0)
                if not pending:
                    self.write(": keepalive\n\n")
                    await self.flush()
                    continue
                for event in pending:
                    cursor = int(event["sequence"])
                    self.write(f"id: {event['event_id']}\n")
                    self.write(f"event: {event['kind']}\n")
                    self.write("data: " + json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n\n")
                    await self.flush()
                pending = []
        except (StreamClosedError, asyncio.CancelledError):
            return


def make_application(supervisor: Supervisor) -> tornado.web.Application:
    handler_args = {"supervisor": supervisor}
    return tornado.web.Application(
        [
            (r"/v1/health", HealthHandler, handler_args),
            (r"/v1/events", EventsHandler, handler_args),
            (r"/v1/kernels", KernelsHandler, handler_args),
            (r"/v1/kernels/([^/]+)", KernelHandler, handler_args),
            (r"/v1/kernels/([^/]+)/executions", ExecutionsHandler, handler_args),
        ],
        compress_response=False,
    )
