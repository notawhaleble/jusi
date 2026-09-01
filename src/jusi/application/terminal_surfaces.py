from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import queue
import threading
from collections.abc import Callable
from typing import Any

from jusi.application.ports import (
    TerminalBroker,
    TerminalBrokerError,
    TerminalHandle,
    TerminalLaunchSpec,
    TerminalSurfaceRequest,
)
from jusi.domain.models import SurfaceResource
from jusi.domain.models import ProcessDiagnostics


@dataclass(frozen=True)
class TerminalChunk:
    cursor: int
    data: bytes


@dataclass(frozen=True)
class TerminalStreamFailure:
    reason: str
    message: str


@dataclass
class TerminalAttachment:
    surface_id: str
    attachment_id: str
    rows: int
    cols: int
    chunks: queue.Queue[TerminalChunk | TerminalStreamFailure]


class TerminalSurfaceError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        reason: str,
        details: dict[str, Any] | None = None,
        diagnostics: ProcessDiagnostics | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.details = details or {}
        self.diagnostics = diagnostics


@dataclass
class _OwnedSurface:
    resource: SurfaceResource
    request: TerminalSurfaceRequest
    lock: threading.RLock = field(default_factory=threading.RLock)
    handle: TerminalHandle | None = None
    reader: threading.Thread | None = None
    stop_reader: threading.Event = field(default_factory=threading.Event)
    replay: deque[TerminalChunk] = field(default_factory=deque)
    replay_bytes: int = 0
    next_cursor: int = 0
    attachment: TerminalAttachment | None = None
    closing: bool = False
    fatal_reported: bool = False


