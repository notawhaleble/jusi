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
- `bind_prepared_client`
- `shutdown_client`
- `inspect_client`

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
- `client_id`: backend-generated prepared/active client id

Notes:

- `sign_id` is not part of the backend contract
- history regions stay frontend-local
- buffer numbers may appear only where client binding requires them
- current ids are backend-generated high-entropy values with `sess-` prefix

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
- provisions a session-scoped prepared client
- prepared state does not become `ready` until frontend binds the real Vim buffer
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
- attached sessions provision a normal prepared client through the same lifecycle as started sessions
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
- requires current prepared client in `ready`
- consumes the session prepared client into the cell's active client
- only after consume does replacement preparation begin
- terminal `cell_updated` may arrive later as an async event

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
  "handler_id": "vd",
  "message_type": "bootstrap_done",
  "payload": {
    "bufnr": 91
  }
}
```

Behavior:

- symmetric plugin/frontend message path
- request form is frontend -> backend
- event form is backend -> frontend
- valid only while that client still has an active handler instance registered
- current built-in `%%vd` path uses this for:
  - `handler_snapshot`
  - `action_request`
  - later frontend replies such as `bootstrap_done`

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
- clears prepared state
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
- restarts prepared-client provisioning
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

### `bind_prepared_client`

```json
{
  "notebook_id": "nb-1",
  "session_id": "sess-1",
  "client_id": "client-1",
  "client_bufnr": 91
}
```

Behavior:

- acknowledges the real Vim buffer for the current prepared client
- transitions prepared state from `binding` to `ready`

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
- tears down either a prepared client or active cell client
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

### `prepared_updated`

```json
{
  "notebook_id": "nb-1",
  "prepared": {
    "id": "client-1",
    "state": "binding",
    "bufnr": -1,
    "client_state": "active"
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
    "client_bufnr": 91,
    "client_state": "active"
  }
}
```

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
- current built-in `%%vd` PTY path also uses it for live terminal traffic:
  - `terminal_input`
  - `terminal_output`
  - `terminal_prompt`
- for PTY-backed handlers, pushed `handler_message` events are now the hot path
- `inspect_client` remains the fallback/debug snapshot path for those handlers

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
- PTY-backed handler output now bypasses that hot path and is pushed directly through `handler_message`
