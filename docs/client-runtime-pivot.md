# Single Client Runtime Pivot

This document defines the next major internal refactor for Jusi.

The original goal was to collapse the old three-role live client stack:

- `client-process`
- `handler-worker`
- `plugin-runtime`

while keeping the editor/backend wire protocol stable.

The implemented result is narrower and cleaner:

- transcript state moved in-process into backend root
- handler control moved in-process into backend root
- `plugin-runtime` remains the only intentional child process for interactive
  app hosting

## Why This Pivot

The current plugin path works, but it is heavier than it should be:

- one interactive client may involve multiple child processes
- common behavior for terminal-hosted plugins is split across different roles
- unsolicited plugin callbacks use an extra mailbox path because the live runtime
  is not the same process as the supervised client runtime
- plugin authors are exposed to internal seams that are likely to move again

That is acceptable as an intermediate implementation, but it is not a release
shape we should optimize around for third-party plugin authors.

## Target Shape

After the implemented pivot, the runtime model is:

- `backend root process`
  - owns protocol parsing
  - owns durable session state
  - owns kernel supervision
  - owns per-client supervision
  - routes editor-facing events and requests

- `plugin-runtime`
  - spawned only for interactive plugin hosting
  - owns the live terminal app context when a plugin needs one

There is no longer a separate long-lived `handler-worker` role. `plugin-runtime`
remains a deliberate boundary.

## What Must Stay Stable

This pivot is an internal architecture change. It should not redefine the public
editor/backend contract.

The following remain stable:

- session lifecycle requests and events
- `execute_cell`
- `inspect_client`
- `shutdown_client`
- `handler_message`
- native-terminal transport advertisement
- `plugin_specs`
- `palette`
- `cell.presentation`

The editor plugin should not need a protocol redesign to consume this refactor.

## Client Runtime Responsibilities

Each client runtime process should own all client-local behavior that today is
spread across multiple subprocess roles.

### Always

- current client transcript/view state
- client status transitions requested by backend root
- control-channel handshake with backend root
- clean shutdown on root request

### For kernel-transcript clients

- transcript accumulation
- stdout/stderr/output snapshot state
- invalidation signals for `inspect_client`

### For handler-owned clients

- handler startup from validated handoff payload
- follow-up handling
- completion handling
- interrupt handling
- plugin-specific snapshot state

### For terminal-hosted interactive clients

- native-terminal attach bootstrap
- terminal-local runtime ownership
- plugin-originated editor callbacks such as `open_path`

## Boundaries

### Backend Root Owns

- session lifecycle
- kernel lifecycle
- durable ids
- client allocation and teardown
- liveness/healthcheck policy
- routing between editor requests and client runtime processes
- routing between kernel events and client runtime processes when needed

### Client Runtime Owns

- client-local volatile state
- handler/plugin execution state
- interactive runtime state
- client-scoped helper facilities

### Plugin Package Owns

- magic declaration
- handoff emission from kernel side
- plugin-specific logic implemented against the client-runtime plugin API

Plugin packages should not own process topology decisions.

## Internal Communication Model

After the pivot, backend root should talk to one child runtime per client.

That internal protocol should be enough for:

- startup handshake
- client snapshot publication
- status updates
- handler follow-up/completion requests
- interrupt requests
- editor action callbacks
- clean exit reporting

The current plugin-runtime mailbox path should be treated as transitional and
removed once the client runtime can emit unsolicited callbacks directly to root.

## Native Terminal Model After The Pivot

Native terminal transport remains the right editor-facing plane for fullscreen
interactive clients.

What changes is only the ownership model behind it:

- today:
  - attach command enters `client-process terminal-attach`
  - terminal attach eventually `exec`s a separate plugin runtime
- target:
  - attach command enters the client runtime directly, or a very thin attach
    shim that stays part of the same client runtime role

The editor plugin should continue to receive:

- `transport.kind = native_terminal`
- `attach_cmd`
- `attach_env`

No protocol change is required there.

