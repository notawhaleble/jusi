# ADR 0019: Configuration Is A Target-Side Runtime Snapshot

- Status: accepted
- Date: 2026-09-02

## Context

The frontend and service now connect directly to the target that owns kernels
and plugin workers. SQL aliases such as `%%sql main` need family routing and
provider options on that target. In 0.x, Vim loaded `~/.jusi/jusi.toml`, merged
it into a target payload, and sent it to the backend. That makes remote secrets,
restart behavior, and configuration authority depend on the editor.

The accepted full-restart model says configuration is reloaded from canonical
sources, but the 1.0 service currently has no configuration port or snapshot.
Implementing SQL before resolving this would either hard-code a test alias or
reintroduce frontend-owned configuration.

## Decision

- The authoritative service loads configuration on the kernel target. Neovim
  neither reads nor transmits backend/plugin configuration.
- The file should describe only capabilities and routing relevant to that
  target. In particular, a shared remote host should not receive unrelated
  local-machine configuration merely because one Neovim instance knows it.
- Literal credentials are permitted for compatibility but discouraged. Exact
  providers should support resolving secrets from their worker environment or
  another target-side secret facility, so the TOML can contain a reference or
  non-secret connection description instead of the secret value.
- The initial canonical file remains `~/.jusi/jusi.toml` to preserve the user's
  established configuration. `jusi serve --config PATH` provides an explicit
  target-side override for tests and alternate deployments. A missing default
  file means an empty configuration; a missing explicitly requested file is an
  error.
- Every `start_kernel` and `restart_notebook` loads a fresh TOML document before
  plugin discovery or kernel spawn and freezes one private data-only snapshot
  into the new notebook-runtime generation. Service process startup does not
  parse plugin configuration and remains available when a later runtime load
  fails.
- Malformed TOML fails that start/restart as
  `service/start_kernel|restart_notebook/invalid_request`, leaves the kernel
  `off`, and reports the target path and parser location without file contents.
- Core treats the parsed mapping as opaque. It does not interpret `[sql]`,
  provider names, paths, credentials, or plugin option semantics.
- The snapshot is never returned by health, emitted in events, logged, placed in
  process diagnostics, or sent to the frontend.
- Discovery remains installation discovery and does not require valid SQL
  aliases. Plugin-specific configuration must not prevent the service or an
  otherwise usable kernel from starting.
- Kernel adapters receive the frozen snapshot through a private initialization
  call after import and before readiness attestation. A family adapter may use
  its own section to resolve an alias to an exact plugin handoff. It must not
  expose configuration through presentation output.
- The exact handoff/worker path may carry only the selected provider's opaque
  private data needed for that client. Core bounds and routes it without
  interpreting or logging it. The exact worker performs provider validation and
  owns credentials and application errors.
- A full notebook restart creates a new snapshot. Ordinary execute and client
  operations never reread the file, so one runtime cannot silently change
  configuration underneath live workers.

## Consequences

- Local and remote operation use the same ownership: the config file exists
  beside the target-side service and kernel.
- Editing plugin config takes effect through the already-required full restart,
  without restarting Neovim or the long-lived service.
- Existing `[sql.<alias>]` configuration can remain family-owned and compatible
  while its transport and lifetime change.
- Core temporarily holds opaque configuration in memory, so all diagnostics and
  failure details around this path require explicit redaction tests.
- Provider semantic errors are local to the attempted SQL client/execution; a
  malformed canonical TOML document is wider because no coherent runtime
  snapshot exists.
- A later secret-provider integration may replace literal credentials in TOML
  without changing frontend or public protocol ownership.
- A shared host is not made private by this design. Its administrator and
  processes with the same OS authority may be able to inspect kernel/worker
  environments and memory; deployment permissions remain part of target setup.

## Rejected Alternatives

### Keep frontend-owned config delivery

This makes remote service behavior depend on which editor connected, sends
backend secrets through Neovim, and makes full restart less authoritative.

### Let each worker reread files when it starts

Workers in one notebook runtime could observe different configuration versions,
and restart would no longer fence a coherent generation.

### Put plugin configuration in health or catalog events

Catalogs are public capability data. Provider paths and credentials are private
runtime inputs and do not belong in the frontend contract.
