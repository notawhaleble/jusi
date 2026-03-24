from __future__ import annotations

from typing import List, Optional

from jusi.application.ports import BindPreparedClientCommand, ExecuteCellCommand, InterruptCellCommand, StartSessionCommand
from jusi.application.ports import DisconnectSessionCommand, ReconnectSessionCommand, StopSessionCommand
from jusi.application.ports import ShutdownClientCommand
from jusi.application.use_cases import BindPreparedClient, DisconnectSession, ExecuteCell, InterruptCell, ReconnectSession, ShutdownClient, StopSession, StartSession
from jusi.domain.models import ExecutableCell
from jusi.infrastructure.runtime import InMemoryKernelRuntime, InMemorySessionStore, build_runtime
from jusi.interfaces.protocol import (
    Envelope,
    ProtocolError,
    dump_envelopes,
    error_response,
    parse_bind_prepared_client,
    parse_disconnect_session,
    parse_envelope,
    parse_execute_cell,
    parse_interrupt_cell,
    parse_reconnect_session,
    parse_shutdown_client,
    parse_start_session,
    parse_stop_session,
    response_envelope,
)


class ProtocolEventSink:
    def __init__(self) -> None:
        self._events: List[Envelope] = []

    @property
    def events(self) -> List[Envelope]:
        return list(self._events)

    def session_updated(self, notebook_id: str, payload: dict) -> None:
        self._events.append(
            Envelope(
                version=1,
                kind="event",
                type="session_updated",
                payload={"notebook_id": notebook_id, "session": payload},
            )
        )

    def prepared_updated(self, notebook_id: str, payload: dict) -> None:
        self._events.append(
            Envelope(
                version=1,
                kind="event",
                type="prepared_updated",
                payload={"notebook_id": notebook_id, "prepared": payload},
            )
        )

    def cell_updated(self, notebook_id: str, payload: dict) -> None:
        self._events.append(
            Envelope(
                version=1,
                kind="event",
                type="cell_updated",
                payload={"notebook_id": notebook_id, "cell": payload},
            )
        )


