---
name: jusi-plugin-development
description: Build, review, or migrate exact-provider notebook plugins for Jusi 1.0, including kernel handoffs, durable workers, terminal clients, followups, completions, interruption, and copy/open. Applies to Jusi runtime plugins, not Codex plugin bundles.
---

# Jusi 1.0 exact plugin development

Read [references/source.md](references/source.md) first. It identifies the
version-matched Jusi reference checkout installed with this skill. All core paths
below are relative to that checkout. Treat it as read-only reference material;
implement the requested plugin in the user's project. Do not change core or
other repositories unless the user includes that work in scope.

Read the relevant 1.0 contracts and examples as needed. Core `AGENTS.md` rules
apply when editing core, not as a requirement to modify core status files or run
its entire suite for an external plugin. Legacy code is behavior evidence, not
the 1.0 API. Keep the user's plugin/family packaging choice. ADRs live in `docs/1.0/adr/`.

## Establish the behavior

Identify the initial input, continuing client/session state, presentation, and
which operations the user actually needs. Inspect corresponding legacy use
cases and tests when migrating. Separate recoverable application errors from
loss of the worker/session. Do not add plugin-specific branches to Lua to make
an operation work.

Use these live references when their concern applies:

- `protocol/schema/v1/plugin-catalog.schema.json` and `plugin-kernel.schema.json`:
  discovery and exact handoff.
- `src/jusi/plugin_api.py`, `src/jusi/infrastructure/plugin_worker_child.py`, and
  ADR 0034: worker entry points, ordinary-thread affinity, interrupt and errors.
- `tests/fixtures/terminal_plugin/jusi_terminal_fixture.py`: a small executable
  fixture for the complete lifecycle. Its journal is fixture-owned application
  IPC, not a required production transport.
- `tests/fixtures/sqlite_plugin/`: separate kernel, worker, and terminal-app
  imports; useful process-boundary evidence, not a full production SQL design.
- `docs/1.0/architecture/completions.md` and ADR 0035: completion ranges and
  content-based editor actions.

## Implement the boundaries

The `jusi.plugins.v1` entry point is a zero-argument catalog provider returning
JSON data. `plugin_id` equals its entry-point name; distribution and plugin
version match installed metadata. Discovery runs in a fresh target-side process;
it must not start sessions, terminals, or import the worker/terminal application.
Declare only implemented capabilities, media types, and interaction needs.

A catalog kernel module supplies `jusi_kernel_adapter_v1()`. It may receive the
notebook's target configuration through `configure_jusi_runtime_v1(config)` and
register magics through `load_ipython_extension(ipython)`. Successful execution
emits one `application/vnd.jusi.handoff.v1+json` record identifying the exact
provider/version/family/magic and an application-owned JSON payload. The
supervisor chooses the worker reference from its catalog, never from the payload.
Do not transfer Python objects by resurrecting the legacy pickle contract.
Large data must not be embedded in control messages. For target-local kernel
handoffs, see ADR 0039 and jusi.kernel_artifacts: kernel-owned private JSON
artifacts can be consumed into client-owned staging, as bundled VisiData does.
Paths in these private target payloads never become frontend file paths.

The worker factory receives immutable `WorkerContext` and returns an object
with `handle(operation, payload)` and optional `close()`. Factory, ordinary
handlers, and cleanup run on the same thread; `interrupt()` runs concurrently.
Keep session ownership inside the worker/client lifetime, not in discovery,
frontend state, or a process-global provider registry. A plugin's target-side
files or application IPC do not imply that Neovim can access those paths.

For an interactive terminal, initial execute returns `WorkerResult` with one
`terminal_surface(...)` request. Core supplies geometry before starting the
application. Later operations cannot create replacement surfaces. Keep terminal
bytes opaque and application input separate from kernel-control transport.
Initial construction must produce a usable client/surface or fail visibly.

## Continuing operations

- Followup receives the literal submitted `body`. Preserve the same client,
  session and surface across successful work and recoverable errors. Results do
  not determine client lifetime. Core captures history only for followupables.
- Use `OperationRejected` for an explicitly recoverable request error;
  unexpected exceptions remain fatal. Render domain errors inside a healthy
  plugin application where appropriate. Never catch broken-session failures and
  pretend the session remains usable.
