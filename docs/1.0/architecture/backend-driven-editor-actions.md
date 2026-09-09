# Backend-driven editor actions

Status: proposal, not implemented. Public `JusiCopy`/`JusiOpen` commands are
withdrawn; ADR 0035's content validators and internal delivery helpers remain.
No production VisiData or shell plugin is changed by this review.

## User workflows

- In VisiData, `zY` copies the current value to Neovim's unnamed register.
- In VisiData, Ctrl-O opens the current value in a Neovim split.
- In a shell client, `jusivim /some/file` reads that target-side file and opens
  its content in Neovim. Relative paths resolve where the helper is invoked,
  including its container filesystem and current directory.

The application captures content when the user invokes the action. The editor
must not later query a changed cursor selection. Opening is initially a content
snapshot, independent of its source client. Writeback is a separate operation.

## Applicable legacy behavior

Read-only review of `../jusi-0.x/src/jusi/visidata_support.py` found:

- `syscopy_value` sends a `yank_text` action with the value's string form.
  `syscopy_cells_async` renders display values into tab-separated rows before
  sending. Selection and serialization are application-owned.
- `append_plugin_frontend_action` emits independently of followup/execute
  responses, through a runtime socket with a file-journal fallback.
- `launch_editor` sends `open_path` requests, including an optional line and
  multiple files. This expresses opening intent rather than terminal output.
- `launch_external_editor` has different semantics: it writes a temporary value,
  sends an `edit_path` request, waits for its request ID's result, and reads the
  edited file back. This is a round trip, not merely open confirmation.

`../jusi-shell/src/jusi_shell/runner.py` injects shell helpers which send paths to
client-owned local IPC. `../jusivim/autoload/jusi/session.vim` opens those paths
and returns edit completion on buffer hide/unload/wipe, using the modified flag.
Its remote-filesystem restriction explains why this design did not travel well.

Preserve application-originated actions, captured content, exact request IDs,
optional location hints and a return channel. Do not carry forward shared
filesystem paths, optimistic “copied” status before delivery, or implicit edit
completion on buffer hiding. Register a result waiter before publishing its
request: legacy blocking edit publishes first, leaving a possible fast-reply race.

## Proposed flow

1. Core gives each target application a client-scoped action channel at launch.
   A small helper API is usable by Python applications and a shell CLI alike.
   It must not depend on a running `handle()` request or use terminal escapes.
   A private local socket to a runtime-owned service adapter is a candidate;
   this is target-local IPC, not a frontend-local service or remote file relay.
2. The application snapshots text and submits a generic copy/open action.
   Core derives client/runtime ownership from the channel, rather than trusting
   caller-supplied arbitrary client IDs. Each action has its own ID and trace.
3. The target service retains bounded content and publishes an ordered
   availability notification addressed to the owning frontend. Payload content
   stays out of lifecycle events and diagnostics. The frontend fetches it over
   HTTP using the action identity.
4. Neovim validates ownership and content, applies the existing register/buffer
   delivery helper, then acknowledges the exact action over HTTP. Copy leaves
   focus alone; open focuses a split belonging to that notebook's presentation.
   The application/helper reports success only after this acknowledgment.

The same flow handles local and remote targets. The shell helper reads the file
in its own execution context and sends bytes; neither the service nor Neovim
needs access to that filesystem path. Large/binary content can extend the
transport later without making open depend on SCP or shared storage.

## Ownership and failure details to settle in the wire contract

- Introduce an explicit editor recipient binding. A terminal attachment ID is
  transport identity and can change on reconnect; it is not sufficient as the
  durable editor recipient. Do not broadcast copy/open to every SSE reader.
- Bind an action to its original recipient and source generation. A replacement
  editor or a new client on the same cell must not inherit old actions. Report
  an unavailable recipient visibly instead of silently queuing surprise opens.
- Reconnect must inspect pending action metadata as well as replay notifications.
  Within the same editor lifetime, deduplicate by action ID so replay or a lost
  acknowledgment does not create another split or overwrite the register again.
  Across editor loss, report uncertain delivery rather than promise exactly-once
  application or retry an already-performed side effect blindly.
- Client/runtime close cancels undelivered actions and releases application
  waiters. Already delivered buffers remain independent. Control acknowledgment
  must bypass ordinary work, including an application waiting for that result.
- Missing files, invalid content, disconnected editors and delivery failures
  affect the action, not an otherwise healthy client or kernel. Service shutdown
  and channel loss must also unblock helpers. Define bounded pending count/bytes,
  retention, and transport waits explicitly; none is a timeout on human editing.

## Separate future edit operation

Legacy provides evidence for editing a value and returning the modified text.
If requested, give that a distinct edit session with explicit accept/cancel and
content returned over HTTP. “Opened successfully” must not mean “editing is
finished”; hiding a buffer must not silently commit or cancel changes. Remote
file writeback additionally needs its target owner and conflict policy.

## Implementation gate

Prove the generic channel with fixtures before touching `%%vd`: copy, open,
helper-read file content, unavailable recipient, wrong/stale ownership, duplicate
notification/ack, disconnect/reconnect, close while waiting, and delivery while
ordinary plugin work is active. Keep the real plugin migration and its keybinding
hooks for the subsequent plugin-development step.
