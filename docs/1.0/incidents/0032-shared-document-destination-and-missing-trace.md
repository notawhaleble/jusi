# Incident 0032: Shared document opens lost their destination and trace

- Date: 2026-11-02

An empty `%%read road` cell failed with `Source window is unavailable` after
negotiating shared staging. Destination capture recognized `offer_document` and
`open_shared_document`, but the session's apply callback only resolved a window
for `open`, `open_url` and `open_document`. Shared opens therefore reached the
document renderer with a nil window even when the client terminal was visible.
The hidden-source capture branch also omitted the two new actions.

The asynchronous delivery callback sent renderer errors directly to `vim.notify`
and acknowledged only a generic failed outcome. It did not record a typed
frontend failure or invoke the controller's failure handler, so `JusiTrace` had
no retained cause. Synchronous exceptions used the same bypass, and ordinary
`nil, error` returns lost the error because the third result of `pcall` was not
captured.

Session capture and application now share one managed-open predicate, covering
both offer and shared-open actions alongside HTTP document opens. Existing
source-client identity checks and hidden-window placement rules still apply.
Delivery failures enter the diagnostic recorder and controller failure handler
with the original action trace, client resource, action ID and action kind.
Renderer errors use `frontend_presentation/editor_action/internal_error`; missing
capture destinations use `not_found`. Existing transport/protocol failure origins
are preserved. A failure does not close the client or change kernel truth, and
replayed or cancelled callbacks do not duplicate reports.

The earlier read integration fixture selected its own destination window and
called the renderer directly, bypassing the defective session callback. New
frontend coverage creates a real Jusi notebook session and drives its delivery
handler through visible/hidden shared opens, early reuse, HTTP fallback and
provider readiness failure. Restoring the old apply condition in memory makes
that regression fail with `Shared document received no source window`.
Additional delivery tests cover thrown errors, `nil, error` returns, starter
exceptions and asynchronous failures, verifying retained trace identity and
replay deduplication. Full Python, frontend, e2e and read suites are used for
regression verification; the user's desktop session is not manipulated.
