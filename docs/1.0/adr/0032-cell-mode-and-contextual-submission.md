# ADR 0032: Cell Mode And Contextual Submission

Status: accepted

## Decision

Cell mode is buffer-local frontend interaction state, available before service
connection. Space toggles it in Normal mode. It defaults off and survives an
immediate model replacement within the same buffer. It is independent from
kernel state, execution outcome, and client capabilities.

While enabled, native Normal-mode keys select cell actions:

| Key | Action |
| --- | --- |
| j / n, k | Next / previous cell or expanded history entry, with a count |
| Enter | Contextual submission or history restoration |
| H | Toggle owning cell history |
| Ctrl-P / Ctrl-N | Restore older / newer history body, clamped at the ends |
| C | Clear active payload and enter Insert, preserving magic/header and history |
| X | Delete owning cell text; existing opener retirement performs full cleanup |
| Y / P | Copy whole cell text / paste below, with fresh runtime identity |
| B | Create an empty cell below and enter Insert |
| Q | Existing full artifact close |

Space and mode-specific mappings are buffer-local. Prior buffer-local mappings
are restored on exit/detach; global mappings become visible again. User remaps
made while cell mode is active are not removed on exit. No Insert-mode mappings
are introduced. Native editing and completion remain available. Insert mode
restores rounded borders; leaving Insert resumes the selected cell mode.

`JusiNextCell` and `JusiPreviousCell` navigate only cells, with counts, outside
cell mode as well. `JusiCellNewAbove` and `JusiCellNewBelow` insert an empty valid
cell and enter Insert. Explicit edit/delete/copy/paste and mode-toggle commands
remain available. Copy uses a session-local text clipboard, with no backend or
runtime identities. Delete leaves surrounding text intact, even when the last
cell is deleted. Malformed cells can be navigated and deleted; copying, pasting
adjacent to, or clearing a malformed cell is declined until repair.

Navigation follows model links and visible history ranges. It does not wrap at
notebook boundaries or trigger a full parse. Targets are the active body's first
line (the opener for an empty body) and each expanded history entry's first line
(its separator when empty). Backward navigation is symmetric, including entry
into a previous cell's expanded history. Text outside cells moves to the nearest
cell in the requested direction. Native folds determine which history is open
in the current window.

## Submission Dispatch

`JusiSubmit`, bound to Enter in cell mode, selects one existing operation:

1. On history: open a closed summary, or restore the selected expanded entry.
   This works offline and does not execute.
2. For an exact pending input request on the owning cell: call `JusiInput`.
3. If its kernel execution is still running: report busy, without replacing it.
4. For an existing client offering followups: call `JusiFollowup`.
5. Otherwise: call `JusiExecute`.

The underlying explicit commands retain their original semantics. The dispatcher
never uses mark colors as resource truth and never converts a rejected input or
followup into a fresh execution. Projection context still resolves its owning
cell identity rather than interpreting terminal screen rows as notebook rows.

## Presentation And Ownership

Status extmarks retain their symbols and colors. Following manual review, cell
mode replaces reverse-video styling with overlay virtual text: `╔══` and `╚══`.
The status symbol stays after the opener. The actual rounded delimiters remain
unchanged in the buffer; no conceal settings or sign/status columns are needed.
RGB and terminal-color paths use the same existing highlight groups. Offline
notebooks receive idle delimiter marks too. Each buffer selects its own overlay
presentation, so toggling one notebook does not change another.

Explicit mode transitions rerender delimiter marks. Body typing and navigation
do not scan status records or contact the backend. Model replacement tears down
old mappings/autocommands and restores the buffer's mode on the new model.
Parking, client-number navigation, and legacy rebuild shortcuts remain outside
this slice; no placeholder bindings are installed for them.

## Verification

`tests/frontend/cellmode_spec.lua` exercises real mapped keys, count/boundary
navigation, expanded/folded history, offline restore, clipboard identity,
localized deletion, mapping restoration, Insert transitions, double-line overlays,
payload replacement, and submission precedence. Real service tests exercise
`JusiSubmit` for new execution, kernel input and durable-client followups.
