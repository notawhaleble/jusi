# Jusi Backend Architecture

## Goal

Jusi is the standalone backend process for notebook execution used by the `jusivim` editor plugin.

## Design Principles

- backend domain logic must not depend on Vim rendering details
- transport is an adapter concern
- state transitions must be explicit and observable
- protocol must stay stable enough for editor/backend coordination
- deployment is conceptually two-component:
  - `jusivim` editor plugin
  - Jusi backend

## Deployment Direction

- the editor plugin starts the backend
- backend is not treated as a separately user-managed daemon in the normal workflow
- backend may run locally or remotely depending on workflow
- remote support does not require a third mandatory user-facing helper component

## Runtime Vocabulary

Use these terms consistently:

- `backend root process`
  - the main `jusi` process started by the editor plugin
  - entrypoint: `python -m jusi`
- `session runtime state`
  - in-memory supervision state held by the backend root process for the session
  - tracks live per-session resources rather than acting as a multi-session registry
- `client runtime`
  - a backend runtime entrypoint used for transcript-host compatibility paths
  - entrypoint: `python -m jusi client-runtime`
  - normal notebook execution uses backend-owned in-process transcript state rather than a dedicated client-runtime child
- `kernel handle`
  - the execution-side resource for the session
  - may be a managed child process, an attached external connection, or another runtime handle

The backend-side single-session structure is not a `registry`.
That word suggests one process indexing many independent session records, which is not the backend shape here.

## Session Model

Backend core keeps explicit session `target` and nothing more in the durable session record:

- `source`
- `alias`
- `kind`
- `value`
- `config`

Interpretation:

- `target` describes what session/kernel environment the backend starts or attaches to
- backend residence is an editor-plugin transport concern
- sessions are durable/reconnectable by default
- one backend root process supervises one durable session record
- that same root process may also supervise multiple child client runtimes for that session
- the editor plugin may keep the real persisted reconnectables list across editor lifetimes
- durable ids are backend-generated with high-entropy `sess-...` values rather than local counters

## Runtime Slice

- `start_session` starts a backend-owned session
- `attach_session` exists as a real backend path, but is intentionally narrow:
  - only `target.kind=connection_file` is executable today
- managed runtime supports that same narrow attach slice against a real external connection file
- managed attached sessions use a small connection-file sidecar registry to coordinate shared disconnect timeout deadlines across peer Jusi root processes
- stop/restart cleanup unregisters only the current Jusi root process from that sidecar; peers are not signaled
- `execute_cell` allocates the real execution client directly for that cell
- `disconnect_session` preserves durable session identity as `disconnected`
- `reconnect_session` restores the durable session linkage without inventing false execution ownership
- backend root process drives editor-link liveness with backend-issued healthchecks and converts missed replies into the normal disconnect/timeout path
- session-level health and teardown decisions remain centralized in the backend root process rather than delegated to clients making independent suicide decisions

## Layering

### Domain

Owns:

- session state
- session target
- cell execution state

### Application

Owns use cases:

- start session
- attach session
- reconnect session
- execute cell
- interrupt execution
- stop session

### Infrastructure

Implements:

- Jupyter/runtime integration
- process supervision
- stdio transport loop
- persistence if introduced later

### Interfaces

Owns:

- protocol parsing
- event encoding
- backend entrypoints

## Plugin Direction

Handler/plugin support is not modeled as “just another runtime” or “just a renderer taxonomy”.

- a plugin has at least:
  - magic command definition
  - display handler
- backend core provides:
  - session/client lifecycle framing
  - plugin discoverability/loading
  - status consistency
  - a structured plugin/editor communication channel
- plugin display handlers and their runtime processes own:
  - plugin-specific interaction logic
  - follow-up/completion semantics
  - mode transitions, for example VisiData-like navigation into shell-like interaction

See [plugins.md](plugins.md) for the plugin contract.

## Handler Activation

Handler-owned execution follows this model:

- every cell still enters through the Jupyter kernel
- handler takeover is driven by a kernel-emitted Jusi handoff mime payload
- backend root process validates that handoff against the registered handler spec
- backend root process can replace the initial `transcript` client runtime with a `handler` client runtime for that cell/client
- that handler runtime owns the live plugin handler for that client lifetime
- normal handler exit maps to cell status `done`
- unexpected handler death maps to cell status `error`
- interrupt for handler-owned cells is a structured handler interrupt first
- follow-up/completion apply only while the handler is alive

Handler startup context is intentionally small:

- `notebook_id`
- `session_id`
- `client_id`
- `cell_id`
- `handler_id`
- explicit `magic_name`
- raw kernel handoff payload and metadata

The backend root process remains the router and supervisor for handler/editor traffic.

## Native Terminal Transport

Interactive terminal-hosted plugins use native terminal attachment rather than notebook-buffer terminal emulation.

- backend root process still owns session and handler lifecycle
- handler-owned clients use native terminal as the default editor plane
- when a handler becomes terminal-backed, backend provisions a dedicated terminal client substrate for that `client_id`
- the editor plugin receives terminal-client metadata from backend and launches a real terminal buffer against that process command
- terminal transport flows through that native terminal job attachment, not through `handler_message terminal_bytes`
- `handler_message` stays available for:
  - follow-up
  - completion
  - plugin commands
  - other control semantics that are not raw terminal traffic

Terminal-backed clients still remain within the normal Jusi client model:

- `session_id`
- `client_id`
- optional `handler_id`

The terminal bridge/client runtime maps back to those ids so stop/disconnect/cleanup stay centralized in backend supervision.

Backend advertises terminal-backed clients explicitly through normal client transport metadata:

- cell-owned active client remains a normal backend client
- backend emits client metadata indicating:
  - transport kind `native_terminal`
  - attach command for the terminal buffer
  - the owning `session_id`
  - the owning `client_id`
  - optional `handler_id`

`inspect_client` remains a debug/recovery seam rather than the hot rendering path for native-terminal clients.

## Runtime Ownership

Backend root owns:

- in-process transcript state for plain kernel cells
- in-process handler control for live handler cells

The only remaining intentional child on the live plugin path is
`plugin-runtime`.
