# Document artifacts and source context

[ADR 0054](../adr/0054-document-artifacts-and-source-context.md) extends the
application editor-action channel with reusable finite document delivery and
source snapshots. These APIs do not depend on a reading or assistant provider.
They require the coordinated Jusi 1.0.7 frontend and a presentation host with
MIME discovery and source snapshot support.

## Application API

An application belonging to a plugin with `editor_actions` can call:

```python
from jusi.editor_client import open_document, close_document, source_snapshot

open_document("/target/report.md", media_type="text/markdown", slot="report",
              lifetime="client", assets=[("/target/figure.png", "image/png", "figure.png")])
close_document(slot="report")
```

`open_document` first offers the document identity. An existing compatible
independent reader can be reused without copying or transferring the book.
Otherwise core selects negotiated shared staging or HTTP content delivery.
The first artifact
is the document; its basename is preserved (or supplied with `name=` when the
source is a temporary conversion). Optional assets have flat UTF-8 filename
hints relative to it. Names are limited to 255 encoded bytes; path separators,
colon, control characters, `.` and `..` are rejected.
Core stages these files privately in the frontend filesystem and selects a
registered MIME-compatible provider. Optional `provider` constrains that choice
to a compatible installed provider. Unsupported types fail visibly. The helper
waits for provider readiness, not just transfer completion.

`lifetime="client"` overlays the requesting client's terminal. Reopening a slot
replaces its previous view, including hidden views in other tabs. Client close
closes these views and releases staging. `lifetime="independent"` opens a separate
document, reuses an existing view by content identity, and survives client close.
Tab cleanup (`\Q`) treats client-owned documents as visible views of the owning
client even when its terminal is hidden. A terminal or owned document visible in
another tab keeps that client alive; only this tab's views close. Independent
books are excluded. Client teardown removes the owned document's split as well
as its buffer, while `close_document` restores the terminal for continued use.
Placement defaults are unchanged: an independent document opens in a right
split and a client-owned document overlays its source. Pass `placement="tab"`
to explicitly open and focus a new tab. Reusing a visible document with this
option focuses its existing window; a hidden document is revealed in a new tab.
Explanations and other callers that omit this option retain their placement.
The document's eventual buffer deletion releases its staging. `document_id`
defaults to SHA-256 of the main file; a provider can report its own immutable
content identity, such as a bundled document's identity. Slots are client-scoped.
`close_document` closes only a client-lifetime slot; it leaves independent books
and other clients' views intact.

Source capture additionally requires the catalog capability `source_context`:

```python
import tempfile

with tempfile.TemporaryDirectory() as directory:
    manifest = source_snapshot(directory, slot="book", context_id=submitted_id,
                               cancel=cancel_event)
    # Consume metadata and files in directory before leaving this scope.
```

The destination is a caller-owned empty directory. The return value contains
`metadata` and file descriptors (`name`, `media_type`, `size`); no frontend path
is interpreted at the target. With an empty context ID, core snapshots the source
document already bound to the slot. With a submitted ID, it consumes the exact
captured reader view and binds that view to the slot for later typed followups.
The optional threading event cancels this request without closing the client.
Errors use the existing editor-delivery error path; cancellation raises
`InterruptedError`.

These are application APIs, not worker `editor_action` result variants. An
application's existing `JUSI_EDITOR_ACTION_SOCKET` provides its client identity.
It is private IPC on the target, supplied by core, and never a Neovim endpoint.

## Selection correlation

For a selection submitted through the palette to a family advertising
`source_context`, core asks the host to capture the source before switching
windows. The host captures its current revision ticket; the reader accepts that
ticket and freezes the physical page before asynchronous extraction/rendering.
Navigation cannot retarget accepted work. A stale ticket fails recoverably.

An opaque `source_context` field accompanies execute/followup separately from the
literal body. Only opted-in workers receive it. Workers can implement
`handle_with_context(operation, payload, *, source_context)` for execute and
followup. The keyword argument is the captured ID or None. The provider payload
retains its original fields, including any private field named `source_context`.
The two-argument compatibility handler receives the ID in a copied payload
when the name is available. A collision rejects the operation. Captures belong to one editing
owner and cell, can be claimed once, and expire after 120 seconds from submission.
Each editing owner retains at most 32 captures. Cell/editor cleanup cancels them.
The receiving plugin decides how to use the returned metadata and components.

