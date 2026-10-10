# Incident 0026: Editor Open Used The Original Notebook Tab

With a notebook mirrored into a project tab through `:J`, VisiData Ctrl-O
opened its selected-value snapshot beside the notebook in its original tab.
The delivery callback chose the first result of `win_findbuf(notebook)` and
only considered the requesting client when no notebook window was visible.

The initial fix preferred a visible terminal belonging to the action's exact client:
the active window first, then a view in the current tab, then another visible
view of that client. A hidden client still fell back to a visible notebook,
and window selection happened after content transfer.

On 2026-10-09 the user reported the old behavior on another machine. Its loaded
frontend version remains unverified, so that specific occurrence is not yet
attributed. Local regression coverage demonstrates two remaining weaknesses:
a hidden client could redirect to a notebook, and switching tabs during a
chunked transfer could change which client view received the snapshot.

Open now captures the exact visible client window/buffer/surface at request
receipt and revalidates it at delivery. Notebook fallback is removed. Hidden,
closed, or repurposed client windows fail delivery instead of redirecting it.
Chunked transfers and transient fetch retries preserve the captured anchor.
Splits use `nvim_open_win` with an explicit window ID, independently of
`switchbuf` or `:sbuffer`. Copy and display-only diff behavior are unchanged.

`tests/frontend/runtime_spec.lua` exercises the actual delivery callback with
notebook and client views in two tabs, checks the split's column and width,
verifies active-client preference among multiple views in one tab, and covers
hidden notebooks, `switchbuf=useopen,usetab`, tab changes between chunks, and
hidden/closed/repurposed client windows. It now goes through the controller's
request, content fetch and acknowledgment path. Snapshot opens leave the
original tab's layout intact. `editor_delivery_spec.lua` covers preservation
of the captured destination across a transient fetch retry.
