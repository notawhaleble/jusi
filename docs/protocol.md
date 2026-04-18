# Jusi Protocol Draft

This document is the shared contract between `jusi` and `jusivim`.

The wire may use Vim terminal/channel APIs, but the protocol stays transport-agnostic.

## Scope

Current backend slice covers:

- `start_session`
- `attach_session`
- `execute_cell`
- `interrupt_cell`
- `input_reply`
- `handler_message`
- `healthcheck_reply`
- `disconnect_session`
- `reconnect_session`
- `stop_session`
- `shutdown_client`
- `inspect_client`
- terminal-backed client transport advertisement on client events/inspection snapshots

## Envelope

All messages use:

```json
{
  "version": 1,
  "kind": "request",
  "type": "start_session",
  "request_id": "req-123",
  "payload": {}
}
```

Fields:

- `version`
- `kind`: `request`, `response`, `event`
- `type`
- `request_id`: required for request/response
- `payload`

## Identities

- `notebook_id`: frontend notebook runtime id
- `session_id`: backend-generated durable session id
- `cell_id`: frontend runtime cell id
- `client_id`: backend-generated cell-owned client id

Notes:

- `sign_id` is not part of the backend contract
- history regions stay frontend-local
- buffer numbers may be absent or unbound from backend perspective
- current ids are backend-generated high-entropy values with `sess-` prefix

## Client Transport

Backend clients may expose one of these transport kinds:

- `inspection`
  - current default
  - frontend renders through `client_updated` plus `inspect_client`
- `native_terminal`
  - native-terminal-friendly client advertisement for fullscreen interactive clients
  - frontend should attach a real editor terminal buffer to the backend-provided attach command/substrate

Current PTY traffic over `handler_message` is transitional rather than the long-term native-terminal transport.
The first concrete native-terminal slice now advertises:

- `attach_cmd`
- `attach_env`
- `session_id`
- `client_id`
- optional `handler_id`

## Session Target

Backend session metadata currently keeps only explicit `target`:

```json
{
  "source": "start",
  "alias": "python3",
  "kind": "kernel",
  "value": "",
  "config": {}
}
```

Fields:

- `source`
- `alias`
- `kind`
- `value`
- `config`

Notes:

- backend sessions are treated as durable/reconnectable by default
- backend no longer models separate `link` metadata
- `endpoint`/backend residence remains a frontend transport concern, not core backend session state

## Requests

### `start_session`

```json
{
  "notebook_id": "nb-1",
  "kernel_name": "python3",
  "target": {
    "source": "start",
    "alias": "python3",
    "kind": "kernel",
    "value": "",
    "config": {}
  }
}
```

Behavior:

- backend is expected to be launched by `jusivim`
- starts a new durable session for the notebook
- enters `starting`, then `connected`
- backend generates a durable `session_id`
- frontend may omit `target`; backend derives the current default target from `kernel_name`

### `attach_session`

```json
{
  "notebook_id": "nb-1",
  "target": {
    "source": "attach",
    "alias": "",
    "kind": "connection_file",
    "value": "/path/to/kernel.json",
    "config": {}
  }
}
```

Behavior:

- attaches to an existing durable session target
- backend generates a durable `session_id` for the Jusi-side binding
- current honest attach slice is intentionally narrow:
  - `target.kind` must be `connection_file`
- this path is now real in both the in-memory runtime and the managed runtime
- attached sessions now only establish the durable backend session; client allocation happens on execute
- managed runtime keeps a small connection-file sidecar registry of attached Jusi root-process PIDs for stop fanout
- other target kinds may still be recorded as identity, but are not executable attach paths yet

### `execute_cell`

```json
{
  "notebook_id": "nb-1",
  "session_id": "sess-1",
  "cell": {
    "id": 12,
    "kind": "code",
    "syntax": "python",
    "main_lines": ["print('x')"]
  }
}
```

Behavior:

- requires `connected` session
- allocates the real execution client directly for that cell
- frontend may first see the cell become `busy` before a local buffer exists for that client
- once backend emits the real `client_id` on `cell_updated`, frontend can bind or create the local buffer for that client
- terminal `cell_updated` may arrive later as an async event
- current long-term direction is:
  - every cell still enters through the Jupyter kernel
  - handler takeover should be driven by a kernel-emitted Jusi handoff mime payload, not by backend header parsing alone
  - once that handoff is accepted, backend will start one handler worker process for that active handler-owned cell/client

### `interrupt_cell`

```json
{
  "notebook_id": "nb-1",
  "session_id": "sess-1",
  "cell_id": 12
}
```

Behavior:

- routes interrupt by current execution owner
- fails explicitly if owner is unknown or not interruptible

### `input_reply`

```json
{
  "notebook_id": "nb-1",
  "session_id": "sess-1",
  "cell_id": 12,
  "client_id": "client-7",
  "value": "typed text"
}
```

Behavior:

