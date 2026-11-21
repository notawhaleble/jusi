# ADR 0050: Current-tab notebook cleanup

Status: accepted

## Decision

`:JusiCloseTab`, mapped to literal `\Q`, belongs to core notebook interaction.
It applies to every notebook represented by a visible notebook/output buffer in
the current tab, regardless of which window has focus. The mapping is global in
Normal mode, including cell mode, and respects existing user mappings. It complements temporary views created by `:J` or
optional helpers such as JShell without depending on any plugin family.

For each selected notebook, the command captures its visible output/client
projections and notebook windows in the invoking tab. Hidden artifacts and artifacts visible
only in other tabs are outside its selection. A projection also visible in
another tab is hidden only in the invoking tab; its artifact/client remains
usable elsewhere. Artifacts visible only in the invoking tab are explicitly
closed through the existing cell lifecycle, including exact active-execution
interruption and backend-client cleanup. No new protocol or kernel state is
introduced, and the notebook's kernel, service, model and text are retained.

Client-owned document slots (`lifetime="client"`) count as views of their
owning client, including when they cover its hidden terminal. Session discovery
and client-view enumeration use the registered ownership, so cleanup also works
after the notebook window has been manually closed. Sharing is evaluated across
both terminal and document views: either kind visible in another tab protects
the client. Independent documents do not select or protect a client.
Client teardown closes owned-document windows before deleting their buffers;
explicit `close_document` instead restores the terminal for continued use.

Once selected cleanup completes successfully, captured notebook windows are
closed while another view of that notebook remains. The last view is always
retained; visibility is checked again before each close. With several notebook
windows in the current tab and none elsewhere, one remains. Unrelated project
windows and independent application exports are outside this operation.

The cell lifecycle exposes completion callbacks for explicit cleanup. A failure
keeps the captured notebook views for retry and follows existing failure
reporting; successfully closed artifacts are not recreated. Repeated invocation
during the same tab cleanup is coalesced. Asynchronous completion validates the
original session, tab, window and buffer rather than using current editor focus.
Runtime replacement invalidates its notebook-close continuation. Repurposed or
moved windows are not closed by an old continuation.

Offline notebooks support the same notebook-window cleanup without backend
work. Native `:close` still only hides a projection; `JusiClose` retains its
existing single-cell cleanup meaning.

## Verification

Frontend tests cover both notebook modes, output mappings, visible/hidden
selection, shared-client preservation, interruption before close, asynchronous
completion after a tab switch, the last notebook view, cleanup failure/retry,
repurposed windows, text/kernel retention and runtime replacement. The real
terminal scenario closes a busy client from a temporary project tab while
preserving the source notebook and unrelated project window.
Document regressions cover a hidden terminal, document-only session discovery,
mixed terminal/document sharing, independent books, failure/retry, staging
release and repurposed windows. The real terminal scenario overlays the client
with a Markdown document before invoking the mapping and verifies that both
the explanation and followup state disappear.
