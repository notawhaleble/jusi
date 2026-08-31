# Jusi

Jusi 1.0 is a unified Neovim notebook system containing:

- a Python backend service and kernel supervisor
- a Neovim-only Lua frontend
- a versioned shared protocol and conformance fixtures
- backend, frontend, and end-to-end tests
- durable architecture, incident, and continuity records

The foundation and walking skeleton are implemented. The Python package provides the authoritative supervisor, managed Jupyter adapter, HTTP commands, and ordered SSE events. The Neovim frontend provides the symbolic notebook parser, model-owned cell identities, extmark anchoring, localized reconciliation, HTTP/SSE transport, and service controller. A headless test proves the full start, execute, ordered-result, and stop path without an interactive UI. Commands and rendering remain deferred.

The sibling [`jusivim`](../jusivim) repository remains the working Vim/Neovim-compatible 0.x frontend. Its Vimscript is not being moved into this repository.

## Start Here

- [1.0 intent](docs/1.0/intent.md)
- [product invariants](docs/1.0/invariants.md)
- [notebook format](docs/1.0/notebook-format.md)
- [state and resource model](docs/1.0/state-model.md)
- [failure taxonomy](docs/1.0/failure-taxonomy.md)
- [architecture overview](docs/1.0/architecture/overview.md)
- [current status](docs/1.0/status.md)
- [shared protocol](protocol/README.md)
- [0.x historical evidence](docs/legacy/0.x/README.md)

## Current Boundary

The implemented slice is deliberately narrow: service readiness, kernel start, `1 + 1`, ordered result event, idempotent kernel stop, the backend-independent notebook model, and a replaceable headless-Neovim transport/controller binding. Existing 0.x reconnect, healthcheck, prepared-client, stdio, and process-oriented terminal-attachment behavior is not part of 1.0.
