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

## Next Architecture Step

The next real design step is not more target variants. It is exercising the current durable-session model against real frontend behavior:

- validate backend-issued healthchecks against the current `jusivim` transport path
- confirm disconnect/timeout behavior for hard frontend loss
- then decide what external attach capability should widen next beyond `connection_file`