- valid only while that exact active client is waiting on `input_request`
- resumes the original execution; does not create a new execution identity

### `handler_message`

```json
{
  "notebook_id": "nb-1",
  "session_id": "sess-1",
  "client_id": "client-7",
  "handler_id": "sqlite",
  "message_type": "followup",
  "payload": {
    "cell_text": "select 2"
  }
}
```

Behavior:

- symmetric plugin/frontend message path
- request form is frontend -> backend
- event form is backend -> frontend
- valid only while that client still has an active handler runtime registered
Design direction:

- frontend -> backend structured call
- backend -> frontend structured callback

Current live handler usage:

- frontend -> backend call:
  - `followup`
  - `complete`
- backend -> frontend callback:
  - current structured action callback:
    - `action_request`

Boundary note:

- if an operation can be completed entirely on the backend/plugin side, it should stay there
- `copy` is not currently part of the generic frontend callback model
- frontend should not be expected to implement plugin-specific operational logic for such paths

Current backend -> frontend built-in action shape:

```json
{
  "notebook_id": "nb-1",
  "session_id": "sess-1",
  "client_id": "client-7",
  "handler_id": "oc",
  "message_type": "action_request",
  "payload": {
    "action_type": "open_path",
    "payload": {
      "path": "/tmp/example.txt",
      "open_in": "split",
      "line": 12,
      "column": 3
    }
  }
}
```

Current built-in frontend action:

- `action_type = open_path`
  - `path` is required
  - `open_in` is optional
  - `line` is optional and 1-based
  - `column` is optional and 1-based

Direction note:

- current native-terminal direction does not keep raw terminal transport on the notebook control channel
- `handler_message` remains the structured control channel for:
  - follow-up
  - completion
  - plugin commands
- backend root process remains the router for this traffic; frontend does not talk to handler workers directly

Current generic completion result shape on the handler channel:

```json
{
  "notebook_id": "nb-1",
  "session_id": "sess-1",
  "client_id": "client-7",
  "handler_id": "sqlite",
  "message_type": "complete_result",
  "payload": {
    "handler_id": "sqlite",
    "message_type": "complete",
    "items": [
      {
        "value": "SELECT",
        "label": "SELECT",
        "kind": "keyword",
        "detail": "keyword",
        "documentation": null,
        "start_col": 0,
        "end_col": 6
      }
    ]
  }
}
```

Completion replacement semantics:

- `start_col` / `end_col` are optional
- when present, frontend should replace exactly that 0-based half-open range in `line_text`
- `end_col` is exclusive
- when absent, frontend may fall back to generic token replacement

Attach note for native-terminal clients:

- current native-terminal attach metadata is available as part of normal client state
- frontend attach lifecycle is simply:
  - execute handler cell
  - observe `client.transport.kind = native_terminal`
  - launch the terminal client from `attach_cmd` + `attach_env`
- current `jusi_vd` plugin uses this by carrying serialized handoff payload into the generic core `plugin-runtime` entrypoint plus a plugin-owned callable

### Planned Kernel Handoff Direction

The next handler activation model is expected to add a kernel-emitted Jusi handoff mime payload carrying:

- explicit `handler_id`
- explicit `magic_name`
- raw cell content/startup payload
- handler-specific metadata

That handoff should be enough for backend to start the correct handler worker process without further kernel messaging.

Current first slice:

- managed runtime now recognizes `application/vnd.jusi.handoff+json` in kernel `display_data` / `execute_result`
- backend records that as a structured handoff event in the active client transcript/view
- backend registry now supports validating `magic_name -> handler_id` handoff combinations, including one magic mapping to multiple handlers
- matched handler-owned executions now run in a dedicated `handler-worker` subprocess
- backend root process remains the router/supervisor for frontend <-> handler traffic
- backend now prefers validated kernel handoff to start the worker when that handoff is present
- backend no longer starts plugin workers from header parsing in `ExecuteCell`
- the in-memory runtime now synthesizes handoff events for magic cells so tests and the non-managed stub path still exercise the same worker activation flow

### `healthcheck_reply`

```json
{
  "notebook_id": "nb-1",
  "session_id": "sess-1",
  "healthcheck_id": "hc-123"
}
```

Behavior:

- valid only while the session is still `connected`
- acknowledges the current backend-issued `healthcheck` event
- clears the outstanding frontend-liveness check and refreshes backend liveness tracking

### `disconnect_session`

```json
{
  "notebook_id": "nb-1",
  "session_id": "sess-1",
  "reason": "transport_lost"
}
```

Behavior:

- moves the session to `disconnected`
- active execution ownership degrades to `unknown`
- session identity remains durable for later reconnect
- session payload exposes `expires_at` as the current disconnect deadline
- if that deadline passes, backend performs final session teardown without waiting for a reconnect attempt
- backend may also enter this path after missed frontend healthchecks

### `reconnect_session`

