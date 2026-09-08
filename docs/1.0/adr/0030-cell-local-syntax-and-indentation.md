# ADR 0030: Cell-Local Syntax And Indentation

- Status: accepted
- Date: 2026-09-09

## Metadata and attribution

Plain cells initially use Python syntax and indentation. Notebook editing is
available on filetype attachment before connecting to a service; disconnect
retains editing and the last discovered catalog. Non-Python kernel language
negotiation is a later extension, not inferred from kernel names.

Discovery occurs at kernel start/full restart. The existing family
`presentation.syntax` and `presentation.indent` catalog fields provide shared
editing defaults. An optional `provider_presentation` on each exact provider's
family claim overrides those fields independently after an authoritative client
handoff. Shared family defaults must agree across providers; provider overrides
need not. Core does not inspect alias configuration to guess the provider.

Profiles are local Neovim runtime identifiers: empty (no language-specific rules), or a lowercase
letter followed by lowercase letters, digits or underscores, at most 64 bytes.
They name local syntax/indent files, never paths, commands or backend code.
Unavailable profiles warn once and fall back to unhighlighted text/previous-line
indentation. Unknown magics retain Python body editing and a distinct magic
header; recognized families can independently select or disable each profile.

Provider attribution records the execution identity, exact magic header and its
local revision. Changing the header (including alias arguments) invalidates it;
changing it back cannot revive a late handoff. Body editing preserves it. Cell
retirement and runtime replacement discard it. A later execution supersedes
older pending attribution. Output/client close does not erase editing metadata
already resolved for an unchanged header.

## Local language evaluation

Use Neovim's installed syntax and indent runtime files in one isolated headless
Neovim child per notebook frontend. This is a local editing worker, independent
of the target-side service, kernel and plugin workers. It loads no user init or
plugins; it uses the frontend runtimepath to resolve the named local profiles.
No editing request contacts the backend or interprets cell code.

Each materialized cell has its own scratch buffer containing only its active
body, excluding the opener, closer, magic header and history suffix. Consequently
an unterminated string/comment or an indentation search cannot enter another
cell. Syntax and indentation names are independently selected. Indentation runs
the installed expression with cell-relative line numbers, cursor and syntax
context; C/Lisp/native fallback behavior remains local to that same buffer.

An in-process scratch buffer cannot be updated inside `indentexpr` because
Neovim holds textlock. The child permits evaluating current text with native
indent scripts without mutating or switching the user's buffers/windows. It is
reused for the notebook lifetime and terminated on detach/replacement/wipeout.
Python-style four-space indentation is the initial notebook default; notebook
width/tab options are passed to the worker, and prior options restore on detach.
Native profile indent triggers are combined in the notebook's dispatch keys.

## Rendering and editing

The union of cells intersecting all windows displaying the notebook is the
highlight working set. Whole active bodies are provided, including their
offscreen prefixes and suffixes. Cells leaving that set release their scratch
buffers/highlights; no whole-notebook language pass runs on ordinary typing.
Indenting an offscreen cell explicitly can materialize it on demand.

Syntax spans project through extmarks using the frontend's standard highlight
groups, leaving delimiter status marks separate. Changes update the affected
cell's worker text incrementally; unchanged visible cells are cached and their
extmarks follow line movement. TextChangedI/TextChangedP and local model change
notifications refresh highlighting without leaving Insert mode or disabling
syntax. Window changes update visibility. Edits arriving during a local worker
request fence its result by changedtick. The magic header itself uses PreProc.

Rendering/evaluation uses local RPC; its cost depends on the installed language
rules and size of a visible cell. Whole-cell visibility does not bound the size
of one enormous cell. This initial implementation favors native-rule correctness
and isolation over introducing a mandatory Tree-sitter parser distribution.

## Verification

Shared Python/Lua catalog fixtures accept independent provider overrides and
reject unsafe profile identifiers. Frontend tests cover isolated multiline
syntax, Python newline indentation, magic/header fencing, Insert-mode updates,
viewport union, offline attachment and worker cleanup. SQLite end-to-end
coverage checks family metadata after discovery and exact provider attribution
after execution. Existing model performance and lifecycle suites remain active.
