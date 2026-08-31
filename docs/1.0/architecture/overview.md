# Jusi 1.0 Architecture Overview

## Shape

```text
local target:
  Neovim -> local Jusi service/supervisor -> local kernel/workers

remote target:
  Neovim -> HTTP/SSE -> remote Jusi service/supervisor -> remote kernel/workers
```

Optional per-client plugin workers and interactive streams remain subordinate,
isolated resources. They do not redefine kernel control.

The service runs at the kernel target. The local launcher is a convenience for
the local-target case, not a mandatory proxy tier or editor-wide daemon. ADR
0012 defines target placement and rejects a frontend-local gateway for remote
kernels.

## Control Plane

The Python service exposes resource-oriented HTTP commands and inspection. Commands create traceable operations. The supervisor owns resource truth and emits ordered events.

SSE is the baseline event stream because the primary direction is backend-to-frontend, ordering matters, and command traffic already has HTTP. WebSockets are not the default merely because some future client may be interactive.

## Presentation Plane

Output events declare media type and interaction capabilities.

- textual/ANSI output is fed intact to Neovim's terminal renderer
- rich media uses a renderer selected for its media type
- interactive terminal clients receive a dedicated PTY or stream adapter

Presentation does not alter kernel ownership or state.

## Notebook Model

The Lua model parses visible plain text into stable cells. Extmarks preserve anchors as text moves. Localized edits update only the affected parse and projection regions. Backend event correlation uses model cell IDs, never line numbers.

The implemented parser, linked reconciliation model, and edit paths are specified in [frontend-notebook-model.md](frontend-notebook-model.md). The service binding and its separation from rendering are specified in [frontend-controller.md](frontend-controller.md).

The initial cell-attached native terminal projection is specified in [frontend-presentation.md](frontend-presentation.md).

The explicit buffer session and first user commands are specified in [frontend-runtime.md](frontend-runtime.md).

## Failure Boundaries

The supervisor treats kernel, execution, client, plugin worker, service, protocol, and frontend transport as separate failure layers. Events and failures retain the originating layer and trace.

Kernel process capture and caused execution-failure chronology are specified in [observability.md](observability.md).

Fresh-process discovery, exact-plugin workers, and the retired 0.x handler topology are specified in [plugins.md](plugins.md) and ADR 0010.

## Implemented Walking-Skeleton Boundary

The active production slice contains only:

- service readiness
- one local managed Python kernel generation
- start, inspect, execute, stop HTTP commands
- ordered SSE events with cursor
- plain `text/plain` result event
- structured failure and process diagnostics sufficient for startup/death
- Python and headless-Neovim black-box real-kernel tests
- replaceable Lua HTTP/SSE adapter and resource controller

It excludes user commands and rendering, plugins, remote checking, authoritative stream resynchronization, PTYs, completion, and rich media.
