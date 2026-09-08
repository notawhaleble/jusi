# Incident 0007: Structural Undo And Cell Output Identity

- Status: surviving-opener defects reproduced and fixed
- Date: 2026-09-07

## Report And Reproduction

A cell waiting for kernel input had its closing delimiter accidentally joined
onto its body. After reverting the edit, `JusiClose` reported no output.

The focused model reproduction confirmed two defects. While the closer was
missing, cursor lookup compared the cursor row against a nil close_row rather
than treating the malformed cell as an addressable range. In a two-cell `J`
and undo sequence, the first cell's identity survived, but undo moved a newly
created neighboring opener anchor an extra row and replaced that neighbor's
identity. Thus the exact loss of the reported cell's binding was not reproduced
in that minimal sequence; invalid lookup and neighboring identity loss were.

## Cause And Correction

The model created structural anchors synchronously in on_lines. Neovim undo
continued adjusting extmarks after the callback, including anchors that had
just been created. Reconciliation now waits until the edit settles; explicit
model queries flush pending local changes before returning data.

An unclosed cell with its original opener remains addressable through the next
opener or EOF. Its text is invalid for submission, but runtime controls and
output ownership remain usable. Invalidated/deleted opener anchors are not
reused, including across coalesced deletion and reinsertion.

## Regression Coverage

The frontend suite uses actual `J`, undo, and redo, checks both cell IDs and
ranges, and covers EOF/history damage plus deleted-opener non-resurrection.
The real input scenario keeps its exact pending request and output across
malformed/valid transitions, navigates focus, and closes the pending execution
while malformed. The performance harness includes the deferred work.

The subsequent merge reproduction confirmed that B's identity retired while
its output and active resources remained orphaned. ADR 0025 resolves that gap:
retired opener identities now trigger deferred full close, including exact
interrupt and client cleanup; undo creates fresh C. Real-kernel and terminal
fixtures verify cleanup and surviving A ownership in both activity orderings.
See the [structural edit policy](../architecture/cell-structural-edits.md).
