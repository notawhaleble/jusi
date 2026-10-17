# Incident 0028: History folds, navigation, and resize

Reported symptoms included duplicate or misplaced history folds after plugin
commands, history labels in unrelated diff folds, slow cell navigation through
long histories, and delayed terminal geometry updates.

Manual folds can split or drift independently of their extmarks after text
replacement and undo. Endpoint level checks do not prove a single exact fold.
Affected-cell repair now removes all native fold fragments before rebuilding
the history range, excluding the opener, body, and closer. Window options are
set with explicit local scope, and the fold-text callback falls back to native
text outside an exact notebook history range.

The synchronous editing worker previously highlighted every history entry of
a visible cell, including folded history. It now receives only visible expanded
entries. Each entry retains its complete isolated syntax context. Read-only
cell snapshots are cached for one buffer changedtick to avoid repeated marker
walks on navigation and fold redraw.

The terminal bridge dropped resize signals received while a previous send was
pending. It now drains pending changes to the latest geometry and checks for
geometry changes during attachment. A separate end-to-end test verifies a real
target PTY receives a window resize within one second. The reported delay was
not reproduced in the idle terminal case; synchronous frontend highlighting
could also delay editor events.

Regression coverage: frontend history and editor-action suites, terminal bridge
integration tests, and the real terminal-surface end-to-end scenario.
