# ADR 0027: Completions Use Explicit Source Ranges

- Status: accepted
- Date: 2026-09-08

## Context

The legacy frontend distinguishes kernel completion from completion on an active
plugin client. Its request carries source/cursor context; menu items carry
replacement coordinates and display metadata. Core word guesses and a single
popup start column are insufficient when plugins complete different lexical
units or a multiline body prefix. An adjacent suffix must survive both preview
and acceptance, even without whitespace separating it from the candidate.

## Decision

Tab invokes explicit completion in Insert mode inside a connected notebook
cell. `JusiComplete` and `<Plug>(JusiComplete)` are also available. Tab passes
through unchanged while a native menu is visible or outside the cell body. Regular cells use the selected Jupyter kernel; a cell with an
active client uses that client's `complete` capability. A `%%` cell whose magic is claimed by the runtime plugin catalog needs an
active client, preserving the legacy plugin use case. Ordinary Jupyter magics
remain in kernel scope. Completion never
executes the cell, starts a client, changes its surface, or appends history.

`POST /v1/kernels/{kernel_id}/completions` accepts command kind `complete`, exact
kernel/notebook/cell identities, `body`, `cursor_pos`, and an optional `client_id`.
A client must own that same cell/notebook/kernel. Unknown and retired clients are
not replaced by another client. Busy runtimes reject completion promptly rather
than queue it behind execution, kernel input, or another worker operation.
The worker/kernel request budget is five seconds.

All offsets are **zero-based Unicode code-point offsets** in the original body,
not bytes, screen columns, grapheme clusters, Vim word lengths, or notebook rows.
`cursor_pos` is an insertion boundary between characters. Newlines count as one
character. Lua converts between these offsets and Neovim's byte columns only at
the editor boundary. Delimiters and history are outside the body.

The Jupyter adapter sends only `body[:cursor_pos]` as code and its length as the
Jupyter cursor position. It matches the exact `complete_reply` parent identity
and normalizes matches and the kernel's replacement range. Completion timeout
or invalid result does not imply kernel death; demonstrated death changes the
kernel to `off` and retains process diagnostics.

Plugin workers receive `handle("complete", payload)` with:

- `body`: complete current body, including any suffix, as source reference;
- `prefix`: `body[:cursor_pos]`, the text to use as completion context;
- `cursor_pos`: absolute code-point insertion offset within the body;
- `cursor_row`: zero-based body-relative row;
- `cursor_col`: zero-based code-point column within that row.

Plugins use the prefix to compute suggestions and their own lexical boundaries.
They can choose a small word, whitespace-delimited WORD, empty insertion, or the
entire prefix without a core-wide token rule. The full body is reference data;
text after the cursor must not influence the completion context.

Completion results have a generic, additive object contract:

```json
{"items": [{"text": "sleep", "start": 17, "end": 18,
            "label": "sleep", "detail": "function",
            "documentation": "Pause execution.", "kind": "function"}]}
```

`text`, `start`, and `end` are required. Metadata fields are optional strings.
Every item obeys `0 <= start <= end <= cursor_pos`; ranges may differ between
items and may span lines. Empty requests, results, and replacement strings are
valid. Limits are 500 items, 65,536 code points per replacement, and 4,096 per
metadata field, in addition to existing transport limits. NUL is rejected.
Schema describes shapes/bounds; Python and Lua enforce the cross-field ranges.
The HTTP response carries `completion` alongside its operation. Neither source
nor candidates enter operation events or health snapshots.

This specializes the otherwise opaque worker-result contract of ADR 0011 only
for the generic editor completion operation. Application results for followups
remain opaque. Invalid completion results are worker protocol failures and use
the existing client-scoped cleanup path; valid results never decide client
lifetime. New terminal-surface requests remain forbidden on client operations.

## Editor application

A request captures exact model, cell, kernel, runtime, supervisor and optional
client identities, buffer changedtick, source window/cursor and mode. A newer
request supersedes the old callback. Source changes, cursor/window changes, or
resource replacement discard the reply. No failed request is retried implicitly.

The frontend uses Vim's native `complete()` menu. Items with differing ranges
are normalized to a common start column by retaining each candidate's untouched
leading prefix. Native completion performs insertion, selection, cancellation,
metadata presentation, and same-line or newly inserted multiline previews. Jusi
does not install navigation, acceptance, or cancellation mappings, and does not
change `completeopt`. In particular, Escape is not remapped to cancel.

Native completion starts on the current line. A candidate reaching into earlier
lines previews its resulting current line; the earlier-line edit is finalized
on `CompleteDone` acceptance, after checking resource identity and expected text.
Cancellation and the native unselected state retain the original source. These
additional edits join the native completion undo block and leave suffix text
untouched. Ranges ending before the cursor also restore the intended insertion
position on acceptance. This supersedes the initially implemented custom menu
and its temporary input mappings following the user's correction.

## Evidence

- `../jusivim/test/notebook.vim`: active-handler reuse, no history on completion,
  regular-cell kernel route, and rejection of unbootstrapped magic cells.
- `tests/conformance/test_completion.py` and
  `tests/frontend/completion_spec.lua`: shared range fixtures, Unicode,
  word/WORD/full-prefix choices, differing ranges, cancellation, empty insertion,
  stale text/runtime/client replies, insert keys and suffix extmarks.
- Supervisor/adapter tests: prefix-only kernel requests, exact client ownership,
  busy rejection, invalid-range containment and real kernel-scope completion.
- `tests/e2e/completion_spec.lua` and terminal-surface e2e: real HTTP/kernel/menu
  application, empty-prefix plugin menu and unchanged client surface.
