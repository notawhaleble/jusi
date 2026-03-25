# Jusi Protocol Draft

This document is the shared contract between `jusi` and `jusivim`.

It defines the message schema and state model. The concrete wire transport may use Vim terminal or channel APIs, but the contract itself is transport-agnostic.

## Scope

This draft covers the first backend milestone:

- start managed session
- attach to existing session
- execute current cell
- interrupt current execution
- stop session
- report async session, prepared-client, and cell updates

## Transport Assumptions

- the frontend launches or connects to a backend process
- messages are UTF-8 JSON envelopes
- delivery is full-message based
- requests and events are versioned
- the transport may be stdin/stdout over Vim terminal or job/channel APIs

The transport adapter is allowed to carry editor-specific routing metadata. The protocol payloads below are the stable contract and should avoid Vim rendering details such as sign placement.

## Envelope

All messages use the same envelope:

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

- `version`: protocol version
- `kind`: `request`, `response`, or `event`
- `type`: operation or event name
- `request_id`: required for `request` and matching `response`
- `payload`: object

## Identity Model

The backend contract should use explicit ids:

- `notebook_id`: frontend-generated runtime id for the notebook buffer binding
- `session_id`: backend-generated id for a kernel session binding
- `cell_id`: frontend runtime cell id from the notebook model
- `client_id`: backend-generated id for a prepared or active client

Notes:

- `cell_id` is already the stable per-notebook runtime identity in `jusivim`
- `sign_id` must not appear in the backend protocol
- buffer numbers may appear in transport-local metadata, but not as core protocol identity

## Requests

### `start_session`

```json
{
  "notebook_id": "nb-1",
  "kernel_name": "python3"
}
```

Behavior:

- start a managed kernel session for the notebook
- enter session state `starting`
- eventually emit session and prepared-client events
- backend provisioning does not make prepared state `ready` by itself; frontend buffer binding is a separate step

### `attach_session`

```json
{
  "notebook_id": "nb-1",
  "target": {
    "connection_file": "/path/to/kernel.json"
  }
}
```

Behavior:

- attach to an existing kernel
- mark session as `attachable`

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

- requires connected session
- requires prepared client in `ready` state
- consumes the prepared client for the target cell
- starts replacement prepared-client provisioning separately

Notes:

- history-region handling remains frontend-local for now
- the backend only needs the executable cell body

### `interrupt_cell`

```json
{
  "notebook_id": "nb-1",
  "session_id": "sess-1",
  "cell_id": 12
}
```

Behavior:

- represents user intent to interrupt the active execution associated with the cell
- actual low-level interrupt routing depends on the current execution owner
- if the backend cannot identify an interruptible owner, the request should fail explicitly

### `stop_session`

```json
{
  "notebook_id": "nb-1",
  "session_id": "sess-1"
}
```

Behavior:

- stops the current Jusi session cleanly
- for managed sessions, this represents backend-owned kernel shutdown
- prepared-client state becomes unusable immediately
- active executions become terminal from the Jusi session point of view

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

- reports that the frontend has bound a backend-owned prepared client to a real Vim client buffer
- transitions prepared state from `binding` to `ready`
- the backend must not fabricate Vim buffer numbers without this frontend acknowledgment

### `shutdown_client`

```json
{
  "notebook_id": "nb-1",
  "session_id": "sess-1",
  "cell_id": 12,
  "client_id": "client-1",
  "reason": "user_close"
}
```

Behavior:

- tears down client ownership separately from `interrupt_cell`
- if the client is the current prepared client, prepared state becomes `missing`
- if the client is attached to a tracked cell, backend emits client lifecycle updates and clears the client buffer binding without overloading cell `status`
- valid reasons include:
  - `user_close`
  - `cell_deleted`
  - `session_stop`
  - `frontend_unload`
  - `transport_lost`
  - `healthcheck`

### `inspect_client`

```json
{
  "notebook_id": "nb-1",
  "session_id": "sess-1",
  "client_id": "client-1"
}
```

Behavior:

- returns the current backend-owned client view snapshot for inspection
- does not change session, prepared, or cell state
- is intended for backend/runtime introspection while the client runtime vertical slice is still stabilizing
- includes a monotonic `revision` field so polling consumers can detect view changes cheaply

Notes:

- this is not yet a signal for `jusivim` to start consuming backend-rendered client views as part of normal integration
- the returned snapshot is derived from backend-owned client runtime state rather than Vim-local rendering state

