# ADR 0009: Local Service Launch Is Explicit And Notebook-Scoped

- Status: accepted
- Date: 2026-08-31

## Context

The walking skeleton can connect to an already running service, but the product
will need a convenient local launch path. Hiding process creation inside
`:JusiConnect` would merge service process ownership with transport connection
and recreate ambiguous start/stop behavior. A shared local service would also
require a multi-notebook ownership protocol that the current one-kernel
supervisor does not have.

## Decision

The initial managed-local topology is one explicitly launched service/supervisor
per notebook runtime. Service launch, transport connect, kernel start, transport
disconnect, kernel stop, and service stop are distinct operations.

- `:JusiConnect` only inspects and connects to a supplied/configured URL. It
  never spawns a process.
- `:JusiServiceStart` launches the configured executable on
  an ephemeral loopback port, capture readiness JSON and bounded stderr, and
  record a frontend-owned service-process identity separately from
  `supervisor_id` and `transport_id`.
- An explicitly configured remote/external URL is never treated as a
  frontend-owned process and is never terminated by local cleanup.
- Disconnecting transport does not stop an owned service or kernel.
- Service stop must report kernel/resource cleanup results before terminating
  the owned process. It cannot silently equate a killed HTTP process with a
  successfully stopped kernel.
- Automatic convenience may invoke the explicit launch operation later, but the
  operation and failures remain visible.

## Consequences

- The current single-kernel supervisor is an honest notebook-local boundary
  rather than an accidentally global service that rejects a second notebook.
- Notebook-local services cost more processes but improve failure and plugin
  isolation. Evidence may justify a multi-notebook supervisor in a later ADR.
- Executable configuration, readiness timeout, stderr retention, buffer-owned
  orphan prevention, and shutdown behavior are verified together by the
  headless-Neovim end-to-end suite.
- Full notebook restart may retain the control-plane service while replacing its
  kernel, workers, discovery state, and frontend runtime, provided fresh process
  boundaries prevent stale plugin imports as required by ADR 0005.
