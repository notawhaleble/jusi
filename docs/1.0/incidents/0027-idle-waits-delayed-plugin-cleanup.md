# Incident 0027: Idle Transport Waits Delayed Plugin Cleanup

- Date: 2026-10-09
- Scope: plugin client retirement and owned-service exit
- Status: fixed

## Evidence

Repeated local VisiData executions measured approximately 185 ms for explicit
close and application quit. Target PTY termination took about 1 ms and worker
shutdown about 10 ms. Roughly 160 ms remained inside the application editor-action
endpoint's close method. SQLite's development fixture, which does not request
that endpoint, closed in roughly 21 ms.

The endpoint closed a listening socket and joined the thread blocked in accept.
On macOS, closing the listening socket from another thread did not immediately
wake the wait, and shutdown of the listener returned ENOTCONN. Its 200 ms
accept timeout therefore determined when cleanup could finish.

Whole-service exit took about 1.18 s, including approximately one second in
asyncio executor shutdown. The final SSE `EventLog.wait_after` continued until
its one-second timeout even after the owning HTTP transport had closed.
Cancelling the asyncio task did not cancel its executor thread. The terminal
output pump also left a queue reader until its 500 ms timeout; after fixing
SSE, this accounted for a remaining variable executor-shutdown tail.

## Resolution

[ADR 0048](../adr/0048-owned-waits-wake-on-transport-close.md) adds explicit
transport-owned wakeups to the listener, SSE condition and terminal attachment
queue. Cleanup still completes and reaps resources before authoritative client
retirement. Regression tests use long idle waits and explicit handshakes to
prove cancellation, including cancellation before wait entry and independent
live readers.

The [before/after profile](../performance/plugin-cells-optimization-2026-10-09.md)
records the resulting user-visible timings and remaining startup costs.
