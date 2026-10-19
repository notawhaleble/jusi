# ADR 0051: Explicit Plugin Client Bootstrap

- Status: accepted
- Date: 2026-10-18

## Decision

Plugin families may advertise `bootstrap` alongside `execute`. This promises
initialization without submitting application content. All exact providers
claiming that family must agree, as for other family capabilities.

`:JusiBootstrap` initializes the current cell using only its first-line magic
and arguments. It preserves the draft, cursor, focus and history. Executing a
plugin cell with an empty or whitespace-only body uses bootstrap when supported.
An already bound cell's normal contextual submission remains a followup; it does
not acquire new empty-followup semantics. `JusiExecute` retains explicit
replacement semantics for a cell that already owns a client; use
`JusiBootstrap` to ensure a client exists without replacing it. Ordinary typing and completion never
initialize resources (ADR 0027).

The existing execute HTTP command accepts optional boolean `bootstrap`. The
frontend sends only the header with that flag. The supervisor also recognizes
empty execution of a bootstrap-capable magic from its authoritative catalog,
covering HTTP callers and startup catalog-delivery races. Explicit bootstrap
ignores any body supplied by a non-frontend caller. Unsupported explicit requests
fail with `unsupported` before kernel execution.

Bootstrap reuses the serialized execution and exact-provider handoff lifecycle,
including execution events, interruption, bounded initialization, worker/surface
creation and runtime cleanup. It adds no durable kernel or client state, worker
operation, or separate resource identity. A new initialization returns the usual
execution response. A repeat returns a successful operation and the existing
`client`, without creating another execution or submitting anything to it.

The supervisor privately retains each client's originating `(magic, arguments)`.
Repeat initialization reuses only one live, non-closing client with an identical
magic and literal argument string. Changed arguments, a changed
magic, or ambiguous clients fail with `conflict`; the user must close the previous
client explicitly. Concurrent initializations serialize and cannot create two
clients for the same matching request. The frontend rejects a duplicate while
its first initialization request is pending.

## Kernel and provider contract

A registered IPython cell-magic callable exposes a callable attribute
`__jusi_bootstrap_v1__`. It accepts one string containing literal magic arguments (without IPython
variable expansion) and
emits the normal exact-provider handoff. The target-side Jupyter adapter invokes
it through `jusi.kernel_bootstrap.bootstrap(magic_name, arguments)`, using Python
string literals. This avoids IPython's empty-cell-magic rejection without a dummy
body, query, shell command or agent prompt. Missing hooks and missing/wrong
handoffs fail the initialization while leaving a live kernel on.

The hook owns alias/configuration resolution and warm-up semantics. Its private
handoff payload reaches the ordinary worker `execute` handler, which creates the
same durable client as normal execution. The provider must not treat this as a
content submission. ACP implements this with an empty initial body: connection,
authentication and session establishment proceed, but no agent prompt is sent.

Creation does not promise that all asynchronously supplied metadata has arrived.
Completion uses whatever the provider can currently supply; provider-specific
authentication requirements and command updates remain provider-owned. Repeating
bootstrap does not restart a session, retry authentication or wait for metadata.

## Evidence

- `tests/backend/unit/test_bootstrap.py`: body isolation, concurrent repeats,
  ordinary-client reuse, header mismatch, unsupported requests, close/recreate
  and missing-handoff containment.
- `tests/backend/integration/test_kernel_bootstrap.py`: real IPython empty-body
  rejection, explicit hook invocation, literal argument transport and live-kernel
  survival after a missing hook.
- Shared bootstrap command/catalog fixtures consumed by Python and Lua.
- `tests/frontend/bootstrap_spec.lua`: draft preservation, pending rejection,
  empty-body routing and history exclusion.
- `tests/e2e/bootstrap_spec.lua`: real HTTP/kernel/worker/terminal startup,
  completion before a first submission, idempotence and populated-cell bootstrap.

- Sibling `jusi-acp/tests/fixtures/bootstrap.lua`: real ACP application path
  completion before the first agent prompt, preserved draft and idempotent reuse.
  The broader session-features test has a separately recorded native crash
  ([Incident 0029](../incidents/0029-acp-session-features-native-neovim-crash.md)).

## Shell and SQL adoption

`jusi-shell` uses its normal shell/options resolver with an empty body. It starts
the interactive shell and control channel without submitting a command.

`jusi-sql` marks private initialization payloads with `bootstrap: true` and an
empty `sql`. PostgreSQL and ClickHouse carry that flag through worker staging,
start their normal metadata loading, and display a ready sheet rather than a
query result. Explicit bootstrap bypasses the providers' legacy dummy-query
fallbacks. Metadata loading is asynchronous and retains provider configuration,
including PostgreSQL's option to disable it. These are plugin-owned payloads;
core does not interpret their fields.
