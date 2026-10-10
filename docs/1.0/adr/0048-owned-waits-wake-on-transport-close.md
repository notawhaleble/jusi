# ADR 0048: Transport Owners Wake Their Blocking Readers During Cleanup

- Status: accepted
- Date: 2026-10-09

## Context

The [plugin timing profile](../performance/plugin-cells-2026-10-09.md) found
that client retirement waited about 160 ms for a 200 ms editor-action listener
timeout. Whole-service exit waited about one second for an SSE executor thread.
Cancelling an asyncio task that awaits `to_thread` does not stop the underlying
blocking function. The terminal output pump had the same issue with its
500 ms queue wait.

## Decision

An owning transport explicitly wakes its blocking reader when that transport
closes or its task is cancelled. Cleanup retains bounded joins, process reaping
and authoritative retirement ordering.

- A client-owned editor-action endpoint selects its listening socket together
  with a private wake socket. Closing the wake socket releases the accept loop
  immediately; cleanup joins it, closes accepted peers and removes the private
  endpoint directory. A stop check prevents admitting a peer during teardown.
- Each SSE connection owns a cancellation event. `EventLog.wait_after` checks
  that event under its condition lock, and connection close or task cancellation
  notifies the condition. The predicate preserves unrelated readers and prevents
  a cancellation that precedes wait entry from being lost.
- A WebSocket output pump wakes the queue belonging to its own attachment when
  it closes or is cancelled. That queue is not reused for a later attachment,
  so the retired reader cannot consume the replacement's stream. Terminal
  WebSockets use TCP_NODELAY for interactive output.

The application event log remains independent of asyncio and Tornado. Existing
keepalive intervals, replay, failure containment, exact attachment ownership,
worker process isolation and the first-draw geometry gate remain in force.
There are no wire-format changes or shared process pools.

Fresh runtime processes also avoid imports needed only for version reporting
or type annotations: installed distribution version is resolved on demand,
the bundled provider resolves its version when constructing its catalog, and
worker ports import diagnostic types only for type checking. The stdlib-only
PTY launcher skips site initialization; the application it execs initializes
its normal environment and user configuration.

## Verification

- Event-log tests exercise cancellation before wait entry, cancellation during
  a long wait, and an unrelated reader that must continue receiving events.
- Real HTTP tests close idle SSE connections from both peer and service sides,
  with a 30-second wait that must end through explicit cancellation.
- A real WebSocket test verifies that a retired attachment releases its pending
  queue reader rather than waiting for a timeout.
- Socket tests close idle listeners and incomplete requests, then check thread,
  descriptor, peer and private-path cleanup, including repeated close.
- Fresh-process tests enforce import boundaries and installed-version accuracy.
- PTY tests verify initial geometry, byte preservation, escalation, and normal
  site initialization inside the application.
- The profiling harness measures user-visible completion as well as nested
  service timings, with an uninstrumented control run.
