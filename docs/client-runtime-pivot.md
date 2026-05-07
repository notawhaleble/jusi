# Runtime Model

This document describes the internal runtime model used by Jusi.

Jusi keeps the editor/backend protocol stable while separating three concerns:

- backend-owned session and kernel supervision
- backend-owned client and handler control
- plugin-owned interactive runtime processes when a handler needs a real terminal app

## Runtime Shape

- `backend root process`
  - owns protocol parsing
  - owns durable session state
  - owns kernel supervision
  - owns per-client supervision
  - routes editor-facing events and requests
  - keeps transcript state for plain kernel cells in process
  - keeps handler control for live handler cells in process

- `plugin-runtime`
  - spawned only for interactive plugin hosting
  - owns the live terminal app context when a plugin needs one

There is no separate long-lived `handler-worker` role.

## Stable Public Contract

The runtime layout is internal. The public editor/backend contract remains:

- session lifecycle requests and events
- `execute_cell`
- `inspect_client`
- `shutdown_client`
- `handler_message`
- native-terminal transport advertisement
- `plugin_specs`
- `palette`
- `cell.presentation`

The `jusivim` plugin should not need a protocol redesign to consume runtime-layout changes.

## Ownership Boundaries

### Backend Root Owns

- session lifecycle
- kernel lifecycle
- durable ids
- client allocation and teardown
- liveness and healthcheck policy
- transcript accumulation for plain kernel cells
- handler startup from validated handoff payload
- follow-up, completion, and interrupt routing
- editor action routing

### Plugin Runtime Owns

- live interactive process state
- shell state
- VisiData session state
- plugin-local terminal interaction

### Plugin Package Owns

- magic declaration
- handoff emission from kernel side
- plugin-specific logic implemented against the handler/plugin API

Plugin packages do not own process-topology decisions.

## Native Terminal Model

Native terminal transport is the editor-facing plane for fullscreen interactive clients.

- the editor receives `transport.kind = native_terminal`
- the editor receives `attach_cmd` and `attach_env`
- terminal attach enters `python -m jusi client-process terminal-attach`
- terminal attach `exec`s into `python -m jusi plugin-runtime`

This keeps fullscreen interaction off the notebook control channel while preserving backend supervision.

## Plain Kernel Cells

Plain Python/IPython cells stay kernel-owned.

- backend sends code to the kernel
- backend observes the kernel output stream directly
- backend keeps structured execution state for lifecycle, reconnect, and inspection
- transcript-style client rendering stays backend-owned invalidation plus `inspect_client`

Terminal-style presentation is a client surface choice, not the execution authority.

## Plugin API Direction

Plugin handlers are written against one backend-facing interaction model:

- handler setup
- follow-up
- completion
- interrupt
- snapshot hooks
- optional native-terminal bootstrap hooks

Plugin authors should not need to care whether control lives in-process in backend root or inside a plugin runtime.
