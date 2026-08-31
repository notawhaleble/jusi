from __future__ import annotations

import threading
from collections import deque
from typing import Any

from jusi.domain.models import ResourceRef, utc_now


class EventCursorExpired(ValueError):
    def __init__(self, after: int, earliest: int) -> None:
        super().__init__(f"Event cursor {after} predates earliest available sequence {earliest}")
        self.after = after
        self.earliest = earliest


class EventLog:
    def __init__(self, supervisor_id: str, *, capacity: int = 2048) -> None:
        self.supervisor_id = supervisor_id
        self._capacity = capacity
        self._events: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._sequence = 0
        self._condition = threading.Condition()

    @property
    def latest_sequence(self) -> int:
        with self._condition:
            return self._sequence

    def append(
        self,
        *,
        trace_id: str,
        layer: str,
        operation: str,
        kind: str,
        resource: ResourceRef,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        with self._condition:
            self._sequence += 1
            event = {
                "protocol_version": 1,
                "event_id": f"evt_{self._sequence:016x}",
                "supervisor_id": self.supervisor_id,
                "sequence": self._sequence,
                "occurred_at": utc_now(),
                "trace_id": trace_id,
                "layer": layer,
                "operation": operation,
                "kind": kind,
                "resource": resource.to_dict(),
                "payload": payload,
            }
            self._events.append(event)
            self._condition.notify_all()
            return dict(event)

    def events_after(self, after: int) -> list[dict[str, Any]]:
        with self._condition:
            if self._events:
                earliest = self._events[0]["sequence"]
                if after < earliest - 1:
                    raise EventCursorExpired(after, earliest)
            return [dict(event) for event in self._events if event["sequence"] > after]

    def wait_after(self, after: int, *, timeout: float) -> list[dict[str, Any]]:
        with self._condition:
            events = self.events_after(after)
            if events:
                return events
            self._condition.wait(timeout)
            return self.events_after(after)
