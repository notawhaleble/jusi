# Jusi Backend Architecture

## Goal

Jusi is the standalone backend process for notebook execution used by Jusivim.

It should preserve the useful MVP behavior while moving to explicit contracts, explicit state transitions, and clearer runtime boundaries.

## Design Principles

- backend domain logic must not depend on Vim rendering details
- transport is an adapter concern
- state transitions must be explicit and observable
- protocol must stay stable enough for cross-repo coordination
- deployment should remain conceptually two-component:
  - Vim plugin
  - Jusi backend

## Deployment Direction

- `jusivim` starts the backend
- backend is not treated as a separately user-managed daemon in the normal workflow
- backend may run locally or remotely depending on workflow
- remote support should not introduce a third mandatory user-facing helper component

## Runtime Vocabulary

Use these terms consistently:

- `backend root process`
  - the main `jusi` process started by `jusivim`
  - current entrypoint: `python -m jusi`
- `session runtime state`
  - in-memory supervision state held by the backend root process for the current session
  - tracks live per-session resources rather than acting as a multi-session registry
- `client process`
  - a backend-owned child process used for prepared/active client runtime behavior
  - current entrypoint: `python -m jusi client-process`
- `kernel handle`
  - the execution-side resource for the session
  - may be a managed child process, an attached external connection, or another runtime handle

Current architecture should not call the backend-side single-session structure a `registry`.
That word suggests one process indexing many independent session records, which is not the current shape.

## Current Session Model

Backend core currently keeps explicit session `target` and nothing more in the durable session record:

- `source`
- `alias`
- `kind`
- `value`
- `config`

Current interpretation:

- `target` describes what session/kernel environment the backend should start or attach to
- backend residence/endpoint is a frontend transport concern for now
- sessions are durable/reconnectable by default
- one backend root process supervises one current durable session record
- that same root process may also supervise multiple child client processes for that session
- frontend may keep the real persisted reconnectables list across Vim lifetimes
- durable ids are backend-generated with high-entropy `sess-...` values rather than local counters

## Current Honest Runtime Slice

- `start_session` starts a backend-owned session and provisions a prepared client
- `attach_session` exists as a real backend path, but is intentionally narrow:
  - only `target.kind=connection_file` is executable today
- managed runtime now supports that same narrow attach slice against a real external connection file
- managed attached sessions now also use a small connection-file sidecar registry to coordinate stop fanout across peer Jusi root processes
- that same sidecar now carries the shared disconnect timeout deadline for attached peers
- `execute_cell` consumes the session prepared client and prepares the next one
- `disconnect_session` preserves durable session identity as `disconnected`
- `reconnect_session` reprovisions prepared state without inventing false execution ownership
- backend root process now also drives frontend-link liveness with backend-issued healthchecks and converts missed replies into the normal disconnect/timeout path
- session-level health and teardown decisions remain centralized in the backend root process rather than delegated to clients making independent suicide decisions

## Layering

### Domain

Owns:

- session state
- session target
- prepared client
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

Handler/plugin support should not be modeled as “just another runtime” or “just a renderer taxonomy”.

Current direction:

- a plugin has at least:
  - magic command definition
  - display handler
- backend core provides:
  - session/client lifecycle framing
  - plugin discoverability/loading
  - status consistency
  - a structured plugin/frontend communication channel
- plugin display handlers own:
  - plugin-specific interaction logic
  - follow-up/completion semantics
  - mode transitions, for example VisiData-like navigation into shell-like interaction

This is the level where MVP-style flexibility such as `%%sql`, `%%vd`, and `%%oc` generalizes best.

See [plugins.md](/Users/niku/Documents/dev/jusi/docs/plugins.md) for the working draft.

## Native Terminal Pivot

The first PTY-backed handler slice was useful to prove:

- plugin/frontend control messages
- live interactive child-process ownership
- handler bootstrap/follow-up ideas

It was not a good final UX for fullscreen interactive tools when rendered through:

- PTY bytes over the backend control channel
- frontend terminal parsing in pure Vimscript
- projection into a normal notebook buffer

So the current architecture direction is:

- keep notebook/session/handler ownership in backend core
- keep structured `handler_message` for control semantics
- stop treating PTY byte transport over the notebook control channel as the long-term terminal surface
- pivot interactive terminal-hosted clients toward native editor terminal buffers

### Concrete Backend Proposal

The preferred substrate is a bridge/client-process model built on the existing `jusi client-process` split.

Current proposal:

- backend root process still owns session and handler lifecycle
- when a handler becomes terminal-backed, backend provisions a dedicated terminal client process for that `client_id`
- frontend receives terminal-client metadata from backend and launches a real editor terminal buffer against that process command
- terminal transport flows through that native terminal job attachment, not through `handler_message terminal_bytes`
- `handler_message` stays available for:
  - bootstrap
  - follow-up
  - completion
  - plugin commands
  - other control semantics that are not raw terminal traffic

### Terminal Client Identity

When backend exposes a terminal-backed client, the identity should still remain within the normal Jusi client model:

- `session_id`
- `client_id`
- optional `handler_id`

The terminal bridge/client process must map back to those ids so stop/disconnect/cleanup stay centralized in backend supervision.

### Advertising A Terminal-Backed Client

Backend should advertise terminal-backed readiness explicitly instead of expecting frontend to infer it from `terminal_bytes`.

The likely contract shape is:

- prepared/active client remains a normal backend client
- backend emits client metadata indicating:
  - transport kind `native_terminal`
  - attach command for the terminal buffer
  - the owning `session_id`
  - the owning `client_id`
  - optional `handler_id`

`inspect_client` can remain a debug/recovery seam, but should no longer be the hot rendering path for native-terminal clients.

## Next Architecture Step

Define the terminal-backed client contract concretely:

- how native-terminal capability is advertised
- what attach command frontend should run
- how the bridge/client process maps back to `session_id`, `client_id`, and optional `handler_id`
- how stop/disconnect/backend-close tear that bridge down consistently
