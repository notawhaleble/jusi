from __future__ import annotations

from typing import Any, Callable

from jusi.infrastructure.client_runtime_updates import ClientRuntimeUpdate
from jusi.infrastructure.debug_timing import emit_timing
from jusi.infrastructure.handler_worker_startup import HandlerWorkerStartup
from jusi.plugins import HandlerContext, build_display_handler_registry


class InProcessHandlerController:
    def __init__(
        self,
        *,
        startup: HandlerWorkerStartup,
        handle_runtime_update: Callable[[ClientRuntimeUpdate], None],
        invoke_backend_action: Callable[[str, dict[str, Any]], dict[str, Any]],
        on_exit: Callable[[str], None],
    ) -> None:
        self._startup = startup
        self._handle_runtime_update = handle_runtime_update
        self._invoke_backend_action = invoke_backend_action
        self._on_exit = on_exit
        self._closed = False
        self._status = "follow-up"
        registry = build_display_handler_registry()
        spec = registry.get(startup.handler_id)
        if spec is None:
            raise RuntimeError(f"unknown handler id: {startup.handler_id}")
        self._handler = spec.factory()
        self._context = self._build_context()
        self._execute_initial()

    def wait_started(self, timeout: float = 5.0) -> str:
        _ = timeout
        return self._status

    def on_frontend_message(self, _context: object, message_type: str, payload: dict[str, Any]) -> None:
        emit_timing(
            "handler_worker.frontend_message.recv",
            client_id=self._startup.client_id,
            handler_id=self._startup.handler_id,
            message_type=message_type,
            payload_keys=sorted(list(payload.keys())),
        )
        self._handler.on_frontend_message(self._context, message_type, payload)

    def interrupt(self) -> None:
        interrupt = getattr(self._handler, "interrupt", None)
        if callable(interrupt):
            interrupt()

    def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._handler.stop()

    def _execute_initial(self) -> None:
        try:
            self._status = self._handler.execute(self._context, self._startup.cell)
        except Exception as exc:
            self._handle_runtime_update(
                ClientRuntimeUpdate.execution_event(
                    {
                        "type": "error",
                        "ename": exc.__class__.__name__,
                        "evalue": str(exc),
                        "traceback": [],
                    }
                )
            )
            self._handle_runtime_update(ClientRuntimeUpdate.execution_status("error"))
            self._status = "error"

    def _build_context(self) -> HandlerContext:
        startup = self._startup

        class RuntimeFrontendChannel:
            def emit_event(_self, event_type: str, payload: dict[str, Any]) -> None:
                self._handle_runtime_update(ClientRuntimeUpdate.channel_event(event_type, payload))

            def request_action(_self, action_type: str, payload: dict[str, Any]) -> None:
                self._handle_runtime_update(ClientRuntimeUpdate.action_request(action_type, payload))

        return HandlerContext(
            notebook_id=startup.notebook_id,
            session_id=startup.session_id,
            cell_id=startup.cell_id,
            client_id=startup.client_id,
            channel=RuntimeFrontendChannel(),
            push_frontend_message=lambda message_type, payload: self._handle_runtime_update(
                ClientRuntimeUpdate.live_handler_message(message_type, payload)
            ),
            invoke_backend_action=self._invoke_backend_action,
            append_execution_event=lambda event: self._handle_runtime_update(
                ClientRuntimeUpdate.execution_event(event)
            ),
            update_execution_status=lambda status: self._handle_runtime_update(
                ClientRuntimeUpdate.execution_status(status)
            ),
            set_client_transport=lambda transport: self._handle_runtime_update(
                ClientRuntimeUpdate.transport(transport)
            ),
            magic_name=startup.magic_name,
            content=startup.content,
            meta=dict(startup.meta or {}),
        )
