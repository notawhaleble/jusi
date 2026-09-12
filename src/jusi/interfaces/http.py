from __future__ import annotations

import asyncio
import json
import queue
from typing import Any

import tornado.web
import tornado.websocket
from tornado.iostream import StreamClosedError

from jusi.application.events import EventCursorExpired
from jusi.application.editor_actions import EditorActionError
from jusi.application.supervisor import Supervisor, SupervisorError, new_id
from jusi.application.terminal_surfaces import (
    TerminalAttachment,
    TerminalChunk,
    TerminalStreamFailure,
    TerminalSurfaceError,
)
from jusi.domain.models import Failure, ResourceRef
from jusi.protocol import (
    ProtocolValidationError,
    encode_terminal_output_frame,
    validate_command,
    validate_terminal_stream_control,
)


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


class ExecutionInterruptHandler(BaseHandler):
    async def post(self, kernel_id: str, execution_id: str) -> None:
        command = self.parse_command("interrupt")
        if command is None:
            return
        if command["kernel_id"] != kernel_id or command["execution_id"] != execution_id:
            failure = Failure(
                failure_id=new_id("fail"),
                trace_id=command["trace_id"],
                layer="protocol",
                operation="interrupt",
                reason="invalid_request",
                message="kernel_id and execution_id in the body must match the URL",
                retryable=False,
                scope="request",
                resource=ResourceRef("execution", execution_id),
            )
            self.supervisor.record_failure(failure)
            self.write_json(400, {"ok": False, "failure": failure.to_dict()})
            return
        try:
            result = await asyncio.to_thread(
                self.supervisor.interrupt_execution,
                kernel_id=kernel_id,
                execution_id=execution_id,
                trace_id=command["trace_id"],
            )
        except SupervisorError as exc:
            self.write_supervisor_error(exc)
            return
        self.write_json(202, {"ok": True, **result})


class ExecutionInputHandler(BaseHandler):
    async def post(self, kernel_id: str, execution_id: str) -> None:
        command = self.parse_command("submit_input")
        if command is None:
            return
        if command["kernel_id"] != kernel_id or command["execution_id"] != execution_id:
            failure = Failure(
                failure_id=new_id("fail"), trace_id=command["trace_id"], layer="protocol",
                operation="submit_input", reason="invalid_request",
                message="Input reply identities must match the URL", retryable=False,
                scope="request", resource=ResourceRef("execution", execution_id),
            )
            self.supervisor.record_failure(failure)
            self.write_json(400, {"ok": False, "failure": failure.to_dict()})
            return
        try:
            result = await asyncio.to_thread(
                self.supervisor.submit_input, kernel_id=kernel_id, execution_id=execution_id,
                input_request_id=command["input_request_id"], value=command["value"],
                trace_id=command["trace_id"],
            )
        except SupervisorError as exc:
            self.write_supervisor_error(exc)
            return
        self.write_json(200, {"ok": True, **result})


class CompletionHandler(BaseHandler):
    async def post(self, kernel_id: str) -> None:
        command = self.parse_command("complete")
        if command is None:
            return
        if command["kernel_id"] != kernel_id:
            failure = Failure(
                failure_id=new_id("fail"),
                trace_id=command["trace_id"],
                layer="protocol",
                operation="complete",
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
                self.supervisor.complete,
                kernel_id=kernel_id, notebook_id=command["notebook_id"], cell_id=command["cell_id"],
                cursor_pos=command["cursor_pos"], client_id=command.get("client_id"),
                body=command["body"],
                trace_id=command["trace_id"],
            )
        except SupervisorError as exc:
            self.write_supervisor_error(exc)
            return
        self.write_json(200, {"ok": True, **result})


class ClientFollowupHandler(BaseHandler):
    async def post(self, client_id: str) -> None:
        command = self.parse_command("followup")
        if command is None:
            return
        if command["client_id"] != client_id:
            failure = Failure(
                failure_id=new_id("fail"),
                trace_id=command["trace_id"],
                layer="protocol",
                operation="followup",
                reason="invalid_request",
                message="client_id in the body must match the URL",
                retryable=False,
                scope="request",
                resource=ResourceRef("client", client_id),
            )
            self.supervisor.record_failure(failure)
            self.write_json(400, {"ok": False, "failure": failure.to_dict()})
            return
        try:
            result = await asyncio.to_thread(
                self.supervisor.followup,
                client_id=client_id,
                body=command["body"],
                trace_id=command["trace_id"],
            )
        except SupervisorError as exc:
            self.write_supervisor_error(exc)
            return
        self.write_json(200, {"ok": True, **result})


