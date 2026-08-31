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

## Failure And Verification

- discovery import/schema failure: `plugin_worker/plugin_error`, scoped to the
  exact catalog entry when known; kernel remains `off` during restart/start
- worker spawn/death: `plugin_worker/spawn_failed|process_exited|process_signalled`
  with PID/exit/signal/stderr; owning client/execution fails, kernel normally stays `on`
- handoff mismatch: `protocol/invalid_request`, scoped to the execution/client
- kernel extension exception: `execution/execution_error`; a process crash is
  separately `kernel/kernel_died`

Required future tests cover discovery in a clean process after package changes,
one broken plugin beside one healthy plugin, worker death containment, bounded
stderr, idempotent cleanup, provider-family conflicts, handoff validation, and
full restart producing fresh catalog/worker/kernel identities.
