# Jusi Protocol

This document defines the wire contract between Jusi and the `jusivim` editor-side plugin.

The transport implementation may use Vim/Neovim facilities, but the protocol stays transport-agnostic.

## Scope

The backend slice covers:

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

- `notebook_id`: editor plugin notebook runtime id
- `session_id`: backend-generated durable session id
- `cell_id`: editor plugin runtime cell id
- `client_id`: backend-generated cell-owned client id

Notes:

- `sign_id` is not part of the backend contract
- history regions stay editor-local
- buffer numbers may be absent or unbound from backend perspective
- ids are backend-generated high-entropy values with `sess-` prefix

## Client Transport

Backend clients may expose one of these transport kinds:

- `inspection`
  - default inspection transport
  - the editor plugin renders through `client_updated` plus `inspect_client`
- `native_terminal`
  - native-terminal-friendly client advertisement for fullscreen interactive clients
  - the editor plugin should attach a real terminal buffer to the backend-provided attach command/substrate

Native-terminal transport advertises:

- `attach_cmd`
- `attach_env`
- `session_id`
- `client_id`
- optional `handler_id`

## Runtime Mode

Cells and client snapshots may also surface `runtime_mode`:

- `transcript`
  - backend-owned transcript/inspection client runtime
- `handler`
  - live handler-controlled client runtime after accepted plugin takeover

Notes:

- `runtime_mode` is execution state, not transport
- transport answers how the editor should attach/render a client
- runtime mode answers which backend-side client runtime currently owns that cell

## Session Target

Backend session metadata keeps only explicit `target`:

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
- backend residence remains an editor-plugin transport concern, not core backend session state

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

- backend is expected to be launched by the editor plugin
- starts a new durable session for the notebook
- enters `starting`, then `connected`
- backend generates a durable `session_id`
- the editor plugin may omit `target`; backend derives the default target from `kernel_name`

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
- the attach slice is intentionally narrow:
  - `target.kind` must be `connection_file`
- this path is implemented in both the in-memory runtime and the managed runtime
- attached sessions establish the durable backend session; client allocation happens on execute
- managed runtime keeps a small connection-file sidecar registry of attached Jusi root-process PIDs for shared disconnect timeout coordination
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
- the editor plugin may first see the cell become `busy` before a local buffer exists for that client
- once backend emits the real `client_id` on `cell_updated`, the editor plugin can bind or create the local buffer for that client
- terminal `cell_updated` may arrive later as an async event
- every cell enters through the Jupyter kernel
- handler takeover is driven by a kernel-emitted Jusi handoff mime payload
- once that handoff is accepted, backend starts the handler control path for that active handler-owned cell/client

### `interrupt_cell`

```json
{
  "notebook_id": "nb-1",
  "session_id": "sess-1",
  "cell_id": 12
}
```

Behavior:

- routes interrupt by tracked execution owner
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

- symmetric plugin/editor-plugin message path
- request form is editor plugin -> backend
- event form is backend -> editor plugin
- valid only while that client still has an active handler runtime registered

Live handler usage:

- editor plugin -> backend call:
  - `followup`
  - `complete`
- backend -> editor plugin callback:
  - structured action callback:
    - `action_request`

Boundary note:

- if an operation can be completed entirely on the backend/plugin side, it should stay there
- `copy` is not part of the generic frontend callback model
- the editor plugin should not be expected to implement plugin-specific operational logic for such paths

Backend -> editor-plugin built-in action shape:

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

Built-in editor actions:

- `action_type = open_path`
  - `path` is required
  - `open_in` is optional
  - `line` is optional and 1-based
  - `column` is optional and 1-based

- `action_type = open_url`
  - `url` is required
  - `open_in` is optional; `client` opens a frontend-local web buffer in the attached handler window and `tab` opens it in a new tab
  - `open_link` is accepted by the frontend as an alias action type
  - URL field aliases accepted by the frontend are `url`, `href`, and `link`
  - backend must not send or manage `client_bufnr` for the web buffer

- `action_type = yank_text`
  - `text` is required
  - frontend should write it into the main editor register path

- `action_type = edit_path`
  - `request_id` is required
  - `path` is required
  - `line` is optional and 1-based

When frontend finishes an `edit_path` request, it must reply through
`handler_message` with:

- `message_type = action_result`
  - `payload.request_id` is required
  - `payload.ok = true` means accept/apply edited file contents
  - `payload.ok = false` means cancel edit and keep original value/content

Direction note:

- native-terminal transport does not keep raw terminal transport on the notebook control channel
- `handler_message` remains the structured control channel for:
  - follow-up
  - completion
  - plugin commands
- backend root process remains the router for this traffic; the editor plugin does not talk to handlers directly

Generic completion result shape on the handler channel:

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
- when present, the editor plugin should replace exactly that 0-based half-open range in `line_text`
- `end_col` is exclusive
- when absent, the editor plugin may fall back to generic token replacement

Attach note for native-terminal clients:

- native-terminal attach metadata is available as part of normal client state
- terminal attach lifecycle is:
  - execute handler cell
  - observe `client.transport.kind = native_terminal`
  - launch the terminal client from `attach_cmd` + `attach_env`
- `attach_cmd` is target-local attach intent, not always a host-local executable path
- frontend may need to materialize attach through the session target, for example:
  - local backend: execute `attach_cmd` directly
  - `venv` target: execute through the target virtualenv Python
  - `docker` target: execute through `docker exec -i <container> ...`
  - `docker+ssh` target: execute through `ssh ... docker exec -i <container> ...`
