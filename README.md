# Jusi

Jusi 1.0 is a unified Neovim notebook system containing:

- a Python backend service and kernel supervisor
- a Neovim-only Lua frontend
- a versioned shared protocol and conformance fixtures
- backend, frontend, and end-to-end tests
- durable architecture, incident, and continuity records

The foundation, walking skeleton, isolated plugin-catalog discovery, and full notebook restart are implemented. The Python package provides the authoritative supervisor, managed Jupyter adapter, HTTP commands, and ordered SSE events. The Neovim frontend provides the symbolic notebook parser, model-owned cell identities, extmark anchoring, localized reconciliation, HTTP/SSE transport, service controller, media-driven native-terminal text projection, and an explicit first command surface. Headless tests prove start, execute, ordered-result, render, full runtime replacement, and stop with real kernels.

The sibling [`jusivim`](../jusivim) repository remains the working Vim/Neovim-compatible 0.x frontend. Its Vimscript is not being moved into this repository.

## Start Here

- [1.0 intent](docs/1.0/intent.md)
- [product invariants](docs/1.0/invariants.md)
- [notebook format](docs/1.0/notebook-format.md)
- [state and resource model](docs/1.0/state-model.md)
- [failure taxonomy](docs/1.0/failure-taxonomy.md)
- [target-side configuration](docs/1.0/configuration.md)
- [architecture overview](docs/1.0/architecture/overview.md)
- [current status](docs/1.0/status.md)
- [shared protocol](protocol/README.md)
- [0.x historical evidence](docs/legacy/0.x/README.md)

## Current Boundary

The implemented slice includes service readiness, fresh plugin-catalog discovery, isolated exact-plugin workers, attested kernel adapters, target-side runtime-configuration snapshots, incremental ordered text output, generic interactive terminal surfaces, idempotent kernel stop, full notebook-runtime restart, the backend-independent notebook model, and a replaceable headless-Neovim transport/controller binding. Web/rich presentation is deferred. Existing 0.x reconnect, healthcheck, prepared-client, stdio, and process-oriented terminal-attachment behavior is not part of 1.0.

## Cell Editing

Opening a `.vipynb` notebook enables Python syntax and four-space indentation
without connecting to a service. Each cell has isolated language context;
unclosed strings/comments and indentation cannot spill into neighboring cells.
Highlighting stays active during Insert mode and covers whole cells visible in
any notebook window.

At kernel start/restart, discovered family `presentation` profiles select
syntax and indentation for recognized magics. An exact provider can declare
`provider_presentation` overrides, applied after its cell executes. Editing the
magic or alias resets that attribution. Profiles name installed Neovim
`syntax/<name>.vim` and `indent/<name>.vim` files independently; missing profiles
warn once. One local headless Neovim editing worker is owned by each open
notebook; editing does not call the backend. See [ADR 0030](docs/1.0/adr/0030-cell-local-syntax-and-indentation.md).

## Navigation And Cell Mode

Press Space in Normal mode to toggle cell mode. Its borders display as
`╔══` / `╚══` in the existing status colors; Insert mode restores the rounded
borders and native editing/completion, and Esc
returns to cell-mode controls.

| Cell-mode key | Action |
| --- | --- |
| `j` / `n`, `k` | Next / previous cell or expanded history entry; counts work |
| Enter | Execute, send pending input, send a followup, or restore history by context |
| `H` | Toggle history |
| Ctrl-P / Ctrl-N | Restore older / newer history |
| `C` | Clear payload and edit, keeping magic header and history |
| `X` | Delete cell and close its resources |
| `Y` / `P` | Copy cell / paste below with a fresh identity |
| `Q` | Close the cell's output/client |

`:JusiNextCell` and `:JusiPreviousCell` skip history and work in either mode.
`:JusiCellNewAbove` / `:JusiCellNewBelow` create an empty cell and enter Insert.
`:JusiCellEdit`, `:JusiCellDelete`, `:JusiCellCopy`, `:JusiCellPasteBelow`,
`:JusiCellModeToggle`, and `:JusiSubmit` expose the same actions explicitly.
The original `JusiExecute`, `JusiInput`, and `JusiFollowup` commands remain
available when you want a specific submission variant.

## Cell History

Followup-capable plugins keep submitted bodies in the cell's history suffix,
newest first. Initial handoff and subsequent followups are captured; exact
repeats move to the front. Magic headers are omitted. Ordinary kernel execution
and kernel input replies do not add history.

History starts folded as `history: N entries`, without a delimiter prefix or
dot fill, using the muted `JusiHistoryFold` foreground, with the cell closer visible. Expanded history keeps its `╞══` delimiter.
Use native `zo`, `zc`, or `za`, or `:JusiHistoryToggle` from anywhere in the cell.
Each window keeps its own fold state. Open history is editable notebook text
with isolated syntax and indentation for each entry.

