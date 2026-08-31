# Frontend Service Controller

## Boundary

The Lua controller binds explicit notebook operations to the versioned service
contract. It reads cell bodies from the notebook model at execution time,
creates command and trace identities, and correlates execution events back to
model cell identities.

It does not render output, parse terminal escapes, infer kernel liveness, or
perform work from buffer edit callbacks.

## Resource Truth

- Kernel `off`/`on` is updated only from validated service responses or ordered
  events.
- Transport connection state is separate from kernel state. Losing the event
  stream never changes an `on` kernel to `off`.
- An invalid or retired cell is rejected locally before a command is sent.
- Execution output is routed through the execution identity announced by
  `execution.started`, not through a current cursor or line number.

## Inspection And Event Ordering

The controller inspects health before opening SSE and retains the last accepted
sequence in one supervisor epoch. First contact, supervisor replacement, or an
unavailable cursor applies the authoritative snapshot and resumes after its
latest sequence. A cursor still inside the retained window replays from its
current position without first applying newer snapshot state.

The controller ignores duplicate events and closes the stream on a gap or
invalid envelope. It never fabricates missing state. The exact resynchronization
rules are recorded in ADR 0008.

## Transport Adapter

`lua/jusi/transport/http_sse.lua` implements the initial curl adapter described
by ADR 0007. `lua/jusi/transport/sse.lua` is an incremental framing parser and
handles arbitrary chunk boundaries, CRLF, comments, and multiline data.

The controller depends on the adapter interface rather than curl itself. Unit
tests use an in-memory fake; the headless end-to-end test uses the real adapter,
service, SSE stream, and Jupyter kernel.