class TerminalSurfaceManager:
    """Owns target PTYs and byte continuity independently from HTTP/SSE."""

    def __init__(
        self,
        broker: TerminalBroker,
        *,
        replay_limit: int = 1024 * 1024,
        attachment_queue_limit: int = 512,
        on_fatal: Callable[[SurfaceResource, TerminalSurfaceError], None] | None = None,
    ) -> None:
        if replay_limit < 1 or attachment_queue_limit < 1:
            raise ValueError("terminal stream limits must be positive")
        self._broker = broker
        self._replay_limit = replay_limit
        self._attachment_queue_limit = attachment_queue_limit
        self._lock = threading.RLock()
        self._surfaces: dict[str, _OwnedSurface] = {}
        self._known_surface_ids: set[str] = set()
        self._on_fatal = on_fatal

    def set_fatal_handler(
        self, handler: Callable[[SurfaceResource, TerminalSurfaceError], None],
    ) -> None:
        self._on_fatal = handler

    def prepare(self, resource: SurfaceResource, request: TerminalSurfaceRequest) -> None:
        owned = _OwnedSurface(resource=resource, request=request)
        with self._lock:
            if resource.surface_id in self._known_surface_ids:
                raise TerminalSurfaceError("Terminal surface identity already exists", reason="conflict")
            self._known_surface_ids.add(resource.surface_id)
            self._surfaces[resource.surface_id] = owned

    def attach(
        self,
        surface_id: str,
        *,
        attachment_id: str,
        rows: int,
        cols: int,
        after_cursor: int,
    ) -> tuple[TerminalAttachment, dict[str, int]]:
        owned = self._owned(surface_id)
        with owned.lock:
            if owned.closing:
                raise TerminalSurfaceError("Terminal surface is closing", reason="channel_closed")
            if owned.attachment is not None:
                raise TerminalSurfaceError("Terminal surface already has an input attachment", reason="busy")
            if after_cursor < self._earliest_cursor(owned):
                raise TerminalSurfaceError(
                    "Terminal stream cursor is no longer available",
                    reason="cursor_expired",
                    details={"earliest_cursor": self._earliest_cursor(owned), "latest_cursor": owned.next_cursor},
                )
            if after_cursor > owned.next_cursor:
                raise TerminalSurfaceError("Terminal stream cursor is ahead of the surface", reason="protocol_violation")
            if owned.handle is None:
                try:
                    owned.handle = self._broker.start(
                        TerminalLaunchSpec(
                            argv=owned.request.argv,
                            cwd=owned.request.cwd,
                            env=owned.request.environment_overrides,
                        ),
                        rows=rows,
                        cols=cols,
                    )
                except TerminalBrokerError as exc:
                    raise self._broker_failure(exc) from exc
                owned.reader = threading.Thread(
                    target=self._drain,
                    args=(owned,),
                    name=f"jusi-terminal-{surface_id}",
                    daemon=True,
                )
            else:
                try:
                    owned.handle.resize(rows=rows, cols=cols)
                except TerminalBrokerError as exc:
                    raise self._broker_failure(exc) from exc
            attachment = TerminalAttachment(
                surface_id=surface_id,
                attachment_id=attachment_id,
                rows=rows,
                cols=cols,
                chunks=queue.Queue(maxsize=self._attachment_queue_limit),
            )
            for chunk in owned.replay:
                end = chunk.cursor + len(chunk.data)
                if end <= after_cursor:
                    continue
                offset = max(0, after_cursor - chunk.cursor)
                try:
                    attachment.chunks.put_nowait(TerminalChunk(chunk.cursor + offset, chunk.data[offset:]))
                except queue.Full as exc:
                    raise TerminalSurfaceError(
                        "Terminal replay exceeds the attachment delivery window",
                        reason="channel_closed",
                        details={"earliest_cursor": self._earliest_cursor(owned), "latest_cursor": owned.next_cursor},
                    ) from exc
            owned.attachment = attachment
            if owned.reader is not None and not owned.reader.is_alive():
                owned.reader.start()
            return attachment, {
                "earliest_cursor": self._earliest_cursor(owned),
                "latest_cursor": owned.next_cursor,
            }

    def detach(self, surface_id: str, attachment_id: str) -> None:
        owned = self._owned(surface_id)
        with owned.lock:
            if owned.attachment is not None and owned.attachment.attachment_id == attachment_id:
                owned.attachment = None

    def write(self, surface_id: str, attachment_id: str, data: bytes) -> None:
        owned = self._attached(surface_id, attachment_id)
        assert owned.handle is not None
        try:
            owned.handle.write(data)
        except TerminalBrokerError as exc:
            raise self._broker_failure(exc) from exc

    def resize(self, surface_id: str, attachment_id: str, *, rows: int, cols: int) -> None:
        owned = self._attached(surface_id, attachment_id)
        assert owned.handle is not None and owned.attachment is not None
        try:
            owned.handle.resize(rows=rows, cols=cols)
        except TerminalBrokerError as exc:
            raise self._broker_failure(exc) from exc
        owned.attachment.rows = rows
        owned.attachment.cols = cols

    def close(self, surface_id: str, *, timeout: float) -> dict[str, Any]:
        with self._lock:
            owned = self._surfaces.get(surface_id)
            known = surface_id in self._known_surface_ids
        if owned is None:
            if known:
                return {"surface_id": surface_id, "result": "already_absent"}
            raise TerminalSurfaceError(f"Unknown terminal surface {surface_id}", reason="not_found")
        with owned.lock:
            owned.attachment = None
            owned.closing = True
            owned.stop_reader.set()
            handle = owned.handle
        try:
            result = "already_absent" if handle is None else handle.stop(timeout=timeout)
        except TerminalBrokerError as exc:
            raise self._broker_failure(exc) from exc
        if owned.reader is not None and owned.reader is not threading.current_thread():
            owned.reader.join(timeout=timeout)
        with self._lock:
            if self._surfaces.get(surface_id) is owned:
                self._surfaces.pop(surface_id)
        return {"surface_id": surface_id, "result": result}

    def _owned(self, surface_id: str) -> _OwnedSurface:
        with self._lock:
            owned = self._surfaces.get(surface_id)
        if owned is None:
            raise TerminalSurfaceError(f"Unknown terminal surface {surface_id}", reason="not_found")
        return owned

    def _attached(self, surface_id: str, attachment_id: str) -> _OwnedSurface:
        owned = self._owned(surface_id)
        with owned.lock:
            if owned.attachment is None or owned.attachment.attachment_id != attachment_id:
                raise TerminalSurfaceError("Terminal attachment is not authoritative", reason="conflict")
        return owned

    @staticmethod
    def _earliest_cursor(owned: _OwnedSurface) -> int:
        return owned.replay[0].cursor if owned.replay else owned.next_cursor

    def _drain(self, owned: _OwnedSurface) -> None:
        assert owned.handle is not None
        while not owned.stop_reader.is_set():
            try:
                data = owned.handle.read(timeout=0.1)
            except TerminalBrokerError as exc:
                self._notify_failure(owned, "channel_closed", str(exc))
                self._report_fatal(owned, self._broker_failure(exc))
                return
            if not data:
                if owned.stop_reader.is_set():
                    return
                if not owned.handle.running:
                    self._notify_failure(owned, "channel_closed", "Target terminal process exited")
                    self._report_fatal(owned, TerminalSurfaceError(
                        "Target terminal process exited",
                        reason="channel_closed",
                        diagnostics=getattr(owned.handle, "diagnostics", None),
                    ))
                    return
                continue
            with owned.lock:
                chunk = TerminalChunk(owned.next_cursor, data)
                owned.next_cursor += len(data)
                self._append_replay(owned, chunk)
                attachment = owned.attachment
                if attachment is not None:
                    try:
                        attachment.chunks.put_nowait(chunk)
                    except queue.Full:
                        self._notify_failure(owned, "channel_closed", "Terminal attachment is too slow")
                        owned.attachment = None

    def _append_replay(self, owned: _OwnedSurface, chunk: TerminalChunk) -> None:
        if len(chunk.data) > self._replay_limit:
            tail = chunk.data[-self._replay_limit :]
            owned.replay.clear()
            owned.replay.append(TerminalChunk(chunk.cursor + len(chunk.data) - len(tail), tail))
            owned.replay_bytes = len(tail)
            return
        owned.replay.append(chunk)
        owned.replay_bytes += len(chunk.data)
        while owned.replay and owned.replay_bytes > self._replay_limit:
            removed = owned.replay.popleft()
            owned.replay_bytes -= len(removed.data)

    @staticmethod
    def _notify_failure(owned: _OwnedSurface, reason: str, message: str) -> None:
        attachment = owned.attachment
        if attachment is None:
            return
        try:
            attachment.chunks.put_nowait(TerminalStreamFailure(reason, message))
        except queue.Full:
            pass

    @staticmethod
    def _broker_failure(exc: TerminalBrokerError) -> TerminalSurfaceError:
        details: dict[str, Any] = {"retryable": exc.retryable}
        if exc.diagnostics is not None:
            details["process"] = exc.diagnostics.to_dict()
        return TerminalSurfaceError(
            str(exc), reason=exc.reason, details=details, diagnostics=exc.diagnostics,
        )

    def _report_fatal(self, owned: _OwnedSurface, failure: TerminalSurfaceError) -> None:
        with owned.lock:
            if owned.closing or owned.fatal_reported:
                return
            owned.fatal_reported = True
        if self._on_fatal is not None:
            self._on_fatal(owned.resource, failure)
