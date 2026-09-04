# ADR 0021: Interrupt Is An Identity-Scoped Control Operation

- Status: accepted
- Date: 2026-09-05

## Context

An interrupt queued behind the execution it is meant to stop is not an
interrupt. ADR 0014 deliberately serializes start, execute, stop, and restart,
and the managed Jupyter adapter serializes execution and shutdown. Both locks
are held while an execution waits for the kernel.

Interrupt must also avoid ambiguous “current work” selection. A delayed
frontend command must not interrupt a newer execution merely because it uses
the same kernel or cell.

Plugin interruption has related user intent but a different owner. A SQL
session may implement `session.interrupt()` to cancel its current operation
while remaining usable. That must not be confused with `session.close()` or
`JusiClose`.

## Decision

Interrupt is a bounded control operation outside the serialized lifecycle and
execution lane. The initial command names both `kernel_id` and the exact active
`execution_id`. The supervisor accepts it only while that identity is the
active kernel-owned execution; stale or mismatched identities fail with a typed
conflict and never fall through to whatever work happens to be current.

The kernel adapter provides a control method that does not acquire its
execution lock. For Jupyter it uses the kernel manager's interrupt operation.
A successful request acknowledges that the signal was requested; the later
ordered `execution.completed` event remains authoritative for the execution
outcome. Successful interruption produces:

- execution outcome `interrupted`
- execute-operation outcome `cancelled`
- interrupt-operation outcome `succeeded`
- unchanged kernel state `on`

It is not an execution failure and does not emit a synthetic failure event.
Failure to request interruption is a typed failure at the originating control
layer. Kernel death observed during interruption still turns the kernel off.

Health inspection includes the active execution identity. A frontend that
connects or resynchronizes during execution can therefore issue an exact
interrupt without reconstructing identity from a cell, request, or cursor.

### Plugin-Owned Work

The same user action will later route through an active work lease. If ownership
has passed to a plugin client declaring `interrupt`, core invokes the plugin's
bounded interrupt hook. Successful plugin interruption preserves the durable
client, session, and surfaces. SQL interruption calls the equivalent of
`session.interrupt()`, not `session.close()`.

An absent interrupt capability is an explicit unsupported result. A hook error
does not implicitly close the client; core retires it only when separate
process/channel evidence demonstrates fatal loss. The concurrent plugin-worker
control mechanism is part of the generic client-operation milestone and is not
smuggled into the single-request worker channel in this kernel slice.

## Consequences

- Stop and restart remain serialized and cannot race execution cleanup.
- Repeating interrupt for the same still-active execution is idempotently
  acknowledged without sending a second kernel signal.
- `JusiInterrupt` resolves the active execution belonging to the current model
  cell; frontend terminal rows never select it.
- Pending input requests can later share the identity-scoped control boundary
  and be cancelled without modal frontend input.

## Verification

The real-kernel Neovim scenario starts a 30-second execution, observes its
ordered active identity, interrupts it before the execute HTTP request returns,
observes outcome `interrupted`, verifies kernel state remains `on`, and then
executes another cell successfully. Unit coverage proves the interrupt bypasses
the occupied operation lane and the Jupyter adapter uses its independent
control method.

