# ADR 0053: Client-scoped URL navigation slots

- Status: accepted
- Date: 2026-10-26

## Context

A terminal application can open an independent browser page using ADR 0052's
generic URL action. A sequence of navigation requests sometimes needs to
replace the application's earlier page, including one hidden with native
window close. Backend plugins cannot safely identify frontend buffers, and
browser-specific or plugin-specific keybindings do not belong in core.

## Decision

Extend `open_url` content with an optional `slot`, a 1-64 character ASCII
identifier containing letters, digits, underscores or hyphens. An unkeyed open
retains existing independent snapshot behavior. A keyed open first closes and
deletes the previous projection in that same client's slot, then creates a new
web buffer. Failure to create the replacement leaves the slot empty.

Managed pages replace the source terminal's visible window contents, without
creating another split. The terminal buffer and client remain alive. Explicit
slot close restores the terminal in windows still displaying the page before
deleting it. Hidden pages are deleted without changing repurposed windows.
Native page/window close still hides the projection; the normal cell focus
command reveals the retained terminal. Failed host rendering restores the
previous window contents.

Add `close_url` content `{action: "close_url", text: "", slot: "page"}`.
Closing an absent, manually deleted, or hidden projection is idempotent.
Application helpers are `jusi.editor_client.open_url(url, slot="page")` and
`close_url(slot="page")`; the worker SDK has corresponding result helpers.
Delivery acknowledgment confirms the frontend action, not page-load completion.
Callers serialize their own navigation requests by waiting for acknowledgments.

Slot names live only in fetched action content. The frontend scopes replacement
authority to its notebook session and the already verified source client, never
to a process-global name or plugin identity. No frontend buffer numbers, window
IDs, commands or backend pathnames enter the protocol. Existing editor-action
ownership, retirement fencing, reconnect deduplication and delivery leases
apply to both operations. Close does not require a visible source window. Open
captures the verified source terminal identity, then resolves managed placement
across all tabs when applying the fetched content. If neither terminal nor page
is visible, reveal the same source terminal in a new window. Windows reused for
unrelated content are preserved. Worker-returned managed URL actions use the
same placement and replacement path as application actions. Unkeyed exports
retain their captured-window validation. Failed page deletion retains the slot
and keeps close retryable; it cannot silently abandon a live page.

Closing or hiding a page does not close its source client or terminal. Explicit
source-client close forgets slot replacement authority while leaving the last
delivered page available, preserving URL exports' independent lifetime. This
does not introduce a durable backend web surface or browser navigation feedback.

## Verification

Shared valid/invalid fixtures cover slot validation and close content in Python,
Lua and JSON Schema. Socket integration checks private payloads and acknowledged
close. Frontend tests cover hidden-page replacement, repeated close, manual
deletion, invalid requests, source survival, same-window presentation and
restoration, repurposed-window isolation, and isolation of two clients' slots.
The external jusi-web integration scenario additionally exercises a real Jusi
service, kernel, worker, PTY/VisiData application and Neovim with a substitute
WebBuffer command. It covers bootstrap, ambiguous bookmarks, citations, native
page close followed by another drilldown, list followups closing the old page,
separate cells retaining separate pages, and recoverable API failure.
