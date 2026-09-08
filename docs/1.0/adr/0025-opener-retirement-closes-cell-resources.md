# ADR 0025: Opener Retirement Closes Cell Resources

- Status: accepted
- Date: 2026-09-07

## Decision

A damaged or removed opener retires its cell identity and initiates a complete
close of its owned resources. There are no editing approval dialogs. Output
retention/parking will not protect an artifact whose owning cell is retired.
Damage to a closer or history structure with a surviving opener still preserves
identity, as specified by ADR 0024.

Deleting A's closer and B's opener merges their bodies under A's identity.
A retains its resources. B's active execution is interrupted, its clients are
closed through their existing cleanup paths, and its ordinary output is
retired. Undo restores B's text under fresh identity C; it cannot restore B's
output, undo interruption, or revive a closed client. Cleanup targets B's
identities even if undo or another execution occurs before cleanup completes.

The notebook model publishes deferred retired-cell identities after structural
reconciliation (including explicit reload). It does not import or call backend,
client, or presentation code. Runtime orchestration consumes these identities
outside the typing callback and uses the same close path as `JusiClose`.
Body-only typing remains local; structural retirement is the explicit lifecycle
trigger for deferred cleanup.

`JusiClose` now means full close for any active kernel execution owned by the
cell, not only a pending-input execution. It fences ordinary presentation,
interrupts exact active execution identities, and closes all captured client
identities. Closing a durable client invokes target-side worker/surface cleanup;
no plugin-specific interrupt capability is required to close that client.
Native window close remains hide-only. Already absent resources are a no-op.

Late execution output or input requests cannot recreate retired presentation.
Clients and surfaces reported after retirement are closed without being opened.
A handoff reported after explicit close is fenced by its originating execution
identity, so it cannot close a newer artifact on the surviving model cell.

Cleanup failures remain typed and visible. Failure of one resource does not
prevent attempts to close the others. Retired identities remain recorded for
retry on transport reconnect or authoritative resynchronization; successful
requests are not repeatedly sent for subsequent late output. A short bounded
retry handles an interrupt conflict while the captured execution is still
reported running; it never targets newer work. Backend resources stay
accounted for until authoritative completion/close events arrive. Replacing
or destroying the notebook runtime fences old callbacks; runtime teardown owns
remaining target cleanup.

## Verification

- Frontend coverage proves model edit/query callbacks do no resource work,
  A survives merge, B closes, undo produces C, late client discovery is cleaned,
  failed cleanup is retryable, and detached runtimes issue no new cleanup.
- The real-kernel scenario tests both active-B/completed-A and
  active-A/completed-B merges, undo during cleanup, and subsequent execution.
- The terminal fixture damages a client-owning opener and verifies removal of
  the backend client/surface and frontend terminal while the kernel stays on.
- The standard body/structural performance harness remains within its budgets.
