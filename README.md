# Jusi

Jusi is the standalone Python backend for notebook-style execution used by Jusivim.

Deployment model:

- Jusi is intended to remain a two-component system:
  - local Vim plugin (`../jusivim`)
  - Jusi backend
- the backend is launched by `jusivim`, not as a separately user-managed service
- the backend may reside locally or remotely depending on the target/workflow
- remote support should not require inventing a third user-facing helper component in addition to the plugin and backend

Jusi is responsible for:

- kernel lifecycle management
- notebook execution coordination
- cell-owned client lifecycle
- backend-to-editor execution events
- transport and runtime integration around those capabilities

Current runtime entrypoints:

- backend root process: `python -m jusi`
- client process: `python -m jusi client-process`
- generic plugin runtime starter: `python -m jusi plugin-runtime`

The project is built around:

- explicit protocol and state transitions
- clean architecture boundaries
- transport isolation from domain logic
- operationally safer session and process management

The Vim plugin lives in a separate repository: `../jusivim`.

## Development Workflow

For normal local development, use an editable install in a virtual environment rather than relying on raw `PYTHONPATH=src`.

Why:

- first-party bundled plugins such as `jusi_vd` are now discovered through real package entry points
- that metadata exists in the installed distribution, not in source imports alone

So the authoritative local path is:

```sh
venv2/bin/python -m pip install -e .
```

Then run tests and local commands through that environment.

## Repository Layout

- `docs/`: protocol, lifecycle, and architecture notes
- `src/jusi/domain/`: core entities and policies
- `src/jusi/application/`: use cases and service interfaces
- `src/jusi/infrastructure/`: Jupyter integration, transport, and process management
- `src/jusi/interfaces/`: external entrypoints and message adapters
- `src/jusi_vd/`: first-party bundled `vd` plugin package
- `tests/`: backend tests
