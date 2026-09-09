# Output Retention On New Execution

Status: implemented

An authoritative `execution.started` event cleans completed, unparked artifacts
from other cells on the same kernel. Both `JusiExecute` and Enter's new-execution
branch take this path. Rejected submissions do not trigger cleanup. Input
replies and plugin followups do not create an execution and do not trigger it.

Candidates use the current presentation/client execution identity and observed
controller outcome, not status-mark colors. Successful, failed, interrupted and
cancelled outcomes are final. Any running work on the cell protects it. Live
clients declaring followup remain by design, regardless of their initial
execution outcome. Unknown outcomes and other kernels are preserved.

Cleanup uses full cell-artifact close, fences late output, and closes any final
non-followup client. It does not delete cell text or change its identity/status.

`JusiPark` toggles retention of the owning cell's current output/client. Parking
is independent of outcome and has no default binding or extra mark. Unparking
makes the artifact eligible on the next accepted execution. Explicit close,
opener retirement and re-executing the parked cell itself clear its retention;
the one-artifact-per-cell invariant remains unchanged. Runtime replacement
creates a fresh retention table and does not resurrect retained artifacts.

See [ADR 0033](../adr/0033-completed-output-retention.md).