PDF and DjVu export `page.txt` and an unannotated full-page `page.png`, plus
`document_id`, physical `page`, `page_count`, `text_origin` and component `errors`.
One component can remain usable if the other fails. Reader render/OCR settings
remain owned by the reader. No extraction subprocess is duplicated in core.

## Transport and bounds

### Document offers and the local fast path

[ADR 0055](../adr/0055-local-document-staging-and-early-reuse.md) adds identity
offers and shared local staging while retaining the same application API.
The owned local-service launcher creates a private directory and verifies a
read/write challenge exchange with the service. The action broker grants each
application a separate namespace there. This is optional: remote connections,
independently connected local URLs, and inaccessible shared directories use HTTP.
Loopback addresses do not imply a shared filesystem.

An offer returns `reused`, `shared`, or `http`. For `shared`, the core helper
creates immutable file copies and submits a session token and artifact ID, never
an original path. The frontend validates the files in that client's namespace
and adopts them into its own storage before opening the provider. Adopted files
survive service/client shutdown until their document buffer is deleted. Missing
staging before commit falls back to HTTP; uncertain presentation delivery does
not retry. Offers and shared-open manifests are limited to 64 KiB, alongside the
existing aggregate/file limits below. Provider readiness is still required.

Restart the frontend and its owned service after upgrading to enable this
launch-time negotiation. No plugin setting or additional setup call is needed.

### HTTP content and snapshot replies

1. The target application submits a document or snapshot request to its existing
   client channel. The service checks capabilities and derives the owner.
2. SSE advertises bounded action metadata. The owning frontend fetches content
   over the existing editor-action HTTP route. Artifact bytes stay out of SSE.
3. For document delivery, core stages and validates content before opening it.
   For snapshot capture, the host uses private local worker staging; core encodes
   the result and posts ordered chunks to `/v1/editor-actions/<id>/reply`.
4. The service validates recipient, pending request, offsets and EOF. Only a
   completed reply can be acknowledged as delivered. It streams that reply back
   to the requesting application. Closure, rebind, expiry and cancellation release
   pending content. A late reply cannot change another request.

Artifact wire version 1 is UTF-8 JSON lines: a manifest, ordered base64 blocks
with decoded offsets and SHA-256 of their ASCII base64 spelling, then an end
record. There are 1–16 flat files, at most 256 MiB decoded total, 16 KiB metadata,
24 KiB per decoded block and 64 KiB per wire record. Duplicate names, traversal,
size/checksum/offset mismatches, truncation and trailing data are rejected and
partial files removed. Checksums detect corruption; client/editor ownership
checks remain the delivery boundary. This adds no HTTP authentication mechanism
and retains the existing target-connection trust model.

The host separately limits snapshot requests to eight pending per worker, eight
files and 32 MiB total; its configured text timeout bounds worker export. Document
providers retain their own limits (for example Markdown's 1 MiB main document).
The service retains the existing action inactivity lease, renewed during fetch
and reply upload; frontend asynchronous work sends keepalives. Cancellation
also stops local pending work when the service reports the request closed.

## Remote kernels and rich output

The HTTP fallback works without a shared filesystem: each process uses paths
only on its own machine and transfers bytes through Jusi HTTP. Source snapshot
replies still use this channel for both local and remote targets. Current
HTML/SVG/image/JavaScript kernel MIME output already uses its own bounded SSE
bundle protocol; Neovim builds the local HTML presentation from those contents.
It does not rely on copying a kernel-local HTML pathname. That mechanism remains
unchanged, including its 16 MiB bundle limit. This addition does not tunnel live
servers, resolve arbitrary local file references, or implement Jupyter widgets.

Verification uses separate temporary target/frontend directories and real HTTP,
headless Neovim and provider processes on one machine. Two-machine deployment
remains unverified.
