# Jusi 1.0 Status

Updated: 2026-08-31

## Current Facts

- The foundation review is accepted; ADRs 0001-0007 are active.
- The 0.x Python package, bundled `jusi_vd`, legacy tests, and stale smoke script have been removed from the active tree. Their exact provenance remains under `docs/legacy/0.x/` and Git history.
- The Python package is now `1.0.0.dev0` and contains framework-independent domain/application layers, a managed Jupyter adapter, and a thin Tornado HTTP/SSE service.
- The walking-skeleton protocol supports start, execute, inspect, stop, ordered replayable events, structured failures, and idempotent repeated stop.
- Python and Lua consume shared command and event fixtures, including unchanged ANSI-bearing text.
- The Neovim frontend has a pure Lua symbolic-format parser, model-owned cell identities, extmark anchors, locally spliced structural reconciliation, and an executable 10,000-line/1,000-cell performance harness.
- A replaceable curl/`vim.system` adapter and Lua controller now bind that model to HTTP commands and ordered SSE events.
- Textual output now projects unchanged through `nvim_open_term()` into a hidden, cell-attached terminal buffer. Renderer choice is media-driven.
- Repository-root Neovim runtime loading exposes explicit connect, kernel start/stop, execute, output-open, and disconnect commands. Output opens in a predictable bottom split.
- The 1.0 development environment is `.venv`; legacy `venv2` imports Jusi 0.1.1 from the detached `/Users/niku/Documents/dev/jusi-0.x` worktree.
- The headless-Neovim black-box scenario starts the real service and kernel, executes `1 + 1` from a model cell, receives the ordered `text/plain` result event, and stops the kernel without loading an interactive UI.
- The archival tag `legacy/0.x-pre-1.0-2026-08-31` and detached sibling worktree `/Users/niku/Documents/dev/jusi-0.x` preserve the audited backend snapshot.

## Walking Skeleton Boundary

Implemented:

- service readiness
- local managed Python kernel adapter
- HTTP start/inspect/execute/stop
- supervisor-owned `off`/`on` kernel truth
- ordered SSE event IDs, sequence, replay cursor, and gap detection
- `text/plain` results and ANSI-preserving text output
- structured process-aware failures
- idempotent stop reporting `stopped` or `already_absent`
- black-box service/kernel scenario runnable outside the restricted sandbox

Deferred:

- full notebook restart command
- plugin discovery and workers
- remote supervisors and `checking`
- automatic local-service ownership, richer window/focus policy, and interactive PTY clients
- authoritative resynchronization after supervisor replacement or event cursor expiry
- input, interrupt, completion, rich media, PTY clients, and durable event storage

## Next Boundary

1. harden event payload conformance and authoritative supervisor/cursor resynchronization
2. decide explicit local-service ownership before adding automatic spawning