- Declare `interrupt` only with a prompt, thread-safe `interrupt()` hook that
  requests cancellation. Do not close the session, signal the kernel, wait for
  `handle()` to return, or take its long-held lock. Ordinary work raises
  `OperationInterrupted` after stopping. Initialize/reset cancellation state so
  an interrupt arriving before `handle()` starts cannot be cleared accidentally.
  Prove the next operation uses the same healthy client.
- Completion receives full `body`, `prefix`, and zero-based Unicode code-point
  `cursor_pos`, `cursor_row`, `cursor_col`. Compute candidates from the prefix;
  return explicit `text`, `start`, `end` ranges with
  `0 <= start <= end <= cursor_pos`. Choose small-word, whitespace-Word, or full
  prefix semantics in the plugin. Test empty prefixes, punctuation, Unicode,
  multiline prefixes and adjacent suffixes such as `s|lalala`. Core owns native
  popup navigation and application without rewriting the suffix.
- For `editor_action`, interpret the opaque `selection` object in the plugin and
  return `copy_text(text, linewise=...)` or
  `open_text(text, name="selection.csv", filetype="csv")`. Content travels in
  HTTP; a filename hint is not a remote path. The current export contract is
  UTF-8 text without NUL, with no total byte ceiling (ADR 0039). Worker editor
  results use data framing; application exports use chunked HTTP. Missing
  selections and transfer/storage failures should be recoverable.

## Application-driven copy/open

Use `src/jusi/editor_client.py` and ADR 0036 for actions invoked inside terminal
applications. A client advertising `editor_actions` receives the private
`JUSI_EDITOR_ACTION_SOCKET` environment variable at terminal launch; preserve it
when spawning application subprocesses. Core derives ownership from this channel.

Capture the current value or rendered selection before any asynchronous work.
Call `jusi.editor_client.copy(text, linewise=False)` or
`jusi.editor_client.open_text(text, name="selection.csv", filetype="csv")`.
For display-only comparison, use jusi.editor_client.show_diff(before, after,
before_name="before.txt", after_name="after.txt", filetype=""). It opens native
read-only diff windows in a separate tab and acknowledges display, never
acceptance/rejection or writeback. The worker-result counterpart is
jusi.plugin_api.show_diff(...). See ADR 0040 and docs/1.0/architecture/show-diff.md.

These helpers deliver immediately and block for editor acknowledgment; they are
different from `jusi.plugin_api` helpers that construct worker return values.
Move delivery off the application UI thread when needed, keeping selection
capture and UI error reporting in the appropriate application context. Report
success only after the helper returns; surface `EditorDeliveryError` without
blindly retrying an uncertain side effect.

A shell can invoke `jusivim PATH [--filetype TYPE]`, or the equivalent
`python -m jusi.editor_client PATH`. The helper reads in its own target filesystem
and working directory and sends content over HTTP through core. Neovim never
needs access to the source path. No SCP, shared storage, terminal escape commands
or frontend-specific code is needed for remote content delivery.

Copy/open are independent snapshots without writeback. Their automatic delivery
uses a renewable inactivity lease; this does not add deadlines to execution, input or
followups. Reconnect deduplicates action IDs; source close cancels pending actions
and leaves delivered buffers alone. Public `JusiCopy`/`JusiOpen` commands remain
withdrawn. Internal editor-invoked selection exports remain available when a
caller needs them, but application keybindings use the application channel.

Application-originated interruptible work publication/leases and edit-and-return
are still separate gaps. The action channel does not make arbitrary work inside
a terminal visible to `JusiInterrupt`. Read
`docs/1.0/architecture/backend-driven-editor-actions.md` for the implemented flow,
fixture commands and the distinct future edit operation before extending it.

## Verify the plugin

Exercise discovery/version mismatch, exact routing, startup and cleanup, ordinary
success, recoverable error followed by success, fatal isolation, and every
advertised capability. Use real subprocess tests for process/thread behavior and
a real Neovim scenario for presentation or editor delivery. Assert stale work
rejection, same-client reuse after cancellation, and independent exported-buffer
lifetime. Protocol changes update schemas, Python/Lua, shared valid/invalid
fixtures, and affected end-to-end coverage together.

Run the plugin project's tests and targeted integration against the installed
Jusi version. For a core contract change, first establish that it is in scope;
then follow core instructions and update all affected protocol consumers.
Report implemented behavior, tests actually run, and any remaining contract gap.
