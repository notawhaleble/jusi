# ADR 0014: Serialize Operations Without Locking Authoritative Inspection

- Status: accepted
- Date: 2026-08-31

## Context

Kernel, discovery, cleanup, and plugin-worker calls can block until a bounded
timeout. Holding the lock used by health and resource inspection across that
I/O makes the service appear unreachable precisely when diagnostics are most
important. Allowing lifecycle operations to race, however, would complicate
the one-kernel baseline with partially overlapping start, execute, stop, and
restart transitions.

## Decision

The supervisor uses two locks with distinct purposes:

- an operation lock serializes start, execute, stop, and full restart
- a state lock protects only short reads and publications of authoritative
  kernel/runtime identity and state

External discovery, kernel, cleanup, and plugin-worker I/O may hold the
operation lock but never the state lock. Health and resource inspection thus
return the last fully observed authoritative snapshot while an operation is in
progress. State publication occurs only after the corresponding external fact
is known: a kernel remains `off` during start and remains `on` until death or
stop is observed.

This is deliberately not concurrent kernel execution. The initial one-kernel
service continues to serialize mutating operations. More concurrency requires
explicit per-resource leases and cancellation semantics rather than weakening
this lock boundary.

## Consequences

- Health and inspection remain responsive during slow plugin discovery,
  kernel startup/execution, worker I/O, and cleanup.
- Stop cannot race an in-flight execution or worker request in the initial
  model.
- An in-progress operation is visible through ordered operation events; health
  exposes resource truth, not speculative transitional state.
- Future multi-runtime support must replace the single operation lock with
  resource-scoped serialization while retaining short authoritative-state
  publication.

## Refinement

ADR 0021 adds one deliberate exception: identity-scoped interrupt uses a
separate bounded control path because queuing it behind execution would make it
ineffective. Start, stop, restart, execution, and cleanup remain serialized.
