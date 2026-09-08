# Cell Identity Through Structural Edits

## Rules

The opener owns identity. Closer/history damage changes validity without
retiring that identity. Opener damage retires the cell and fully closes its
resources, without approval dialogs. Undo restores text, not retired resources.
See [ADR 0024](../adr/0024-cell-identity-survives-invalid-closing-structure.md)
and [ADR 0025](../adr/0025-opener-retirement-closes-cell-resources.md).

| Edit | Identity and resource policy |
| --- | --- |
| Edit, insert, delete, or join body lines | Preserve while opener survives |
| Join closer to body; damage or delete closer | Preserve; malformed text blocks submission, but focus/interrupt/close work |
| Undo/redo or manually repair closer | Preserve throughout; output remains attached |
| Modify history boundaries or separators | Preserve; validity and text ranges follow grammar |
| Insert text or cells before an existing cell | Preserve surviving cell IDs |
| Split by inserting closer and opener | Original opener retains original ID/output; new opener gets fresh ID without inherited output |
| Insert opener inside unclosed cell | Original survives malformed up to new opener; new cell recovers independently |
| Damage opener, delete opener, or delete whole cell | Retire identity; interrupt active work, close clients, remove outputs |
| Merge by deleting A's closer and B's opener | A survives with its resources; B fully closes regardless of activity/retention |
| Undo that merge | A survives; restored second cell is fresh C with no B resources |
| Replace whole cell including opener | Fresh identity if original anchor was invalidated; old resources close |
| Explicit reload | Old model identities retire and close; parsed cells receive fresh identities |

Parking, when implemented, protects outputs from later execution cleanup only.
It cannot preserve a deleted owner. No output or interaction is transferred
between cells based on text equality, line number, or undo history.

## Runtime Boundary

Retirement notifications are deferred beyond buffer edits and model queries.
Runtime orchestration closes exact execution/client identities and fences late
presentation. Failures are surfaced and remain retryable on reconnect; backend
resources are not declared closed without authoritative evidence. Body typing
never performs backend work. Opener retirement triggers deferred lifecycle
work, not synchronous cleanup inside the edit callback.

## Remaining Design Space

Explicit move or split commands could offer deliberate identity-preserving
operations in the future. Their semantics must be specified separately from
ordinary text deletion/insertion, which follows the rules above. Visual cues
or optional opener protection may help avoid accidental resource retirement;
modal editing approval dialogs are excluded.
