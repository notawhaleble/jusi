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
:JusiToggleFocus
:JusiClose
:JusiRestart
:JusiStopKernel
:JusiDisconnect
```

Connecting and disconnecting affect only the frontend transport. Disconnect
preserves the current notebook model and output surfaces for later transport
resumption. Neither command implicitly starts or stops a kernel.

Execution reveals the cell's ordinary output or plugin client without taking
focus. `:JusiToggleFocus` moves between that artifact and its source cell and
reopens a buffer hidden with native `:close`. `:JusiClose` completely removes
the cell artifact, including backend client teardown when required, without
stopping the kernel.

Alternatively, when `jusi` is on `PATH`, `:JusiServiceStart` explicitly launches
and connects a notebook-local service; `:JusiServiceStop` stops its kernel and
owned service. Configure another executable through
`require("jusi").setup({ service_command = { ... } })`.
