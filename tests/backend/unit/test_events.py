from __future__ import annotations

import pytest
import threading
from concurrent.futures import ThreadPoolExecutor
from queue import Queue

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


def test_transport_cancellation_wakes_only_its_reader(monkeypatch) -> None:
    log = EventLog("sup_test")
    cancelled, other_cancelled = threading.Event(), threading.Event()
    waiting = Queue()
    original_wait = log._condition.wait

    def wait(timeout=None):
        waiting.put(True)
        return original_wait(timeout)

    monkeypatch.setattr(log._condition, "wait", wait)
    with ThreadPoolExecutor(max_workers=2) as pool:
        retired = pool.submit(log.wait_after, 0, timeout=30, cancelled=cancelled)
        live = pool.submit(log.wait_after, 0, timeout=30, cancelled=other_cancelled)
        try:
            waiting.get(timeout=1)
            waiting.get(timeout=1)
            cancelled.set()
            log.wake_waiters()
            assert retired.result(timeout=1) == []
            assert not live.done()
            append(log, 1)
            assert [event["sequence"] for event in live.result(timeout=1)] == [1]
        finally:
            cancelled.set()
            other_cancelled.set()
            log.wake_waiters()


def test_cancellation_before_wait_does_not_lose_the_wakeup() -> None:
    log = EventLog("sup_test")
    cancelled = threading.Event()
    cancelled.set()
    log.wake_waiters()
    assert log.wait_after(0, timeout=30, cancelled=cancelled) == []
    append(log, 1)
    assert log.wait_after(0, timeout=0)[0]["sequence"] == 1
