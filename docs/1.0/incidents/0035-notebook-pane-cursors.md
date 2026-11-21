# Incident 0035 — Notebook pane cursors

With two windows displaying the same notebook, executing from the right pane
could submit the cell under the left pane's cursor. An uncounted cell-mode
`Q` used the same lookup and could close the other cell's client. Both commands
used Neovim's first window displaying the buffer. They now read the invoking
pane's cursor. Output projections still identify their cell through stored
model identity, and counted commands still select the explicit output ID.

Starting a notebook replaces its offline editor. Restart replaces its connected
editor. Both paths removed manual history folds by running a Normal command
at each fold's line. This moved each pane's cursor during teardown. Fold
cleanup now saves and restores the view in each affected window as it runs.
A user can continue navigating while a lifecycle request is pending.

The headless runtime regression submits cells from both panes and closes the
client in the focused pane. The target lifecycle test exercises real start and
restart with a folded notebook and switches panes before restart completes.