Place the cursor on an entry and use `:JusiHistoryApply` to restore it into the
active body without executing. The current magic header/alias and stored history
remain; one undo restores the previous body. Both commands work offline and have
no default mappings.

## Current Manual Workflow

Start the service in a regular terminal:

```sh
.venv/bin/jusi serve
```

This uses target-side `~/.jusi/jusi.toml` when present. Use
`.venv/bin/jusi serve --config /path/to/jusi.toml` for an explicit path; Neovim
does not read or upload backend configuration.

With this repository installed as a Neovim plugin, open a `.vipynb` buffer containing 1.0
cell delimiters and use:

```vim
:JusiConnect
:JusiStartKernel
:JusiExecute
:JusiInterrupt
:JusiInput
:JusiFollowup
:JusiComplete
:JusiToggleFocus
:JusiClose
:JusiRestart
:JusiStopKernel
:JusiDisconnect
```

Cell status appears after the opener through Neovim extmarks: green `✓`
for done, red `✗` for error, orange `!` for interrupted, and blue `>` for a live
followup client. Running work uses purple `*`, including while kernel input is
pending. Both delimiters share the status color. Never-executed cells have yellow
delimiters and no status symbol.
Both RGB and 256-color terminal palettes are defined; `termguicolors` is optional.
The buffer text and sign/status columns are untouched. These marks show the last
observed work outcome; editing a cell does not erase it.

For kernel input, execute a cell containing `input('lalala: ')`. Its output
split shows the prompt without taking focus. Replace the same cell body with
`ololo` and run `:JusiInput`; the original execution receives that literal text
and resumes. `JusiInput` requires a pending request for that cell. It preserves
whitespace and joins body lines with newlines; it does not evaluate the reply.
Accepted replies echo literally on the prompt line, followed by a newline,
including for assignment forms such as `a = input('lalala: ')`. Bare `input(...)`
can also produce a genuine Python expression result on the following line.
The command has no default mapping and is separate from plugin followups.
Kernel execution and input waits have no automatic time limit. End them with
`JusiInterrupt`, `JusiClose`, kernel stop, or restart; retiring the owning cell
also closes its active work.

For an existing plugin client that declares `followup`, edit its source cell
and run `:JusiFollowup`. It sends the literal body (including an empty body) to
the same worker and keeps its output surface. The command reports delivery after
the worker replies. It has no default mapping and does not start a new kernel
execution. Plugins interpret the body and update their own presentation.

Press Tab in Insert mode inside a connected notebook cell to invoke Vim's native
completion menu. Navigation, acceptance, cancellation, and `completeopt` keep
their usual Vim behavior; Jusi installs no mappings for them. Tab itself also
keeps native behavior while a menu is already visible. `<Plug>(JusiComplete)`
and `:JusiComplete` remain available for explicit Insert-mode invocation.

Same-line edits preview directly through Vim. A replacement reaching into earlier
lines is finalized on acceptance; cancelling leaves the original text intact.
Text after the cursor remains untouched.

Regular cells complete using their kernel scope. Plugin cells need an existing
client with the `complete` capability; empty prefixes are supported. Plugin
authors should use the [completion payload and range contract](docs/1.0/adr/0027-completions-use-explicit-source-ranges.md).

Connecting and disconnecting affect only the frontend transport. Disconnect
preserves the current notebook model and output surfaces for later transport
resumption. Neither command implicitly starts or stops a kernel.

Execution reveals the cell's ordinary output or plugin client without taking
focus. `:JusiToggleFocus` moves between that artifact and its source cell and
reopens a buffer hidden with native `:close`. `:JusiClose` completely removes
the cell artifact, including backend client teardown when required, without
stopping the kernel. Closing an active cell interrupts its execution as part of
that cleanup. Damaging or removing a cell's opener performs the same full close
automatically; damage to its closer preserves identity. Merging A with B by
removing their adjoining borders keeps A and closes B. Undo restores the second
cell's text with a fresh identity and no old output.

Alternatively, when `jusi` is on `PATH`, `:JusiServiceStart` explicitly launches
and connects a notebook-local service; `:JusiServiceStop` stops its kernel and
owned service. Configure another executable through
`require("jusi").setup({ service_command = { ... } })`.

After an error, use `:JusiTrace` to inspect the latest failure, or
`:JusiTrace trace_...` for a specific trace (tab completion is available).
The read-only split includes available resource identities, configuration path,
process exit status, and stderr. Close it with native `:close`.
The last 50 received failures are kept for this Neovim session, including failed
service startups; they are not a persistent or complete backend log.
