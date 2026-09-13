# ADR 0040: Show-diff is a display-only editor action

Status: accepted

## Decision

The generic editor_actions capability includes show_diff alongside copy and
open. Applications call jusi.editor_client.show_diff(before, after, ...).
The internal editor-invoked API has the equivalent jusi.plugin_api.show_diff
worker-result helper. Core does not identify a plugin or interpret its changes.

Delivery opens a new tab with native Neovim diff windows: before on the left,
after on the right, with focus on after. The original tab's layout is preserved.
Both buffers are read-only, nonmodifiable snapshots with modelines disabled.
Filename hints and a common filetype select labels and syntax, never target
paths, local source files or commands. Native tab/window navigation and closing
remain available; there are no additional mappings or public Jusi diff command.

The acknowledgment means the comparison was displayed. It does not mean
acceptance, rejection, a write to either filesystem, or completion of a review.
No decision callback or edit-and-return session is created. Native closure sends
no subsequent decision to the plugin. Successfully delivered buffers and their
tab are independent of the source client/runtime.

## Data and delivery

The public helper accepts two UTF-8 strings without NUL. The wire action uses
one concatenated text stream, a before_bytes boundary, before_name, after_name
and filetype. The boundary counts UTF-8 bytes, not characters or lines. Headers
and partial chunks retain the boundary for the complete content. The frontend
checks it against the assembled content and rejects a boundary inside a UTF-8
character before creating either buffer. Empty or identical sides are valid;
terminal newlines are preserved independently.

Using one stream keeps both snapshots under one action identity and atomic
delivery outcome. It reuses ADR 0039's chunked HTTP, private staging, renewable
inactivity lease and cleanup without introducing total-size ceilings. Only text
and metadata cross the service connection; no shared filesystem or SSH-specific
transfer is required. The destination still needs memory for both snapshots.

The existing exact recipient/source checks, replay-safe acknowledgment and
source-close cancellation apply. A partial download or invalid diff creates no
tab. If presentation fails after creation begins, newly created windows/buffers
are removed and focus returns to the original window. A successful display
survives source close; repeated notices acknowledge the existing result.

## Verification

Shared schemas and Python/Lua fixtures cover the content, application stream
header, fetch response, event and internal command. Invalid boundary metadata,
path hints, NUL and acceptance-related fields are rejected.

Frontend tests cover inline and staged snapshots, Unicode, empty and identical
sides, independent newline preservation, read-only native diff, original layout
preservation, invalid assembled boundaries and rollback on split failure.
Application socket coverage transfers multi-megabyte paired snapshots.
The real terminal fixture invokes action:diff through the same application
helper, observes acknowledgment and verifies source-close independence.