### `disconnect_session`

```json
{
  "notebook_id": "nb-1",
  "session_id": "sess-1",
  "reason": "transport_lost"
}
```

Behavior:

- records that the Jusi session lost linkage while the kernel may still be alive
- moves session state to `disconnected` only for attachable sessions
- managed sessions should tear down immediately instead of entering a reconnectable state
- prepared-client state becomes unusable until reconnect
- active executions may remain active but lose trustworthy owner information

### `reconnect_session`

```json
{
  "notebook_id": "nb-1",
  "session_id": "sess-1"
}
```

Behavior:

- attempts to re-establish the notebook-to-session linkage
- transitions through `starting`
- provisions a new prepared client if reconnect succeeds
- does not invent false ownership for executions whose owner cannot be restored

## Responses

Synchronous responses only acknowledge whether the request was accepted.

Success example:

```json
{
  "version": 1,
  "kind": "response",
  "type": "start_session",
  "request_id": "req-123",
  "ok": true,
  "payload": {}
}
```

Failure example:

```json
{
  "version": 1,
  "kind": "response",
  "type": "execute_cell",
  "request_id": "req-456",
  "ok": false,
  "error": {
    "code": "prepared_client_missing",
    "message": "Cannot execute cell without a prepared client"
  }
}
```

Rule:

- request acceptance does not imply completion
- authoritative state changes arrive through events
- inspection-style requests may return synchronous payload data when they do not change backend state

`inspect_client` success example:

```json
{
  "version": 1,
  "kind": "response",
  "type": "inspect_client",
  "request_id": "req-789",
  "ok": true,
  "payload": {
    "client": {
      "title": "cell 12: done",
      "lines": ["started cell 12 [code:python]", "finished: done"],
      "execution_status": "done",
      "active_cell_id": 12
    }
  }
}
```

## Events

### `session_updated`

```json
{
  "notebook_id": "nb-1",
  "session": {
    "id": "sess-1",
    "state": "connected",
    "kernel_name": "python3",
    "connection": "/path/to/kernel.json",
    "attachable": false,
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
    "state": "ready",
    "bufnr": 91
  }
}
```

Notes:

- `bufnr` exists because the current Vim-side model expects a client buffer number
- the backend may emit `bufnr = -1` while the prepared client exists but is not yet bound to a real Vim buffer
- the frontend must complete the bind step before prepared state becomes `ready`

### `cell_updated`

```json
{
  "notebook_id": "nb-1",
  "cell": {
    "id": 12,
    "status": "busy",
    "owner": {
      "kind": "kernel"
    },
    "client_id": "client-1",
    "client_bufnr": 91
  }
}
```

### `backend_error`

```json
{
  "notebook_id": "nb-1",
  "scope": "session",
  "message": "Kernel process exited unexpectedly"
}
```

## State Mapping

The backend should emit states compatible with the current `jusivim` model where practical.

### Session States

- `idle`
- `starting`
- `connected`
- `stopping`
- `stopped`
- `failed`
- `detached`

### Prepared States

- `missing`
- `spawning`
- `binding`
- `ready`

### Cell Statuses

Initial backend target:

- `pending`
- `busy`
- `follow-up`
- `done`
- `error`
- `interrupted`
- `parked`

Notes:

- `follow-up` represents a cell whose kernel execution returned but whose Jusi-specific lifecycle remains active
- cell status is cell-local and must not by itself block execution of other cells
- execution gating is driven by prepared-client readiness, not by whether some previous cell reached `done`

### Client Lifecycle State

Prepared and cell updates may carry `client_state` separately from cell `status`.

Current values:

- `active`
- `shutting_down`
- `shutdown`

Notes:

- `parked` remains reserved for the deliberate keep-output workflow
- shutdown lifecycle must not overload `parked`

### Execution Ownership

Interrupt routing is determined by execution ownership, not by status alone.

Current owner kinds:

- `kernel`: interruption should target the IPython kernel
- `handler`: interruption should target a handler-specific cancellation path
- `unknown`: active work exists but the backend cannot safely resolve the owner

## Compatibility Notes

- preserve MVP behavior where it affects the user workflow
- do not preserve MVP transport field names blindly
- the plugin adapter in `jusivim` is responsible for mapping backend events into `jusi#session#callback_*`
- the frontend owns history-region structure and editing semantics unless a later backend feature explicitly requires that data
- if this contract changes, update this file first and mirror the implementation status in both repos' `.local/current.md`
