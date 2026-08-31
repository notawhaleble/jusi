# Jusi Agent Instructions

## Scope

This repository is the unified Jusi 1.0 home for the Python service, Neovim Lua frontend, shared protocol, fixtures, and tests.

The sibling `../jusivim` repository is the working Vim-compatible 0.x frontend. It may be inspected for behavior and incidents, but must not be modified or copied here unless the user explicitly asks.

## Required Reading

Before changing architecture, protocol, lifecycle, or ownership, read:

1. `docs/1.0/intent.md`
2. `docs/1.0/invariants.md`
3. `docs/1.0/state-model.md`
4. `docs/1.0/failure-taxonomy.md`
5. `docs/1.0/status.md`
6. the relevant scoped `AGENTS.md`

Documents under `docs/legacy/0.x/` are historical evidence, never normative 1.0 design.

## Working Rules

- Preserve user changes and avoid destructive Git operations.
- Do not introduce durable kernel states beyond `off`, `on`, and exceptional remote `checking`.
- Treat start, stop, execute, interrupt, cleanup, and transport connection as operations, not kernel states.
- Never infer kernel liveness in the frontend.
- Keep supervisor, kernel, execution, client, plugin worker, cell, and frontend transport identities distinct.
- Keep terminal presentation separate from kernel-control transport.
- Select presentation by media type and interaction needs, not by cell kind or plugin identity.
- Protocol changes are atomic across schemas, Python, Lua, fixtures, and conformance tests.
- Ordinary typing must not perform backend, session, client, or whole-notebook work.
- Record architectural choices in ADRs and concrete failures in incident records.

## Continuity

`docs/1.0/status.md` is the tracked continuity snapshot. Keep it concise and factual. Replace stale facts; do not append a diary. Link to ADRs, incidents, or tests for detail.

## Verification

Run the Python and headless Neovim suites with:

```sh
.venv/bin/python -m pytest -q
nvim --headless -u tests/frontend/minimal_init.lua -l tests/frontend/run.lua
nvim --headless -u tests/frontend/minimal_init.lua -l tests/e2e/run.lua
```

The black-box test requires an environment that permits loopback HTTP and Jupyter ZeroMQ sockets; it skips explicitly in restricted sandboxes.
