# ADR 0036: Applications Initiate Acknowledged Editor Actions

- Status: accepted
- Date: 2026-09-09

## Decision

Applications capture copy/open content at the moment the user invokes an action.
An interactive client declaring `editor_actions` receives a private target-local
Unix socket through `JUSI_EDITOR_ACTION_SOCKET`. The service binds that endpoint
to the exact client/runtime/notebook/cell; a helper cannot select another client
by supplying its ID. The socket directory is private and its file mode is 0600.
It is owned and removed with the client, including failed startup and shutdown.

`jusi.editor_client.copy(text, linewise=False)` and
`jusi.editor_client.open_text(text, name="selection.txt", filetype="")` submit
snapshots and wait for delivery confirmation. They do not import the kernel or
Neovim. The installed `jusivim PATH [--filetype TYPE]` command reads a file in the
helper's own target filesystem and working directory, then uses the same channel.
No target path, file relay, SCP or shared storage enters frontend delivery.
Applications own selection, serialization, keybindings and display of failures.
The helpers can run outside an ordinary worker request; callers that need a
responsive application UI can invoke them off its UI thread after capturing data.

The channel uses bounded length-prefixed JSON, with separate application request
and service action IDs. The service registers the action waiter before publishing
`editor_action.requested`. Ordered events and health contain only metadata.
`GET /v1/editor-actions/{action_id}?editor_id=...` fetches content and its remaining
delivery lifetime. `POST` on that URL accepts `ack_editor_action` with the exact
recipient, action ID and `delivered` or `failed` outcome. Neither endpoint uses the
ordinary plugin-work lane. A delivery succeeds only after the frontend applies
content and acknowledges it; repeated matching acknowledgments are idempotent.
Schemas and shared fixtures are in `protocol/schema/v1/editor-actions.schema.json`
and `protocol/fixtures/v1/`.

## Recipient and Lifetime

A controller has a separate `editor_id`, stable across its transport reconnects.
The exclusive terminal attach optionally carries this ID; binding occurs after
attachment validation and before application launch. SSE tracks live connections
for that recipient, with separate connection IDs so a late old disconnect cannot
invalidate a replacement connection. IDs express ownership within the existing
service trust boundary; this is not a new authentication mechanism.

A new action requires a connected owning editor. An accepted action remains
bound to that original editor and exact source generation. Replacing a terminal's
editor cancels unfetched actions and marks fetched actions uncertain. Another
editor never inherits them. Reconnection inspects pending metadata and replays
notices; Neovim checks source ownership again after fetch, before mutation.

Neovim stores the application outcome before acknowledging it. Duplicate notices
only repeat the acknowledgment. Its cache retains 256 completed entries and pins
unconfirmed outcomes through the bounded service delivery lifetime, preventing
later successful actions from evicting a still-pending delivery. A disconnected
editor or retired source cannot apply a late fetch response. Delivery after loss
of editor memory is not claimed to be exactly once.

Client/runtime close and service shutdown cancel pending actions, release waiters,
and close channels. Closing an unfetched action yields `cancelled`; if content
was fetched, the service reports `unknown` because mutation may have happened
without an acknowledgment. Expiry similarly yields `failed` before fetch and
`unknown` after fetch. Missing recipients, invalid content and unavailable files
fail only the action. Helpers never automatically resubmit an uncertain action.

## Bounds and Presentation

- Content retains ADR 0035's 512 KiB UTF-8/no-NUL bound and copy `v`/`V` types.
  The encoded local frame also has a 1 MiB limit, reached sooner by heavily
  escaped JSON. Open names are basename hints, not executable commands or paths.
- At most 32 actions and 4 MiB of text are pending per target service. Completed
  metadata is pruned to 128 tombstones; content is released immediately on finish.
- Delivery has a 30-second lifetime; the helper's socket wait is 35 seconds.
  This bounds an automatic transfer, not kernel execution, input, followups or
  human editing. The frontend subtracts fetch round-trip time conservatively
  before applying content.
- Copy updates the unnamed/yank registers without moving focus. Open focuses a
  split anchored to a visible window of the source notebook or its terminal.
  The new modifiable unsaved buffer is independent of the source client; source
  cleanup never deletes it. No visible source window means open fails.

## Scope and Verification

The internal editor-invoked API from ADR 0035 remains reusable, with no public
`JusiCopy`/`JusiOpen` commands. Application work publication/interruptible work
leases, binary/large transfers, optional location hints, multiple-file actions,
and edit-and-return/writeback remain separate extensions. This implementation
supplies terminal applications with the channel; it does not migrate `%%vd` or
any external plugin.

Manager, socket, Python/Lua conformance and frontend tests cover recipients,
replay, acknowledgment, disconnect/reconnect, expiry, capacity, stale sources,
close while waiting and cache pressure. The real terminal fixture proves copy
while an ordinary followup is blocked, selected-text open, helper-read file open,
and exported-buffer survival after client close. Tests use loopback HTTP across
separate processes; deployment on a remote machine remains a later verification
step, with no remote-specific content delivery branch in this implementation.
