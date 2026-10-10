# ADR 0050: Current-tab notebook cleanup

Status: accepted

## Decision

`:JusiCloseTab`, mapped to literal `\Q`, belongs to core notebook interaction.
It applies to the notebook owning the current notebook/output buffer, in the
current tab. The mapping works in Normal mode, including cell mode, and respects
existing user mappings. It complements temporary views created by `:J` or
optional helpers such as JShell without depending on any plugin family.

The command captures this notebook's visible output/client projections and
notebook windows in the invoking tab. Hidden artifacts and artifacts visible
only in other tabs are outside its selection. A projection also visible in
another tab is hidden only in the invoking tab; its artifact/client remains
usable elsewhere. Artifacts visible only in the invoking tab are explicitly
closed through the existing cell lifecycle, including exact active-execution
interruption and backend-client cleanup. No new protocol or kernel state is
introduced, and the notebook's kernel, service, model and text are retained.

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
