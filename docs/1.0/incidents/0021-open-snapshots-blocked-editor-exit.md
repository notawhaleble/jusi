# Incident 0021: Open Snapshots Blocked Editor Exit

- Date: 2026-09-16
- Scope: frontend editor-action presentation

## Evidence

VisiData Ctrl-O opened each value snapshot in a listed normal buffer, marked it
modified, and hid it when its split closed. The open action has no writeback
destination, so writing did not update the VisiData cell or kernel value. Hidden
exports nevertheless participated in Neovim's unsaved-change checks and forced
the user to resolve every snapshot when exiting.

## Correction

Generic open actions now create modifiable, unlisted `nofile` scratch buffers.
They retain names and filetypes for display and syntax, preserve exact text and
newline shape, and remain independent of source-client cleanup. Local edits do
not set the modified flag, so ordinary window close and editor exit discard the
snapshot without a save prompt. Copy and read-only show-diff behavior are
unchanged; edit-and-return remains a separate, unimplemented operation.

Frontend coverage checks the scratch options and closes a locally changed
snapshot without force. The real terminal and bundled VisiData scenarios check
that delivered open buffers are scratch buffers and stay unmodified after local
edits.
