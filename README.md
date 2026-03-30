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
- prepared client lifecycle
- backend-to-editor execution events
- transport and runtime integration around those capabilities

Current runtime entrypoints:

- backend root process: `python -m jusi`
- client process: `python -m jusi client-process`

The project is built around:

- explicit protocol and state transitions
- clean architecture boundaries
- transport isolation from domain logic
- operationally safer session and process management

The Vim plugin lives in a separate repository: `../jusivim`.

## Current Focus

The first implementation milestone is the backend contract and the first execution vertical slice:

- start a managed kernel
- report session readiness
- provision a prepared client
- execute a cell
- publish session, prepared-client, and cell updates back to the editor

## Repository Layout

- `docs/`: protocol, lifecycle, and architecture notes
- `src/jusi/domain/`: core entities and policies
- `src/jusi/application/`: use cases and service interfaces
- `src/jusi/infrastructure/`: Jupyter integration, transport, and process management
- `src/jusi/interfaces/`: external entrypoints and message adapters
- `tests/`: backend tests
