# ADR 0029: Cell Execution Has No Automatic Deadline

- Status: accepted
- Date: 2026-09-08

## Decision

Ordinary kernel execution and its pending input requests may wait indefinitely
while their owning execution/cell remains alive. Elapsed time alone is not a
failure and never triggers interruption. This replaces the ten-second default
execution budget and the separate five-minute input allowance in ADR 0023.

Explicit interruption, cell artifact close, opener retirement, kernel death,
and runtime teardown retain their existing identity-scoped lifecycle behavior.
Stop and restart interrupt active kernel work before waiting for the serialized
operation lane, including execution that has no pending input.
Native window close and frontend disconnect still only hide/detach presentation.

The supervisor passes no execution deadline to the kernel adapter by default.
The adapter continues polling kernel liveness and control while waiting, including
when waiting for the matching shell reply after IOPub idle. Explicit internal
execution budgets remain available for bounded tests; input waiting does not
consume those budgets and has no separate expiry.

The frontend execution HTTP request has no total curl time limit. Connection
establishment remains bounded. Startup, plugin handoff setup, completion,
control requests and cleanup retain independent deadlines; removing the kernel
execution deadline does not remove their bounds. No protocol fields change.

## Verification

Real-kernel adapter coverage advances its clock beyond both former limits while
running and while awaiting input, then delivers input successfully. It also
checks explicit input interruption and subsequent kernel reuse. HTTP coverage
asserts execution receives no deadline. Frontend coverage asserts execute opts
out of the total request timeout; existing end-to-end tests cover input close,
interrupt, stop, restart, and cell resource lifetime.
