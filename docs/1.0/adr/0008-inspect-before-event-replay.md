# ADR 0008: Inspect Before Event Replay

- Status: accepted
- Date: 2026-08-31

## Context

An open SSE connection proves transport reachability but not supervisor identity,
kernel ownership, or cursor validity. A service restart creates a new supervisor
epoch whose sequence numbers may overlap the previous epoch. A bounded event log
can also discard events while the frontend is disconnected.

Treating either case as ordinary replay would make the frontend guess resource
state or apply events from the wrong epoch.

## Decision

Before opening or reopening SSE, the frontend inspects `/v1/health`. The response
is an authoritative snapshot containing `supervisor_id`, current kernel resource
or `null`, and the inclusive retained event window
`earliest_event_sequence..event_sequence`.

- On first contact, supervisor replacement, an expired cursor, or a cursor ahead
  of the supervisor, the frontend applies the snapshot, clears execution
  correlations, sets its cursor to the snapshot's latest sequence, and streams
  future events from there.
- If the supervisor matches and the cursor remains replayable, the frontend
  preserves its state at that cursor and asks SSE to replay the missing range.
  It does not jump to the newer snapshot and then replay older transitions.
- Events arriving after inspection but before SSE opens are retained by the
  supervisor and replayed from the selected cursor.
- Stream-open comments establish no resource truth.

## Consequences

- “Reconnect” means re-establishing and synchronizing frontend transport; it
  never revives a kernel.
- A replaced supervisor can authoritatively turn a stale frontend `on` view into
  `off` without inventing a kernel-death event.
- Expired event history loses transient execution/output history by design, but
  resource state is repaired explicitly and observably.
- Durable recovery of missed output, if required later, needs resource storage
  beyond the bounded event log rather than frontend inference.
