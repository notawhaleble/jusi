# Jusi 1.0 Intent

## Product

Jusi turns a plain-text Neovim buffer into a responsive notebook backed by an explicitly supervised kernel.

Jusi 1.0 is one repository and one coordinated product:

- Python service and backend supervisor
- Neovim-only Lua frontend
- shared versioned protocol and fixtures
- backend, frontend, conformance, and end-to-end tests
- durable architecture, incident, performance, and continuity records

The Vim-compatible 0.x product remains in the sibling `jusivim` repository.

## Rewrite Goal

The rewrite replaces confusing recovery rituals with small authoritative resource models, localized failure, actionable diagnostics, and predictable cleanup. Learned user behavior and regression knowledge are preserved; accidental topology and state complexity are not.

## Primary User Model

- A kernel is `off` or `on`.
- `checking` is shown only while a remote supervisor's reachability or kernel ownership is being verified.
- Start and stop are operations, not durable kernel states.
- A dead kernel is `off` and cannot be reconnected.
- Frontend reconnection, if named that way, reconnects only the frontend's transport/event stream to a surviving authoritative supervisor.
- Execution and plugin failures normally affect their owning execution, cell, client, or worker—not the notebook or kernel.
- Restart is a full notebook-runtime replacement: preserve current user text and editor state, but reload configuration, rediscover plugins, rebuild the notebook model, and start a new kernel generation.

## Notebook Direction

- The notebook remains plain text.
- `.vipynb` remains the canonical notebook extension; Neovim assigns it the `jusi` filetype.
- The notebook model owns stable cell identities.
- Neovim extmarks anchor model identities to mutable buffer text.
- Text, not syntax or extmarks, determines notebook structure and cell type.
- Ordinary editing is local work and remains independent of backend availability.

The native 1.0 text grammar, symmetric cell boundaries, history suffix, and localized recovery rules are defined in [notebook-format.md](notebook-format.md).

## Transport Direction

- HTTP is the baseline command and inspection plane.
- SSE is the baseline ordered event plane.
- Textual and ANSI-bearing output uses Neovim's terminal renderer without a Jusi ANSI parser.
- A dedicated PTY or stream is added only for genuinely interactive, bidirectional, or high-volume clients.

## First Milestone

The first walking skeleton is complete when an automated black-box test proves:

```text
service ready -> kernel on -> execute 1 + 1 -> ordered result 2 -> kernel off
```

It does not require the full Neovim UI.

## Explicit Non-Goals For The Skeleton

- remote supervisor deployment
- kernel attachment or recovery
- plugin workers
- terminal PTY interaction
- rich media rendering
- completion, history UI, palettes, or legacy mappings
- broad migration of the 0.x plugin API
