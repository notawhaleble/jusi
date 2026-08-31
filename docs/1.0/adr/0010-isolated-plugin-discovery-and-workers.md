# ADR 0010: Plugin Discovery And Runtime Code Are Process-Isolated

- Status: accepted
- Date: 2026-08-31

## Context

The 0.x plugin system learned useful concepts—magic families, exact handler
identity, kernel handoff, follow-up/completion capabilities, and optional
terminal runtimes—but accumulated imported handler objects, client-runtime
controllers, status files, attach commands, and terminal transport metadata in
the core backend.

Plugin installation and code edits are a primary reason for full notebook
restart. Loading entry points in the long-lived service would preserve Python
module caches and allow discovery/import failures to damage the supervisor.

## Decision

The long-lived 1.0 service does not import third-party plugin packages.

- Plugin discovery runs in a fresh short-lived process and returns a validated,
  data-only catalog. Discovery uses a new versioned entry-point group rather
  than loading 0.x `jusi.display_handlers` implementations as if compatible.
- Catalog publication is atomic: one broken or conflicting provider fails the
  attempt with exact attribution instead of publishing a partial capability
  snapshot.
- Catalog entries declare exact plugin identity/version, family/magic claims,
  kernel extension module names, worker launch reference, supported operations,
  interaction requirements, and media capabilities. They contain no Python
  callable objects or transport commands.
- Active plugin behavior runs in a supervised worker process for the exact
  plugin/client where practical. The worker has its own identity, stderr,
  operation trace, and cleanup result. Its death normally affects only that
  worker/client/execution.
- Kernel-side extension code is limited to magic parsing and a versioned handoff
  into core/plugin-worker control. Provider work that does not require kernel
  memory runs outside the kernel.
- Follow-up, completion, interrupt, and generic editor actions are declared
  client capabilities/operations. They are not durable cell or kernel states.
- Text/ANSI and rich output still select renderers by media type. Plugin identity
  never selects terminal presentation by itself.

The exact catalog, handoff, and worker-control schemas will land atomically with
fixtures and Python/Lua conformance tests before plugin execution is enabled.

## Consequences

- A broken plugin import cannot crash or contaminate the supervisor process.
- Full notebook restart discards the catalog, discovery process, kernel
  extensions, and all workers, then discovers/imports again from canonical
  configuration as required by ADR 0005.
- The initial entry-point group will be `jusi.plugins.v1`; 0.x plugins require an
  explicit compatibility adapter or migration and are never loaded silently.
- Worker startup has more process overhead, accepted in exchange for failure
  containment and actionable diagnostics.
- Interactive terminal workers may later advertise a dedicated stream/PTY
  capability, but they do not alter HTTP/SSE kernel control.
