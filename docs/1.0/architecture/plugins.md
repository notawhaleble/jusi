# Plugin Architecture

## Preserved Product Behavior

The 1.0 design preserves these learned behaviors from 0.x:

- one visible magic family may resolve to multiple exact providers
- the frontend can discover family-level creation/completion capabilities before
  execution
- kernel parsing can produce a validated exact-plugin handoff
- one plugin client may support follow-up, completion, interrupt, generic editor
  actions, or an interactive terminal
- exact-provider presentation metadata may refine family defaults after handoff

The 0.x base-class hierarchy, `handler_message`, prepared clients, status files,
attach commands, and `busy`/`follow-up` cell states are historical evidence, not
1.0 contracts.

## Ownership

Core owns catalog schema validation, family-claim conflict detection, resource
identities, worker supervision, generic operations/actions, failure taxonomy,
and cleanup. Core does not import provider modules or interpret provider payloads.

A plugin family owns shared configuration schema, provider selection, family
syntax/indent hints, and common capability semantics. It does not own kernel
liveness, frontend windows, or terminal selection.

An exact plugin owns its configuration validation, kernel magic adapter, worker
entry point, provider protocol, credentials, and exact regression fixtures. It
cannot widen its failure scope by assertion.

The frontend owns cell parsing, generic action execution, media rendering, and
interactive surfaces. It never chooses an exact provider by parsing a header
when an authoritative handoff is required.

## Lifetimes

```text
notebook runtime generation
  ├─ discovery process (short-lived)
  ├─ validated catalog snapshot
  ├─ kernel generation
  │   └─ loaded versioned magic adapters
  └─ zero or more plugin workers
      └─ one or more explicitly owned clients/executions
```

No discovery process, catalog snapshot, imported kernel extension, worker,
client, or capability snapshot survives full notebook restart.

These resources are target-side and runtime-scoped. For a remote kernel, the
service, discovery process, and workers run remotely with that kernel; no local
Jusi proxy discovers or executes its plugins. A future multi-runtime service
must retain a separate catalog and worker registry per `runtime_id`.

## Discovery Contract

The versioned Python entry-point group is `jusi.plugins.v1`. Each entry-point
name is one exact `plugin_id` and resolves, only inside the discovery process,
to a zero-argument callable returning one JSON-compatible catalog-entry object.
The returned `distribution` and `plugin_version` must exactly match installed
distribution metadata.

Discovery treats `kernel_extensions` and `worker_entry_point` as opaque strings;
it does not import or resolve them. Exact providers may share a family claim
only when magic name, capabilities, and family presentation agree. One magic
cannot identify incompatible families.

Each attempt runs in a fresh interpreter and owned process group. The machine
result uses a private bounded file, so provider stdout cannot corrupt it. The
parent validates the complete catalog again and retains bounded stderr plus
PID/exit/signal diagnostics. Any broken entry, duplicate identity, or family
conflict prevents publication of the entire catalog; stale or partial
capabilities are never presented as authoritative.

The isolated adapter is an application port used by initial start and full
notebook restart. Its validated catalog is owned by the resulting notebook
runtime and is present in authoritative health/start/restart snapshots. Plugin
runtime execution and kernel-extension loading remain deferred.

## Worker Control

The exact worker host and runtime-owned registry are now implemented behind an
internal application boundary. Core allocates `plugin_worker_id`, selects the
entry point from the current catalog, validates the exact family and operation
capability, and starts a fresh child. The provider factory receives an immutable
`WorkerContext`; its object handles one bounded request at a time.

Control uses private duplicated descriptors and length-prefixed JSON. Ordinary
plugin stdin is null and stdout is redirected into bounded stderr diagnostics,
so output noise and ANSI bytes cannot become control frames. Worker exceptions,
timeouts, channel loss, invalid frames, exits, and signals fence that worker.
Isolation contains reliability failures but does not sandbox the user's
filesystem or environment permissions.

There is intentionally no HTTP or Neovim worker command yet. The next slice must
define the versioned kernel-adapter handoff that authoritatively chooses the
exact plugin, family, client, and originating execution before exposing generic
plugin operations. Before requests become externally reachable, supervisor
locking must also be narrowed so worker I/O cannot hold the global authoritative
state lock.

## Kernel Adapter And Handoff

Catalog-declared kernel modules load inside the fresh target kernel after
Jupyter readiness. Each module exposes `jusi_kernel_adapter_v1()` and may expose
the standard IPython `load_ipython_extension(ipython)` hook. One aggregate,
closed attestation must exactly match catalog plugin version and family claims
before the supervisor publishes kernel state `on`.

Execution adapters emit exact-provider control through
`application/vnd.jusi.handoff.v1+json`. Core captures at most one record,
removes it from renderable outputs, and validates it against the current
runtime catalog. Its payload remains opaque and bounded by the Jupyter command
path's 1 MiB control-record limit. Backend execution/client/worker identities
are added only by core.

Supervisor operations are serialized separately from authoritative state
inspection. Worker startup, requests, and cleanup may occupy the operation
lane, but never the short-lived state lock used by health and inspection.

## Failure And Verification

- discovery import/schema failure: internal `plugin_discovery/plugin_error`,
  attributed to the exact entry point and distribution when known; kernel
  remains `off` during restart/start
- worker spawn/death: `plugin_worker/spawn_failed|process_exited|process_signalled`
  with PID/exit/signal/stderr; owning client/execution fails, kernel normally stays `on`
- handoff mismatch: `protocol/invalid_request`, scoped to the execution/client
- kernel extension exception: `execution/execution_error`; a process crash is
  separately `kernel/kernel_died`

Discovery tests cover a fresh process after package changes, one broken
plugin beside one healthy plugin, atomic repair on the next attempt, timeout and
process-group cleanup, bounded stderr, exit/signal distinction, malformed
entries, and provider-family conflicts. Worker tests cover fresh process/import
identity, stdout isolation, request/result correlation, handler failure,
timeout, non-JSON and oversized results, exit/signal distinction, bounded
stderr, catalog-only selection, capability rejection, idempotent cleanup, and
restart teardown fencing. End-to-end exact-provider handoff and two-worker
failure containment remain for the kernel-adapter slice.