class ClientEditorActionHandler(BaseHandler):
    async def post(self, client_id: str) -> None:
        command = self.parse_command("editor_action")
        if command is None:
            return
        if command["client_id"] != client_id:
            failure = Failure(failure_id=new_id("fail"), trace_id=command["trace_id"], layer="protocol",
                operation="editor_action", reason="invalid_request", message="Client identity must match URL",
                retryable=False, scope="request", resource=ResourceRef("client", client_id))
            self.supervisor.record_failure(failure)
            self.write_json(400, {"ok": False, "failure": failure.to_dict()})
            return
        try:
            result = await asyncio.to_thread(self.supervisor.editor_action, client_id=client_id,
                action=command["action"], selection=command["selection"], trace_id=command["trace_id"])
        except SupervisorError as exc:
            self.write_supervisor_error(exc)
            return
        self.write_json(200, {"ok": True, **result})


class ClientInterruptHandler(BaseHandler):
    async def post(self, client_id: str) -> None:
        command = self.parse_command("interrupt_client")
        if command is None:
            return
        if command["client_id"] != client_id:
            failure = Failure(failure_id=new_id("fail"), trace_id=command["trace_id"], layer="protocol",
                operation="interrupt_client", reason="invalid_request", message="Client identity must match URL",
                retryable=False, scope="request", resource=ResourceRef("client", client_id))
            self.supervisor.record_failure(failure)
            self.write_json(400, {"ok": False, "failure": failure.to_dict()})
            return
        try:
            result = await asyncio.to_thread(self.supervisor.interrupt_client,
                client_id=client_id, operation_id=command["operation_id"], trace_id=command["trace_id"])
        except SupervisorError as exc:
            self.write_supervisor_error(exc)
            return
        self.write_json(202, {"ok": True, **result})


class ClientHandler(BaseHandler):
    async def delete(self, client_id: str) -> None:
        command = self.parse_command("close_client")
        if command is None:
            return
        if command["client_id"] != client_id:
            failure = Failure(
                failure_id=new_id("fail"),
                trace_id=command["trace_id"],
                layer="protocol",
                operation="close_client",
                reason="invalid_request",
                message="client_id in the body must match the URL",
                retryable=False,
                scope="request",
                resource=ResourceRef("client", client_id),
            )
            self.supervisor.record_failure(failure)
            self.write_json(400, {"ok": False, "failure": failure.to_dict()})
            return
        try:
            result = await asyncio.to_thread(
                self.supervisor.close_client,
                client_id=client_id,
                trace_id=command["trace_id"],
            )
        except SupervisorError as exc:
            self.write_supervisor_error(exc)
            return
        self.write_json(200, {"ok": True, **result})


class NotebookRuntimeRestartHandler(BaseHandler):
    async def post(self, runtime_id: str) -> None:
        command = self.parse_command("restart_notebook")
        if command is None:
            return
        if command["runtime_id"] != runtime_id:
            failure = Failure(
                failure_id=new_id("fail"),
                trace_id=command["trace_id"],
                layer="protocol",
                operation="restart_notebook",
                reason="invalid_request",
                message="runtime_id in the body must match the URL",
                retryable=False,
                scope="request",
                resource=ResourceRef("notebook_runtime", runtime_id),
            )
            self.supervisor.record_failure(failure)
            self.write_json(400, {"ok": False, "failure": failure.to_dict()})
            return
        try:
            result = await asyncio.to_thread(
                self.supervisor.restart_notebook,
                runtime_id=runtime_id,
                kernel_id=command["kernel_id"],
                notebook_id=command["notebook_id"],
                next_notebook_id=command["next_notebook_id"],
                kernel_name=command["kernel_name"],
                trace_id=command["trace_id"],
            )
        except SupervisorError as exc:
            self.write_supervisor_error(exc)
            return
        self.write_json(200, {"ok": True, **result})