class ProtocolServer:
    def __init__(self, runtime: Optional[InMemoryKernelRuntime] = None) -> None:
        self._runtime = runtime or build_runtime()
        self._store = InMemorySessionStore()

    def handle_message(self, raw: str) -> List[str]:
        request = parse_envelope(raw)
        if request.kind != "request":
            raise ProtocolError("Server expects request envelopes")
        if request.type == "start_session":
            return self._handle_start_session(request)
        if request.type == "execute_cell":
            return self._handle_execute_cell(request)
        if request.type == "interrupt_cell":
            return self._handle_interrupt_cell(request)
        if request.type == "disconnect_session":
            return self._handle_disconnect_session(request)
        if request.type == "reconnect_session":
            return self._handle_reconnect_session(request)
        if request.type == "stop_session":
            return self._handle_stop_session(request)
        if request.type == "bind_prepared_client":
            return self._handle_bind_prepared_client(request)
        if request.type == "shutdown_client":
            return self._handle_shutdown_client(request)
        return dump_envelopes([error_response(request, "unknown_request", "Unknown request type")])

    def _handle_start_session(self, request: Envelope) -> List[str]:
        start_request = parse_start_session(request.payload)
        events = ProtocolEventSink()
        use_case = StartSession(runtime=self._runtime, store=self._store, events=events)
        use_case.execute(
            StartSessionCommand(
                notebook_id=start_request.notebook_id,
                kernel_name=start_request.kernel_name,
            )
        )
        envelopes = [response_envelope(request, ok=True)]
        envelopes.extend(events.events)
        return dump_envelopes(envelopes)

    def _handle_interrupt_cell(self, request: Envelope) -> List[str]:
        interrupt_request = parse_interrupt_cell(request.payload)
        events = ProtocolEventSink()
        use_case = InterruptCell(runtime=self._runtime, store=self._store, events=events)
        try:
            use_case.execute(
                InterruptCellCommand(
                    notebook_id=interrupt_request.notebook_id,
                    session_id=interrupt_request.session_id,
                    cell_id=interrupt_request.cell_id,
                )
            )
        except ValueError as exc:
            return dump_envelopes([error_response(request, "invalid_state", str(exc))])
        envelopes = [response_envelope(request, ok=True)]
        envelopes.extend(events.events)
        return dump_envelopes(envelopes)

    def _handle_disconnect_session(self, request: Envelope) -> List[str]:
        disconnect_request = parse_disconnect_session(request.payload)
        events = ProtocolEventSink()
        use_case = DisconnectSession(store=self._store, events=events)
        try:
            use_case.execute(
                DisconnectSessionCommand(
                    notebook_id=disconnect_request.notebook_id,
                    session_id=disconnect_request.session_id,
                    reason=disconnect_request.reason,
                )
            )
        except ValueError as exc:
            return dump_envelopes([error_response(request, "invalid_state", str(exc))])
        envelopes = [response_envelope(request, ok=True)]
        envelopes.extend(events.events)
        return dump_envelopes(envelopes)

    def _handle_reconnect_session(self, request: Envelope) -> List[str]:
        reconnect_request = parse_reconnect_session(request.payload)
        events = ProtocolEventSink()
        use_case = ReconnectSession(runtime=self._runtime, store=self._store, events=events)
        try:
            use_case.execute(
                ReconnectSessionCommand(
                    notebook_id=reconnect_request.notebook_id,
                    session_id=reconnect_request.session_id,
                )
            )
        except ValueError as exc:
            return dump_envelopes([error_response(request, "invalid_state", str(exc))])
        envelopes = [response_envelope(request, ok=True)]
        envelopes.extend(events.events)
        return dump_envelopes(envelopes)

    def _handle_execute_cell(self, request: Envelope) -> List[str]:
        execute_request = parse_execute_cell(request.payload)
        events = ProtocolEventSink()
        use_case = ExecuteCell(runtime=self._runtime, store=self._store, events=events)
        try:
            use_case.execute(
                ExecuteCellCommand(
                    notebook_id=execute_request.notebook_id,
                    session_id=execute_request.session_id,
                    cell=ExecutableCell(
                        cell_id=execute_request.cell_id,
                        kind=execute_request.kind,
                        syntax=execute_request.syntax,
                        main_lines=execute_request.main_lines,
                        keep_running=execute_request.keep_running,
                    ),
                )
            )
        except ValueError as exc:
            return dump_envelopes([error_response(request, "invalid_state", str(exc))])
        envelopes = [response_envelope(request, ok=True)]
        envelopes.extend(events.events)
        return dump_envelopes(envelopes)

    def _handle_stop_session(self, request: Envelope) -> List[str]:
        stop_request = parse_stop_session(request.payload)
        events = ProtocolEventSink()
        use_case = StopSession(runtime=self._runtime, store=self._store, events=events)
        try:
            use_case.execute(
                StopSessionCommand(
                    notebook_id=stop_request.notebook_id,
                    session_id=stop_request.session_id,
                )
            )
        except ValueError as exc:
            return dump_envelopes([error_response(request, "invalid_state", str(exc))])
        envelopes = [response_envelope(request, ok=True)]
        envelopes.extend(events.events)
        return dump_envelopes(envelopes)

    def _handle_bind_prepared_client(self, request: Envelope) -> List[str]:
        bind_request = parse_bind_prepared_client(request.payload)
        events = ProtocolEventSink()
        use_case = BindPreparedClient(store=self._store, events=events)
        try:
            use_case.execute(
                BindPreparedClientCommand(
                    notebook_id=bind_request.notebook_id,
                    session_id=bind_request.session_id,
                    client_id=bind_request.client_id,
                    client_bufnr=bind_request.client_bufnr,
                )
            )
        except ValueError as exc:
            return dump_envelopes([error_response(request, "invalid_state", str(exc))])
        envelopes = [response_envelope(request, ok=True)]
        envelopes.extend(events.events)
        return dump_envelopes(envelopes)

    def _handle_shutdown_client(self, request: Envelope) -> List[str]:
        shutdown_request = parse_shutdown_client(request.payload)
        events = ProtocolEventSink()
        use_case = ShutdownClient(runtime=self._runtime, store=self._store, events=events)
        try:
            use_case.execute(
                ShutdownClientCommand(
                    notebook_id=shutdown_request.notebook_id,
                    session_id=shutdown_request.session_id,
                    cell_id=shutdown_request.cell_id,
                    client_id=shutdown_request.client_id,
                    reason=shutdown_request.reason,
                )
            )
        except ValueError as exc:
            return dump_envelopes([error_response(request, "invalid_state", str(exc))])
        envelopes = [response_envelope(request, ok=True)]
        envelopes.extend(events.events)
        return dump_envelopes(envelopes)
