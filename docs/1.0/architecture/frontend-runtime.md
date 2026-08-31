# Neovim Runtime And Command Surface

## Explicit Lifecycle

The initial Neovim runtime keeps one local frontend session per attached
notebook buffer. A session combines the notebook model, frontend transport,
controller, and presentation manager without merging their ownership.

`:JusiConnect [base_url]` attaches the current buffer and opens its event stream.
It does not start a kernel. `:JusiDisconnect` closes only frontend transport and
projections; it does not claim that the supervisor or kernel stopped.

The first commands are:

- `:JusiConnect [base_url]`
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

The repository-root `plugin/jusi.lua` calls `setup()` with defaults so ordinary
Neovim plugin managers expose commands immediately. Calling `setup()` in user
configuration updates defaults without duplicating commands.

## Deliberate Deferral

This slice does not spawn or own the Python service process. The user starts a
local service explicitly or supplies a supervisor URL. Automatic local service
ownership needs a separate decision covering executable discovery, logs,
shutdown, multiple Neovim instances, and whether a kernel should outlive a
frontend transport.
