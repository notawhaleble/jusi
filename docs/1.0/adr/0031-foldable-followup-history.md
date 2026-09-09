# ADR 0031: Foldable Followup History

Status: accepted

## Decision

History is durable notebook text in the existing `╞══` / `├┄┄` suffix,
newest first. Only followup-capable plugin submissions are captured:

- Initial execution is captured when exact client creation declares `followup`.
- Followups are captured on successful delivery or a worker-level failure after
  client selection. Supervisor/protocol rejection adds nothing.
- Capture uses the exact frontend submission snapshot, omits an initial magic
  header, preserves whitespace and empty replies, and moves exact duplicates to
  the front. Repeating the newest entry does not modify the buffer.
- Kernel input replies and ordinary kernel executions are never captured.

HTTP and SSE share trace-correlated pending snapshots, so their ordering cannot
capture a submission twice. These snapshots belong to the current frontend
model; they are not an audit log or reconstructable backend history. Transport
uncertainty waits for evidence rather than inventing acceptance. Initial
handoff capture requires observing its client-created event. A retired opener
cannot receive late history. Accepted snapshots awaiting structural repair are
kept locally until repair or model retirement; no uncertain ranges are rewritten.

The literal-reserved-delimiter limitation of the notebook grammar remains in
force. No new escaping or notebook format is introduced.

## Folding And Commands

One native manual fold covers the history boundary through the final entry,
excluding the cell closer. Fold text is `history: N entries`, with blank fold
fill and the muted `JusiHistoryFold` foreground (RGB and 256-color terminal
defaults, reapplied after colorscheme changes). Expanded history retains its literal `╞══` boundary. The previous window
fillchars setting is restored along with other folding options. History folds
start closed on first view. Native `zo`, `zc`, `za`, and related commands remain
available. No mappings or fold/sign columns are installed.

`JusiHistoryToggle` works from anywhere in the source cell.
`JusiHistoryApply` restores the entry at the cursor (including its leading
separator) into the active body, preserving the current magic header and alias.
It neither executes nor removes that entry. It closes this window's history,
focuses the restored body, and is one undoable text replacement. Contextual
submission/navigation bindings remain deferred to cell mode.

Fold state is independent per window. Ordinary edits, history capture, buffer
switches and immediate frontend-model replacement preserve an existing view's
choice. Runtime cell identity is not reconstructed from saved fold state.
Damaged history structure is unfolded locally; repaired structure can fold
again. Fold extmarks anchor presentation ranges only. Opener retirement uses
existing full cell cleanup and drops that cell's folds and pending snapshots.

Folding, restoration and language rendering work offline. Attaching a notebook
sets window-local manual folding options and restores previous options when
leaving the notebook. Jusi owns the generated history folds, not a parallel
syntax-fold hierarchy.

## Locality And Language Context

Initial window attachment enumerates cells. Later structural notifications
update only affected/retired cell folds. Body typing leaves native folds to
follow their text; it does not trigger a full fold rebuild or backend work.

The notebook-owned editing worker renders each history entry in an isolated
scratch buffer using the cell's current syntax and indentation profiles.
An unfinished string or indentation block cannot leak between active body and
history or between entries. Whole visible cells retain complete syntax context,
including their history. Hidden history therefore still has language spans
ready when a native fold opens. Scratch entries retire with their owning cell
or when it leaves the visible-cell set.

## Verification

`tests/frontend/history_spec.lua` covers window-independent native folds,
range boundaries, deduplication, Unicode and empty submissions, restore/undo,
structural damage/repair, deferred accepted submissions, opener retirement,
HTTP/SSE ordering, capability gating, buffer/model replacement, and isolated
history syntax/indentation. `tests/e2e/terminal_surface_spec.lua` verifies initial
handoff and followup capture through a real plugin worker and service.
