# Neovim Runtime And Command Surface

## Explicit Lifecycle

The initial Neovim runtime keeps one local frontend session per attached
notebook buffer. A session combines the notebook model, frontend transport,
controller, and presentation manager without merging their ownership.

`:JusiConnect [base_url]` attaches the current buffer and opens its event stream.
It does not start a kernel. `:JusiDisconnect` closes only frontend transport; it
preserves the notebook model, cell identities, and output projections and does
not claim that the supervisor or kernel stopped. A later `:JusiConnect` on the
same buffer inspects and resumes through ADR 0008.

Wiping the notebook buffer destroys its frontend model and projections. For an
external service that editor-local cleanup does not stop a kernel. If the buffer
explicitly launched and owns a local service process, destroying the owner also
performs best-effort cleanup of that process and its kernel.

The first commands are:

- `:JusiConnect [base_url]`
- `:JusiServiceStart`
- `:JusiServiceStop`
- `:JusiStartKernel`
- `:JusiExecute`
- `:JusiOpenOutput`
- `:JusiStopKernel`
- `:JusiDisconnect`

Execution resolves the current model cell through extmark-anchored ranges and
reads its active body at command time. It does not scan the notebook or run from
an edit callback.

## Initial Configuration

`require("jusi").setup()` accepts:

- `base_url` (default `http://127.0.0.1:8765`)
- `kernel_name` (default `python3`)
- `output_height` (default `12`)
- `service_command` (default `{ "jusi", "serve" }`)
- `service_timeout_ms` (default `8000`)

The repository-root `plugin/jusi.lua` calls `setup()` with defaults so ordinary
Neovim plugin managers expose commands immediately. Calling `setup()` in user
configuration updates defaults without duplicating commands.

## Owned Local Service

The user may start a service in a regular terminal and connect to its URL, or use
`:JusiServiceStart` for an explicitly owned notebook-local process. The launcher
uses an ephemeral loopback port, validates readiness JSON, retains the final
16 KiB of stderr, and reports spawn/readiness/exit/cleanup failures with a
distinct `service_process_id`.

For the managed-local path, `:JusiServiceStart` performs connection after
readiness; `:JusiConnect` is not a prerequisite. For a remote or independently
managed target, Neovim uses `:JusiConnect` and launches no local proxy.

Owned services are scoped per notebook buffer, not per Neovim process or OS
login session. Different buffers and different Neovim instances may therefore
own distinct local services. The current supervisor admits at most one active
kernel generation; kernels across restart are sequential, not concurrent.

`:JusiServiceStop` first stops an authoritatively `on` kernel, then disconnects
transport and terminates the owned service. A kernel-stop failure prevents the
service from being silently killed. Wiping the owning notebook buffer performs
best-effort owned-process cleanup; external URLs are never terminated.

After confirmed service stop, its frontend session and runtime projections are
retired while buffer text and undo history remain. A later
`:JusiServiceStart` on the same buffer creates a fresh service, supervisor,
transport, notebook model, and eventual kernel generation. Repeating start
while a session or launch is already active is rejected without spawning a
second process.
