from __future__ import annotations

import pytest

from jusi.application.events import EventCursorExpired, EventLog
from jusi.domain.models import ResourceRef


def append(log: EventLog, number: int) -> None:
    log.append(
        trace_id=f"trace_{number}",
        layer="service",
        operation="inspect",
        kind="operation.completed",
        resource=ResourceRef("supervisor", "sup_test"),
        payload={},
    )


def test_event_log_reports_replay_window_and_rejects_expired_cursor() -> None:
    log = EventLog("sup_test", capacity=2)
    assert log.earliest_sequence == 1
    assert log.latest_sequence == 0

    append(log, 1)
    append(log, 2)
    append(log, 3)

    assert log.earliest_sequence == 2
    assert log.latest_sequence == 3
    assert [event["sequence"] for event in log.events_after(1)] == [2, 3]
    with pytest.raises(EventCursorExpired) as captured:
        log.events_after(0)
    assert captured.value.earliest == 2