## Plain Kernel Cell Direction

The pivot should also stop treating plain kernel cells as a separate
transcript-first execution class.

Target direction:

- plain Python/IPython cells become client-runtime-backed clients too
- backend still sends code to the kernel and still observes the full kernel
  output stream
- the live presentation surface is the client runtime rather than a notebook
  buffer renderer
- backend retains enough structured execution state for lifecycle, reconnect,
  and debugging

This means the post-pivot model is not:

- plugin cells as live clients
- plain kernel cells as a different transcript transport class

It is instead:

- all cell executions are client-runtime-backed
- backend owns execution identity and kernel observability
- client runtime owns the live presentation surface

This removes a transport split that would otherwise become another short-lived
architectural seam.

### What Backend Still Owns For Plain Kernel Cells

Even when the primary presentation surface is a live client, backend should
still retain kernel-observed execution information such as:

- stream output structure
- execute-result/error boundaries
- final execution status
- enough snapshot/debug state for reconnect and inspection

So this is not a return to the old PTY-rendering idea.

The important distinction is:

- backend still consumes the kernel protocol directly
- backend does not delegate execution observability to a terminal emulator
- terminal-style presentation is only the client surface, not the execution
  authority

## Execution Plan Implication

The implementation plan for the pivot should assume one client-runtime model for
both plugin and non-plugin cells.

That means:

- do not introduce a new long-term transcript/terminal mode split for plain
  kernel cells
- when Phase 2 defines the internal client-runtime protocol, it must be able to
  represent:
  - plain kernel execution events
  - handler-owned interactive execution
  - editor action callbacks
- when Phase 3 folds handler control into client runtime, the same runtime model
  should already be suitable for future plain kernel client ownership too
- plugin-runtime collapse is no longer part of the target plan

## Plugin API Direction

The plugin API should move toward one runtime-facing abstraction:

- handler setup
- follow-up
- completion
- interrupt
- snapshot hooks
- optional native-terminal bootstrap hooks

That API should not require plugin authors to care whether their code runs in a
worker process or in a second runtime process. The answer after this pivot is
simply: it runs inside the client runtime.

## Migration Plan

### Phase 1: Freeze Public Contract

- keep current wire protocol stable
- stop adding new plugin features that depend on the current split runtime model
- remove half-implemented runtime-common features that would force a short-lived
  plugin shape

This phase is complete once the worktree is committed in its cleaned-up state.

### Phase 2: Introduce A Client Runtime Interface

- define one internal client-runtime protocol between backend root and child
  runtimes
- make current client-process behavior speak that protocol explicitly
- move handler-child responsibilities behind that same interface

This is the key design phase. The goal is to replace role-specific process
contracts with one client-runtime contract before removing any subprocess type.

### Phase 3: Fold Handler Worker Into Client Runtime

- start handler-owned clients as client runtimes directly
- move follow-up/completion/interrupt handling into that process
- keep backend root routing unchanged from the editor perspective

At the end of this phase, the separate handler child runtime should disappear as
a long-lived role.

This phase is complete.

### Phase 4: Reintroduce Shared Interactive Facilities

Only after the new runtime model exists should we add shared facilities such as:

- common VisiData configuration loading
- common VisiData copy behavior
- blocking external-editor integration for VisiData-based plugins

Those facilities then land on a stable runtime API instead of the current
transitional process split.

## Non-Goals For This Pivot

- redesigning the editor/backend protocol
- redesigning plugin presentation metadata
- redesigning palette metadata
- reintroducing prepared clients
- solving every remote-target workflow in the same change

## Success Criteria

We should treat the pivot as complete only when all of the following are true:

- one active client corresponds to one backend-owned runtime process
- plugin authors do not need to care about in-process handler control versus
  `plugin-runtime`
- unsolicited plugin callbacks no longer need the action mailbox file
- native-terminal plugins still work through the same editor-facing transport
  metadata
- transcript clients and interactive plugin clients both fit the same client
  supervision model
