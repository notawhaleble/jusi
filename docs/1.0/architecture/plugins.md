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

## Failure And Verification

- discovery import/schema failure: internal `plugin_discovery/plugin_error`,
  attributed to the exact entry point and distribution when known; kernel
  remains `off` during restart/start
- worker spawn/death: `plugin_worker/spawn_failed|process_exited|process_signalled`
  with PID/exit/signal/stderr; owning client/execution fails, kernel normally stays `on`
- handoff mismatch: `protocol/invalid_request`, scoped to the execution/client
- kernel extension exception: `execution/execution_error`; a process crash is
  separately `kernel/kernel_died`

Discovery tests now cover a fresh process after package changes, one broken
plugin beside one healthy plugin, atomic repair on the next attempt, timeout and
process-group cleanup, bounded stderr, exit/signal distinction, malformed
entries, and provider-family conflicts. Future runtime tests still need worker
death containment, idempotent worker cleanup, handoff validation, and full
restart producing fresh catalog/worker/kernel identities.
