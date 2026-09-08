# Incident 0012: Whole Cell Deletion Skipped Retirement

- Date: 2026-09-08
- Scope: notebook model structural reconciliation

## Evidence

After the window cleanup correction in Incident 0011, the user still observed
an output buffer and split surviving complete deletion of their cell. Read-only
inspection of the running Neovim found that the output's cell remained in the
model's linked list and ID map, but its opener extmark no longer existed. The
cell had not been published as retired, so lifecycle cleanup never ran.

The replacement region started at its first surviving opener. When a deleted
opener vanished from Neovim's index, the linked cells before that surviving
opener were skipped. Undo-created openers expose this case on later deletion.

## Correction

Choose the first old cell from the linked successor of the region's left
boundary (or the model head), rather than the first extmark within the region.
This includes deleted cells with no surviving anchor in retirement, preserves
neighbor identities, and retains bounded local reconciliation.

Frontend coverage deletes first, middle and last cells, undoes each deletion,
and deletes the recreated cell again. A lifecycle test uses an undo-created
middle cell with a real output buffer and window and verifies both are closed.
The standard model performance harness remains bounded.
