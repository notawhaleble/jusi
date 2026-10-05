# Incident 0026: Editor Open Used The Original Notebook Tab

With a notebook mirrored into a project tab through `:J`, VisiData Ctrl-O
opened its selected-value snapshot beside the notebook in its original tab.
The delivery callback chose the first result of `win_findbuf(notebook)` and
only considered the requesting client when no notebook window was visible.

Open now prefers a visible terminal belonging to the action's exact client:
the active window first, then a view in the current tab, then another visible
view of that client. Only a hidden client falls back to a visible notebook,
again preferring the current tab. With no visible source, delivery still fails.
Copy and display-only diff behavior are unchanged.

`tests/frontend/runtime_spec.lua` exercises the actual delivery callback with
notebook and client views in two tabs, checks the split's column and width,
verifies active-client preference among multiple views in one tab, and covers
the hidden-client notebook fallback. Snapshot opens leave the original tab's
layout intact.