class EditorActionDeliveryHandler(BaseHandler):
    def action_error(self, action_id: str, exc: EditorActionError) -> None:
        failure = Failure(failure_id=new_id("fail"), trace_id=new_id("trace"), layer="client",
            operation="editor_action", reason=exc.reason, message=str(exc), retryable=False,
            scope="request", resource=ResourceRef("supervisor", self.supervisor.supervisor_id))
        self.write_json(409, {"ok": False, "failure": failure.to_dict()})

    def get(self, action_id: str) -> None:
        try:
            offset = self.get_query_argument("offset", None)
            if offset is not None and (not offset.isdecimal() or len(offset) > 16):
                raise EditorActionError("Invalid text offset", "invalid_request")
            result = self.supervisor.editor_actions.fetch(action_id, self.get_query_argument("editor_id", ""),
                                                          int(offset) if offset is not None else None)
        except EditorActionError as exc:
            self.action_error(action_id, exc)
            return
        self.write_json(200, {"ok": True, **result})

    def post(self, action_id: str) -> None:
        command = self.parse_command("ack_editor_action")
        if command is None:
            return
        try:
            if command["action_id"] != action_id:
                raise EditorActionError("Action identity must match URL", "invalid_request")
            result = self.supervisor.editor_actions.acknowledge(action_id, command["editor_id"], command["outcome"])
        except EditorActionError as exc:
            self.action_error(action_id, exc)
            return
        self.write_json(200, {"ok": True, "delivery": result})


class EventsHandler(BaseHandler):
    def initialize(self, supervisor: Supervisor) -> None:
        super().initialize(supervisor)
        self._connection_closed = False
        self.editor_id = ""
        self.connection_id = new_id("econn")

    def on_connection_close(self) -> None:
        self._connection_closed = True
        self.supervisor.editor_actions.disconnect(self.editor_id, self.connection_id)

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

        self.editor_id = self.get_query_argument("editor_id", "")
        if self.editor_id and (len(self.editor_id) > 128 or not self.editor_id.replace("_", "").isalnum()):
            self.write_json(400, {"ok": False, "error": "invalid editor_id"})
            return
        if self.editor_id:
            self.supervisor.editor_actions.connect(self.editor_id, self.connection_id)
        self.set_header("Content-Type", "text/event-stream; charset=utf-8")
        self.set_header("Cache-Control", "no-cache")
        self.set_header("X-Accel-Buffering", "no")
        cursor = after
        try:
            self.write(": connected\n\n")
            await self.flush()
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
        finally:
            self.supervisor.editor_actions.disconnect(self.editor_id, self.connection_id)


