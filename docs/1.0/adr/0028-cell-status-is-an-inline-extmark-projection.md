# ADR 0028: Cell Status Is An Inline Extmark Projection

- Status: accepted
- Date: 2026-09-08

## Decision

Cell status appears as colored virtual text immediately after the existing
opener (`╭── *` for busy). These are native Neovim extmarks with
`virt_text_pos = "eol"`; both opener and closer have matching status highlights.
They do not use signs, `sign_text`, the sign column, or status-column options.
The text buffer and delimiter grammar remain unchanged.

The user's symbol vocabulary is:

| Observed work | Symbol | Default color |
| --- | --- | --- |
| Never executed | blank | yellow delimiters |
| Kernel execution running, including stdin wait | * | purple |
| Successful execution | ✓ | green |
| Failed execution or fatal client loss | ✗ | red |
| Interrupted/cancelled execution | ! | orange |
| Live client offering followups | > | blue |
| Observed followup operation running | * | purple |
| Previously observed outcome no longer recoverable after a replay gap | ! | muted |

Purple indicates work in progress; a green check appears only on observed success.
A followup-ready mark means the durable client offers that operation, rather
than implying a new kernel state. Kernel input acceptance does not finish work;
the execution completion event decides its final mark. Completion requests do
not affect these marks. Plugin application results remain opaque.

`JusiCellBusy`, `JusiCellDone`, `JusiCellError`, `JusiCellInterrupted`,
`JusiCellFollowup`, `JusiCellIdle`, and `JusiCellUnknown` are default highlight groups that users
can override. Defaults are reapplied after colorscheme changes without replacing
explicit group definitions. Each default defines both RGB foreground and
256-color terminal foreground; enabling `termguicolors` is not required.

## Ownership and movement

The notebook model still owns cell identity; decoration IDs never become cell
identities. A projection stores the last observed execution identity and outcome,
optional live client identity, and exact in-flight followup operation identity.
An old execution/operation completion cannot overwrite newer work on that cell.

The status decoration's range covers the opener, with the same gravity, invalidation,
and no-undo-restoration policy as the model opener. Neovim moves the decoration
as lines are inserted or deleted. Ordinary body typing performs no status scan,
redraw, controller lookup, or backend work. Closer damage retains the mark;
opener retirement deletes it, and undo's newly recreated cell starts with a
yellow delimiters and no status symbol. Initial attachment decorates existing
delimiters; deferred model notifications update only structurally affected cells.
A separate closer highlight follows edits and is removed when the closer is
damaged. A restored or reassigned closer takes its owning cell’s current color.
Never-executed opener highlights are separate from observed execution records.

Last observed final outcomes remain visible after output close or body edits;
these marks describe work history, not a claim that edited text was executed.
Edited-since-submission presentation and parked-output markers are separate,
deferred dimensions. Client close removes followup availability, exposing the
originating execution outcome; fatal loss instead displays an error.

Disconnect retains marks. Normal replay advances them. A resynchronization
recovers active executions and durable clients from authoritative health.
A replay gap makes previous unverifiable outcomes muted rather than inventing
success or interruption; live resources supply their recoverable projections.
Completed history is not recoverable from a fresh health snapshot. Supervisor
replacement and notebook-runtime/model replacement discard old projections.
No backend states, events, or wire fields are added.

## Verification

`tests/frontend/marks_spec.lua` covers the symbol/color mapping, end-of-line symbols and matching delimiter
extmarks, unchanged text/changedtick/column options, stale event fencing,
followup operation correlation, movement, closer damage, opener deletion/undo,
and health resynchronization. Existing real-kernel input and terminal-client
end-to-end tests assert busy-to-final and followup-ready transitions.