```json
{
  "notebook_id": "nb-1",
  "session_id": "sess-1"
}
```

Behavior:

- valid only from `disconnected`
- does not invent false active execution ownership
- fails with:
  - `session_not_found` for unknown notebook/session identity
  - `session_stopped` for already-stopped sessions
  - `session_expired` when the disconnect deadline has passed

### `stop_session`

```json
{
  "notebook_id": "nb-1",
  "session_id": "sess-1"
}
```

Behavior:

- explicitly tears down the durable Jusi session
- request acknowledges promptly with `stopping`
- terminal `stopped` may arrive later as an async event
- for externally attached `connection_file` sessions, managed runtime now:
  - sends kernel shutdown through the attached Jupyter client
  - tears down local Jusi channels and clients
  - signals peer attached Jusi root processes registered for the same connection file so they shut down too
- timeout teardown follows that same whole-session rule for attached `connection_file` sessions

### `shutdown_client`

```json
{
  "notebook_id": "nb-1",
  "session_id": "sess-1",
  "cell_id": 12,
  "client_id": "client-2",
  "reason": "user_close"
}
```

Behavior:

- separate from interrupt
- tears down an active cell client
- cell status and client lifecycle remain separate concerns

### `inspect_client`

```json
{
  "notebook_id": "nb-1",
  "session_id": "sess-1",
  "client_id": "client-2"
}
```

Behavior:

- returns a derived backend-owned client view snapshot
- includes a monotonic `revision` for polling consumers
- may later include terminal attach metadata for recovery/debug, but that is not intended to be the primary live transport for native-terminal clients

## Events

### `session_updated`

```json
{
  "notebook_id": "nb-1",
  "session": {
    "id": "sess-1",
    "state": "connected",
    "kernel_name": "python3",
    "connection": "inmemory://python3/1",
    "target": {
      "source": "start",
      "alias": "python3",
      "kind": "kernel",
      "value": "",
      "config": {}
    },
    "expires_at": null,
    "last_error": "",
    "last_action": "start"
  }
}
```

### `cell_updated`

```json
{
  "notebook_id": "nb-1",
  "cell": {
    "id": 12,
    "status": "busy",
    "owner": {"kind": "kernel"},
    "client_id": "client-1",
    "client_state": "active"
  }
}
```

Notes:

- `client_id` appears when backend has allocated the real execution client
- `client_bufnr` may be omitted when frontend has not yet bound a local buffer
- native-terminal transport metadata belongs to that real execution client, not to any session-level prepared slot
- `owner` is independent from `status`

### `client_updated`

```json
{
  "notebook_id": "nb-1",
  "session_id": "sess-1",
  "client_id": "client-1",
  "revision": 7
}
```

Behavior:

- emitted when backend observes that a client's visible view revision changed
- intended as a redraw invalidation signal, not as a full view payload
- frontend should respond by calling `inspect_client` for the same client if it needs the updated snapshot
- this is the first backend-driven redraw signal for client rendering; `inspect_client` remains the content source for now
- planned native-terminal clients are expected to rely less on this hot path and more on direct terminal attachment via advertised transport metadata

### `handler_message`

```json
{
  "notebook_id": "nb-1",
  "session_id": "sess-1",
  "client_id": "client-1",
  "handler_id": "vd",
  "message_type": "handler_snapshot",
  "payload": {
    "handler_id": "vd",
    "mode": "browse",
    "entry": "%%vd pods"
  }
}
```

Behavior:

- backend -> frontend side of the structured handler channel
- used for plugin/display-handler control messages
- current native-terminal direction keeps `handler_message` for control semantics, not as the live fullscreen terminal transport
- `inspect_client` remains useful for debug/recovery metadata, but the real terminal surface is the advertised native-terminal attach command

### `healthcheck`

```json
{
  "notebook_id": "nb-1",
  "session_id": "sess-1",
  "healthcheck_id": "hc-123"
}
```

Behavior:

- emitted by backend while a session is `connected`
- frontend should reply with `healthcheck_reply`
- if replies stop long enough, backend marks the session `disconnected` and starts the existing disconnect-timeout flow
- known issue: if Vim is suspended, for example with `Ctrl-Z`, backend may treat the missing reply as link loss

## Current Limitations

- `start_session` still routes actual start behavior mainly through `kernel_name`
- `attach_session` is only real for `target.kind=connection_file`
- durable session metadata is currently in-memory per backend root process; cross-process persistence is still future work
- transcript-style client redraw remains invalidation-plus-pull for now:
  - backend emits `client_updated`
  - frontend still pulls the full snapshot through `inspect_client`
- native-terminal pivot is now preferred for fullscreen interactive handlers:
  - backend should advertise terminal-backed clients explicitly
  - frontend should attach a real editor terminal buffer to the backend-provided client substrate
  - `handler_message` should remain for control semantics rather than carrying fullscreen terminal transport bytes
