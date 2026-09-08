# ADR 0024: Cell Identity Survives Invalid Closing Structure

- Status: accepted for surviving-opener edits; broader cases remain open
- Date: 2026-09-07

## Decision

Text validity and runtime identity are separate. When the existing opener
survives, damaging or removing the closer or history structure retains the
model cell and its execution/output bindings. Repairing that structure restores
execution eligibility on the same cell; it does not create a replacement.
An unclosed cell's addressable range ends at the next opener or end of buffer.

Reading a body for execution or input submission still requires valid text.
Focus, interrupt, and close use identity and remain available while the text is
malformed. Editing the text alone never closes a projection, sends an interrupt,
or performs backend cleanup.

Structural reconciliation must run after Neovim finishes the edit's extmark
adjustments. In particular, undo may apply further adjustments after on_lines;
newly created anchors inside that callback can be shifted twice. The callback
records a dirty text span and schedules local reconciliation. Public model
queries flush that span before reading cell state, so commands cannot act on
stale structure. Body-only editing retains its immediate local fast path.
The performance harness includes flushing in the measured structural cost.

Reuse requires a surviving, non-invalidated opener anchor and a matching parsed
opener. Coalescing edits must not revive an opener that was deleted and inserted
again before the scheduled reconciliation. This preserves the anti-resurrection
invariant for retired identities.

ADR 0025 resolves opener damage and merges: a damaged opener retires the cell
and fully closes its resources. The surviving opener retains its identity;
undo creates a fresh identity for restored text. See the
[structural edit policy](../architecture/cell-structural-edits.md).

## Verification

- Real Neovim `J`, undo, and redo preserve both affected and neighboring IDs.
- Missing closer at EOF and invalid history structure remain addressable;
  repair restores body/history access.
- Deleted/reinserted openers receive fresh IDs even when edits coalesce.
- The real-kernel input scenario retains its prompt/output while malformed,
  navigates focus, repairs/undoes the structure, and closes/interrupts the exact
  execution while still malformed.
- The 10,000-line performance harness preserves its local scan and latency
  budgets without a full parse or backend work during editing.
