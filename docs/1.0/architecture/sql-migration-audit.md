# SQL/VisiData Migration Audit

This audit is based on the clean sibling repositories `jusi-sql` and
`jusi-sqlite`, the archived 0.x backend, and `jusivim` behavior. Those
repositories remain unchanged.

## Preserve

- `%%sql <alias>` is a family-level command. The alias resolves to one exact
  installed provider from target-side configuration; the frontend never picks
  `sqlite`, `postgres`, or another provider.
- The shared SQL family and exact providers remain independently installable
  Python distributions. They are not folded into core Jusi merely to make the
  first test pass.
- A successful SQL handoff creates one durable backend client and native
  terminal surface. The worker owns its database state, query sequence,
  transactions, datasets, and VisiData behavior until explicit close, fatal
  worker/client failure, or notebook-runtime teardown.
- Recoverable database/application errors are rendered by the healthy SQL
  application, including inside a VisiData sheet when appropriate. Fatal
  factory, worker, or terminal loss uses the core failure envelope.
- SQL family syntax/indent hints, alias discovery, follow-up, completion, and
  provider-specific refinement remain useful behavior. Only initial execute is
  required for the first slice.
- Terminal geometry is supplied by the generic terminal surface. VisiData
  remains the terminal renderer for datasets; Neovim does not render SQL rows.
- Full notebook restart reloads target configuration, rediscovers providers,
  imports adapters in a fresh kernel, and starts no stale SQL worker.

## Retire

- 0.x handler base classes, handler snapshots, `follow-up` as a cell status,
  prepared clients, attach/status files, and plugin-specific frontend messages.
- Frontend loading and transmitting `~/.jusi/jusi.toml` or VisiData config.
- `JUSI_PLUGIN_RUNTIME_CALLABLE`, JSON payloads in environment variables, and
  the core-owned `plugin-runtime` bootstrap.
- Core monkey-patching VisiData or interpreting SQL queries, rows, completions,
  connection state, or provider errors.
- Pickle/base64 handoffs used by the bundled 0.x `%%vd` path.
- Any claim that a provider or application error changes kernel truth.

## Current 1.0 Gaps

- The service has no target-side configuration loader or runtime snapshot. The
  accepted restart documents say configuration reloads, but there is currently
  nothing to reload.
- `jusi-sql` and `jusi-sqlite` implement the incompatible 0.x entry-point,
  handler, kernel MIME, runtime bootstrap, and environment contracts.
- The core environment intentionally has neither VisiData nor these provider
  packages installed. Core should not gain mandatory SQL dependencies.
- Core supports exact execute/handoff/client/terminal creation, but not generic
  follow-up, completion, or interrupt HTTP operations yet.
- The exact lifetime of VisiData startup configuration and its safe snapshot
  transport still needs a decision after the initial query path.

## Proposed Migration Sequence

1. Accept or revise ADR 0019's target-side runtime configuration snapshot.
2. Add a central 1.0 SQL/SQLite development fixture that uses the real generic
   discovery, kernel adapter, exact worker, SQLite database, target PTY, and
   VisiData process. It must not masquerade as an installed production plugin.
3. Prove `%%sql main` selects SQLite from a temporary target config, renders a
   read-only `select 1 as value` dataset in VisiData, closes only its client,
   and leaves the kernel on.
4. Prove invalid SQL and provider failure remain scoped correctly; prove full
   restart reloads changed alias/provider configuration.
5. Migrate the separate `jusi-sql` family package and `jusi-sqlite` exact
   provider to the proven contract in their own repositories only after that
   boundary is accepted. Do not copy their 0.x implementation into core.
6. Add follow-up, completion, interrupt, VisiData editor actions, and additional
   providers as later contract slices.

The first slice is deliberately read-only. Transaction policy and mutation
confirmation belong to the SQL family/provider design, not the core walking
skeleton.

For shared remote targets, the runtime TOML should contain only that target's
capability/routing configuration. Provider secrets should normally be resolved
from the target worker environment or a provider-owned secret facility rather
than copied from the user's local machine into TOML.