class TerminalSurfaceHandler(tornado.websocket.WebSocketHandler):
    def initialize(self, supervisor: Supervisor) -> None:
        self.supervisor = supervisor
        self.surface_id = ""
        self.attachment_id = ""
        self.attachment: TerminalAttachment | None = None
        self._pump_task: asyncio.Task[None] | None = None

    def select_subprotocol(self, subprotocols: list[str]) -> str | None:
        return "jusi.terminal.v1" if "jusi.terminal.v1" in subprotocols else None

    def open(self, surface_id: str) -> None:
        self.surface_id = surface_id
        if self.selected_subprotocol != "jusi.terminal.v1":
            self.close(code=1002, reason="jusi.terminal.v1 subprotocol is required")

    async def on_message(self, message: str | bytes) -> None:
        if isinstance(message, bytes):
            if self.attachment is None:
                await self._failure("attach", "protocol_violation", "Terminal input arrived before attachment")
                return
            try:
                await asyncio.to_thread(
                    self.supervisor.write_terminal_surface,
                    self.surface_id,
                    self.attachment_id,
                    message,
                )
            except TerminalSurfaceError as exc:
                await self._failure("stream", self._wire_reason(exc.reason), str(exc))
            return
        try:
            control = validate_terminal_stream_control(json.loads(message))
        except (json.JSONDecodeError, ProtocolValidationError) as exc:
            await self._failure("attach" if self.attachment is None else "stream", "protocol_violation", str(exc))
            return
        if control["surface_id"] != self.surface_id:
            await self._failure("attach", "protocol_violation", "surface_id does not match the WebSocket URL")
            return
        if self.attachment is None:
            if control["kind"] != "attach":
                await self._failure("attach", "protocol_violation", "First terminal control must be attach")
                return
            self.attachment_id = control["attachment_id"]
            try:
                attachment, _ = await asyncio.to_thread(
                    self.supervisor.attach_terminal_surface,
                    self.surface_id,
                    attachment_id=self.attachment_id,
                    editor_id=control.get("editor_id"),
                    rows=control["rows"],
                    cols=control["columns"],
                    after_cursor=int(control["cursor"]),
                )
            except TerminalSurfaceError as exc:
                await self._failure("attach", self._wire_reason(exc.reason), str(exc))
                return
            self.attachment = attachment
            await self.write_message(json.dumps({
                "protocol_version": 1,
                "kind": "attached",
                "surface_id": self.surface_id,
                "attachment_id": self.attachment_id,
                "cursor": control["cursor"],
                "rows": control["rows"],
                "columns": control["columns"],
            }, separators=(",", ":")))
            self._pump_task = asyncio.create_task(self._pump())
            return
        if control["attachment_id"] != self.attachment_id:
            await self._failure("stream", "protocol_violation", "attachment_id is not authoritative")
            return
        if control["kind"] != "resize":
            await self._failure("stream", "protocol_violation", "Only resize is valid after attachment")
            return
        try:
            await asyncio.to_thread(
                self.supervisor.resize_terminal_surface,
                self.surface_id,
                self.attachment_id,
                rows=control["rows"],
                cols=control["columns"],
            )
        except TerminalSurfaceError as exc:
            await self._failure("resize", self._wire_reason(exc.reason), str(exc))
            return
        await self.write_message(json.dumps({
            **control,
            "kind": "resized",
        }, separators=(",", ":")))

    def on_close(self) -> None:
        if self._pump_task is not None:
            self._pump_task.cancel()
        if self.attachment_id:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._detach())
            except RuntimeError:
                pass

    async def _detach(self) -> None:
        try:
            await asyncio.to_thread(
                self.supervisor.detach_terminal_surface,
                self.surface_id,
                self.attachment_id,
            )
        except TerminalSurfaceError:
            # Surface retirement may win the race with WebSocket close.
            return

    async def _pump(self) -> None:
        assert self.attachment is not None
        while True:
            try:
                item = await asyncio.to_thread(self.attachment.chunks.get, True, 0.5)
            except queue.Empty:
                if self.ws_connection is None:
                    return
                continue
            if isinstance(item, TerminalStreamFailure):
                await self._failure("stream", self._wire_reason(item.reason), item.message)
                return
            assert isinstance(item, TerminalChunk)
            try:
                await self.write_message(encode_terminal_output_frame(item.cursor, item.data), binary=True)
            except tornado.websocket.WebSocketClosedError:
                return

    async def _failure(self, operation: str, reason: str, message: str) -> None:
        attachment_id = self.attachment_id or "att_unset"
        payload = {
            "protocol_version": 1,
            "kind": "failure",
            "surface_id": self.surface_id,
            "attachment_id": attachment_id,
            "operation": operation,
            "reason": reason,
            "message": message[:1000] or "Terminal stream failed",
            "retryable": reason in {"busy", "cursor_expired", "channel_closed"},
        }
        try:
            validate_terminal_stream_control(payload)
            await self.write_message(json.dumps(payload, separators=(",", ":")))
        except tornado.websocket.WebSocketClosedError:
            return

    @staticmethod
    def _wire_reason(reason: str) -> str:
        return reason if reason in {"busy", "cursor_expired", "not_found", "protocol_violation", "channel_closed"} else "channel_closed"


def make_application(supervisor: Supervisor) -> tornado.web.Application:
    handler_args = {"supervisor": supervisor}
    return tornado.web.Application(
        [
            (r"/v1/health", HealthHandler, handler_args),
            (r"/v1/events", EventsHandler, handler_args),
            (r"/v1/editor-actions/([^/]+)", EditorActionDeliveryHandler, handler_args),
            (r"/v1/surfaces/([^/]+)/terminal", TerminalSurfaceHandler, handler_args),
            (r"/v1/kernels", KernelsHandler, handler_args),
            (r"/v1/kernels/([^/]+)", KernelHandler, handler_args),
            (r"/v1/kernels/([^/]+)/executions", ExecutionsHandler, handler_args),
            (r"/v1/kernels/([^/]+)/executions/([^/]+)/interrupt", ExecutionInterruptHandler, handler_args),
            (r"/v1/kernels/([^/]+)/executions/([^/]+)/input", ExecutionInputHandler, handler_args),
            (r"/v1/kernels/([^/]+)/completions", CompletionHandler, handler_args),
            (r"/v1/clients/([^/]+)/editor-actions", ClientEditorActionHandler, handler_args),
            (r"/v1/clients/([^/]+)/interrupt", ClientInterruptHandler, handler_args),
            (r"/v1/clients/([^/]+)/followups", ClientFollowupHandler, handler_args),
            (r"/v1/clients/([^/]+)", ClientHandler, handler_args),
            (r"/v1/notebook-runtimes/([^/]+)/restart", NotebookRuntimeRestartHandler, handler_args),
        ],
        compress_response=False,
    )
