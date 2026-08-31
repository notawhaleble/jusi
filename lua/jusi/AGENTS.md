# Neovim Frontend Instructions

## Ownership

The Lua frontend owns notebook parsing, stable model cell identities, extmark anchors, rendering, commands, mappings, buffers, windows, and terminal surfaces.

It does not own kernel liveness, backend resource truth, process cleanup policy, or plugin-worker state.

## Hot-Path Rules

- Text is the source of notebook structure.
- Cell identity is model-owned; extmarks anchor identities to changing text.
- Signs, highlights, and terminal buffers are projections, never sources of truth.
- Ordinary typing must not contact the service or scan/re-render the whole notebook.
- Reparse and redraw only the affected region unless a structural ambiguity requires bounded recovery.

## Presentation

- Choose a renderer from media type and interaction requirements, never cell kind.
- Feed textual and ANSI-bearing output to Neovim's terminal renderer without implementing an ANSI parser.
- A PTY or dedicated bidirectional stream is reserved for genuinely interactive clients.
- The frontend never guesses whether a kernel is alive.
