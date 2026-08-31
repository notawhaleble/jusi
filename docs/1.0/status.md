# Jusi 1.0 Status

Updated: 2026-08-31

## Current Facts

- The foundation review is accepted; ADRs 0001-0006 are active.
- The 0.x Python package, bundled `jusi_vd`, legacy tests, and stale smoke script have been removed from the active tree. Their exact provenance remains under `docs/legacy/0.x/` and Git history.
- The Python package is now `1.0.0.dev0` and contains framework-independent domain/application layers, a managed Jupyter adapter, and a thin Tornado HTTP/SSE service.
- The walking-skeleton protocol supports start, execute, inspect, stop, ordered replayable events, structured failures, and idempotent repeated stop.
- Python and Lua consume shared command and event fixtures, including unchanged ANSI-bearing text.
- The Neovim frontend now has a pure Lua symbolic-format parser, model-owned cell identities, extmark anchors, locally spliced structural reconciliation, and an executable 10,000-line/1,000-cell performance harness. Rendering, commands, and backend bindings have not started.
- The 1.0 development environment is `.venv`; legacy `venv2` imports Jusi 0.1.1 from the detached `/Users/niku/Documents/dev/jusi-0.x` worktree.
- Current verification in the unrestricted development environment: all 15 Python tests pass, including the HTTP/SSE adapter and black-box real-kernel walking skeleton; headless Neovim protocol conformance passes.
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
- Neovim rendering, commands, backend bindings, and terminal presentation
- input, interrupt, completion, rich media, PTY clients, and durable event storage

## Next Boundary

1. review the parser/model API and benchmark evidence
2. define the first frontend command and transport-binding slice without adding rendering complexity
