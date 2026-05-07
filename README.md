# Jusi

Jusi is the Python backend for notebook-style execution used by the `jusivim` Vim/Neovim plugin.

It is responsible for:

- kernel lifecycle management
- notebook execution coordination
- per-cell client lifecycle
- plugin handler supervision
- protocol events and transport metadata for the editor side

Jusi is designed as part of a two-component system:

- `jusivim` editor plugin
- Jusi backend

The backend may run locally or remotely depending on the selected session target.

## Entrypoints

Primary editor-facing entrypoint:

- backend root process: `python -m jusi`

Internal/support entrypoints used by the backend and `jusivim`:

- native terminal attach: `python -m jusi client-process terminal-attach`
- plugin runtime starter: `python -m jusi plugin-runtime`
- transcript runtime entrypoint: `python -m jusi client-runtime`

Normal usage is through `jusivim`, which starts and talks to the backend over the Jusi protocol.

## Protocol Features

The backend protocol supports:

- session start and attach
- cell execution
- interrupt and input reply
- disconnect and reconnect
- client inspection and shutdown
- structured handler messaging for plugin follow-up and completion
- native terminal transport advertisement for interactive handlers

Session metadata may also include:

- `plugin_specs`
  - editor-facing presentation defaults keyed by magic name
- `palette`
  - editor-facing plugin creation metadata keyed by magic name

Cell metadata may include:

- `presentation`
  - authoritative post-handoff presentation metadata for an executed cell
- `runtime_mode`
  - backend-owned runtime mode such as `transcript` or `handler`

## Repository Layout

- `src/jusi/domain/`
  - core models and policies
- `src/jusi/application/`
  - use cases and service interfaces
- `src/jusi/infrastructure/`
  - Jupyter/runtime integration and process management
- `src/jusi/interfaces/`
  - protocol parsing and server entrypoints
- `src/jusi_vd/`
  - bundled first-party `%%vd` plugin
- `docs/`
  - protocol and architecture reference
- `tests/`
  - backend test suite

## Documentation

- [Protocol](docs/protocol.md)
- [Session Lifecycle](docs/session-lifecycle.md)
- [Plugin Contract](docs/plugins.md)
- [Architecture](docs/architecture.md)
- [Backend Map](docs/backend-map.md)

## Contributing

Development workflow and contributor notes live in [CONTRIBUTING.md](CONTRIBUTING.md).
