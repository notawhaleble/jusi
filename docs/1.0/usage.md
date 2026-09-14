# Using Jusi

Start with the [installation guide](installation.md) for setup and a first notebook.

## Jupyter Conversion

Use `jusi import-ipynb notebook.ipynb` to create `notebook.vipynb`, or
`jusi export-ipynb notebook.vipynb -o exported.ipynb` for the reverse direction.
Import includes code, Markdown and raw sources as ordinary native cells. Export
creates code cells; outputs and followup history are excluded.
See [conversion details](notebook-conversion.md).

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
notebook; editing does not call the backend. See [ADR 0030](adr/0030-cell-local-syntax-and-indentation.md).

## Navigation And Cell Mode

Press Space in Normal mode to toggle cell mode. Its borders display as
`╔══` / `╚══` in the existing status colors; Insert mode restores the rounded
borders and native editing/completion, and Esc
returns to cell-mode controls.

| Cell-mode key | Action |
| --- | --- |
| `j`, `k` | Next / previous cell or expanded history entry; counts work |
| Enter | Execute, send pending input, send a followup, or restore history by context |
| `H` | Toggle history |
| Ctrl-P / Ctrl-N | Restore older / newer history |
| `C` | Clear payload and edit, keeping magic header and history |
| `X` | Delete cell and close its resources |
| `Y` / `P` | Copy cell / paste below with a fresh identity |
| S | Toggle output parking (`~` beside the status symbol) |
| B | Create an empty cell below and enter Insert |
| `Q` / `{id}Q` | Close current / numbered output and its resources |
| `{id}G` | Jump to the numbered output's source cell |

`:JusiNextCell` and `:JusiPreviousCell` skip history and work in either mode.
`:JusiCellNewAbove` / `:JusiCellNewBelow` create an empty cell and enter Insert.
`:JusiCellEdit`, `:JusiCellDelete`, `:JusiCellCopy`, `:JusiCellPasteBelow`,
`:JusiCellModeToggle`, and `:JusiSubmit` expose the same actions explicitly.
A new accepted execution closes completed outputs on its kernel. Busy cells and
followup clients remain. Use `:JusiPark` on an output or its source cell to toggle
retention across later executions; explicit close or re-executing that cell
still replaces its artifact.

The original `JusiExecute`, `JusiInput`, and `JusiFollowup` commands remain
available when you want a specific submission variant.

## Palette and Focus

Use `:J <Tab>` from any buffer to select a loaded notebook. Completion then
offers its discovered magics and configured aliases:

```vim
:J notebook
:J notebook vd
:J notebook sql main
:J! notebook sql main
```

Plain `J notebook` creates a new empty cell. A magic request reuses its matching
cell or creates one; `J!` also submits it using the same contextual behavior as
Enter in cell mode. A visual selection supplies the body, preserving existing
cell history. New cells enter Insert mode. Notebook labels use filenames without
the extension; duplicate names use paths, and completion escapes spaces.

The notebook opens in a left split when it is absent from the current tab.
Completion reads cached startup metadata: aliases come from target-side
`[magic.alias]` configuration tables and refresh on restart. Offline notebooks
support plain cell creation; magic completion becomes available after startup.

Press **Ctrl-\ twice** to toggle between a cell and its output, including from
Insert mode or a terminal client. Entering an interactive client starts terminal
input; returning to the notebook uses Normal mode. Existing bindings take
precedence. From an unrelated buffer, the chord focuses the first visible
notebook in the current tab, or the first visible notebook across other tabs.
It also works for offline notebooks. To use another key, map it to `:JusiToggleFocus` (or call
`require('jusi.focus').toggle()` to also enter terminal input automatically).

## Statuslines and Backslash Keys

Notebook statuslines show kernel `on`/`off`, target alias, modified text and cell
mode. Before inspection the kernel is unknown; disconnected transport is shown
separately, with cached kernel state marked **last known**.

Each output/client statusline shows a numeric ID. Use `12G` in cell mode to jump
to output 12's cell, or `12Q` to close it from any current cell. These keys also
work in output buffers in Normal mode. Outside cell mode use `12\g` and `12\q`.
`:JusiGotoClient 12` and `:JusiCloseClient 12` work from any buffer. IDs survive
hiding a window, are never reused in a Neovim session, and retire with the output.
Closing an old ID cannot close its replacement.

Notebook Normal-mode shortcuts use a literal backslash, independent of mapleader:

| Key | Action |
| --- | --- |
| `\a` / `\b` | New cell above / below |
| `\c` / `\x` | Edit / delete cell |
| `\y` / `\p` | Copy cell / paste below |
| `\j` | Submit by context, including input, followup and history |
| `\h` | Toggle history |
| `\s` | Toggle output parking |
| `\ii` / `\00` | Interrupt / restart |
| `\q` / `{id}\q` | Close current / numbered output |
| `{id}\g` | Jump to numbered output's cell |

Existing custom mappings take precedence. Ordinary `G` keeps its native behavior
outside cell mode; uncounted `G` in cell mode also goes to the last line.

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
remain; one undo restores the previous body. Both commands work offline. Cell-mode `H` and Normal-mode `\h` toggle history;
Enter in cell mode restores the selected history entry.

## Explicit Lifecycle Controls

Start the service in a regular terminal:

```sh
jusi serve
```

This uses target-side `~/.jusi/jusi.toml` when present. Use
`jusi serve --config /path/to/jusi.toml` for an explicit path; Neovim
does not read or upload backend configuration.

With the Neovim plugin installed, open a `.vipynb` buffer containing 1.0
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
the same worker and keeps its output surface. Successful delivery updates the client without a status notification. It has no default mapping and does not start a new kernel
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
authors should use the [completion payload and range contract](adr/0027-completions-use-explicit-source-ranges.md).

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

For a single-command lifecycle, use `:JusiStart local` with `jusi` on PATH,
then `:JusiStop`. Named local and remote profiles and a launch command for
local and remote use are documented in the
[target start/stop guide](architecture/target-start-stop.md).

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


## VisiData

The bundled `%%vd` plugin evaluates a Python expression and opens a snapshot in
VisiData. Install with `pip install 'jusi[vd]'`, then restart the notebook runtime.
Inside the terminal, `zY` copies to Neovim and Ctrl-O opens the current value in a
split. See the [usage guide](architecture/bundled-vd.md) for examples,
supported values and snapshot behavior.


## Plugin-driven diff display

Applications can call `jusi.editor_client.show_diff(before, after, ...)` to
display read-only snapshots in a new native Neovim diff tab. This uses the same
remote-capable transfer channel as copy/open and acknowledges display only.
See the [show-diff guide](architecture/show-diff.md).