- the bundled `jusi_vd` plugin uses this by carrying serialized handoff payload into the generic core `plugin-runtime` entrypoint plus a plugin-owned callable

### Kernel Handoff

Handler activation uses a kernel-emitted Jusi handoff mime payload carrying:

- explicit `handler_id`
- explicit `magic_name`
- raw cell content/startup payload
- handler-specific metadata

That handoff is sufficient for backend to start the correct handler client runtime without further kernel messaging.

Behavior:

- managed runtime recognizes `application/vnd.jusi.handoff+json` in kernel `display_data` / `execute_result`
- backend records that as a structured handoff event in the active client transcript/view
- backend registry validates `magic_name -> handler_id` handoff combinations, including one magic mapping to multiple handlers
- matched handler-owned executions replace the initial transcript runtime with a dedicated `handler` client runtime
- backend root process remains the router/supervisor for editor-plugin <-> handler traffic
- backend uses validated kernel handoff to start that runtime when the handoff is present
- backend no longer starts handler runtimes from header parsing in `ExecuteCell`
- the in-memory runtime synthesizes handoff events for magic cells so tests and the non-managed stub path exercise the same takeover flow

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
- acknowledges the active backend-issued `healthcheck` event
- clears the outstanding editor-liveness check and refreshes backend liveness tracking

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
- session payload exposes `expires_at` as the disconnect deadline
- if that deadline passes, backend performs final session teardown without waiting for a reconnect attempt
- backend may also enter this path after missed editor healthchecks

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
- active cells are normalized as stopped execution state during teardown:
  - `status -> interrupted`
  - `owner.kind -> unknown`
  - `client_state -> shutdown`
  - cleared live runtime identity means `client_id`, `runtime_mode`, and transport metadata are omitted from later `cell_updated` payloads
- for externally attached `connection_file` sessions, managed runtime:
  - sends kernel shutdown through the attached Jupyter client
  - tears down local Jusi channels and clients
  - unregisters only the current Jusi root process from the connection-file sidecar
- peer attached Jusi root processes are not signaled by stop; they must observe kernel loss or their own frontend/session lifecycle
- timeout teardown follows that same current-session cleanup rule for attached `connection_file` sessions

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
- for closed follow-up cells backend normalizes the execution before shutdown:
  - `status -> done`
  - `owner.kind -> unknown`
  - cleared live runtime identity means later `cell_updated` payloads omit `client_id`, `runtime_mode`, and transport metadata

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
- may include `runtime_mode` when backend has tracked execution ownership for that client
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
    "plugin_specs": {
      "sql": {
        "syntax": "sql",
        "indent": "sql",
        "followup": true,
        "completion": true
      },
      "shell": {
        "syntax": "sh",
        "indent": "sh",
        "followup": true,
        "completion": true
      }
    },
    "palette": {
      "vd": {
        "entries": []
      },
      "sql": {
        "entries": ["analyticsdb", "mysqlitedb"]
      }
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
    "runtime_mode": "transcript",
    "client_state": "active",
    "presentation": {
      "syntax": "pgsql",
      "indent": "sql"
    }
  }
}
```

Notes:

- `client_id` appears when backend has allocated the real execution client
- `runtime_mode` is optional and identifies the backend-side runtime currently owning that cell
- when backend clears live runtime identity, `client_id`, `runtime_mode`, and transport metadata are omitted rather than emitted as empty strings
- `client_bufnr` is frontend-local editor state and is omitted unless backend has a real bound buffer number
- native-terminal transport metadata belongs to that real execution client, not to any session-level prepared slot
- `owner` is independent from `status`
- `presentation` is optional and only appears when backend has authoritative editor presentation metadata for this cell
- session-level `plugin_specs` are broad pre-execution defaults keyed by magic name
- provider-family plugins should keep `plugin_specs` provider-neutral and use cell-level `presentation` for concrete provider dialects
- session-level `palette` is optional and exposes the editor-facing creation palette keyed by magic name
- installed plugins without named config entries should still appear in `palette` with an empty `entries` list

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
- the editor plugin should respond by calling `inspect_client` for the same client if it needs the updated snapshot
- this is the backend-driven redraw signal for client rendering; `inspect_client` remains the content source
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

- backend -> editor-plugin side of the structured handler channel
- used for plugin/display-handler control messages
- native-terminal transport keeps `handler_message` for control semantics, not as the live fullscreen terminal transport
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
- the editor plugin should reply with `healthcheck_reply`
- if replies stop long enough, backend marks the session `disconnected` and starts the existing disconnect-timeout flow
- known issue: if Vim is suspended, for example with `Ctrl-Z`, backend may treat the missing reply as link loss

## Current Limitations

- `start_session` still routes actual start behavior mainly through `kernel_name`
- `attach_session` is only real for `target.kind=connection_file`
- durable session metadata is in-memory per backend root process; cross-process persistence is future work
- transcript-style client redraw remains invalidation-plus-pull:
  - backend emits `client_updated`
  - the editor plugin still pulls the full snapshot through `inspect_client`
- native-terminal transport is preferred for fullscreen interactive handlers:
  - backend should advertise terminal-backed clients explicitly
  - the editor plugin should attach a real terminal buffer to the backend-provided client substrate
  - `handler_message` should remain for control semantics rather than carrying fullscreen terminal transport bytes
