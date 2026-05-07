# Jusi Backend Map

This document is the practical backend map for Jusi.

It is meant to answer:

- which actors exist
- how they talk to each other
- which protocols and side channels are involved
- which module owns what
- what the important runtime flows look like

## Glossary

- `editor plugin`
  - the `jusivim` Vim/Neovim side
  - starts the backend
  - sends protocol requests
  - consumes protocol events and responses
  - owns Vim rendering, signs, buffers, terminal windows, completion UI

- `backend root process`
  - the main `jusi` process
  - entrypoint: `python -m jusi`
  - owns protocol parsing, session state, runtime supervision, handler supervision

- `session`
  - the durable execution identity for one notebook/backend pairing
  - addressed by `session_id`
  - has a `target` describing what kernel/runtime environment is used

- `cell execution`
  - backend-tracked state for one notebook cell
  - addressed by cell id
  - status examples:
    - `busy`
    - `done`
    - `follow-up`
    - `error`

- `client`
  - backend-owned execution/display identity
  - addressed by `client_id`
  - one session may have multiple live clients
  - a cell may be bound to one active client at a time

- `managed client process`
  - compatibility/runtime-support entrypoint: `python -m jusi client-process`
  - not the same thing as a plugin runtime

- `kernel handle`
  - the Jupyter-side execution resource for a session
  - may be:
    - managed child kernel
    - attached external connection file

- `handler`
  - plugin-side execution/display logic for a handler-owned cell
  - examples:
    - `vd`
    - `sqlite`
    - `shell`

- `handler controller`
  - in-process runtime/controller for a handler-owned cell
  - owned by backend root

- `plugin runtime`
  - entrypoint: `python -m jusi plugin-runtime`
  - terminal-hosted live runtime for a plugin
  - usually attached to a real editor terminal buffer
  - examples:
    - VisiData runtime
    - live shell runtime

- `native terminal attach`
  - outer attach entrypoint:
    - `python -m jusi client-process terminal-attach`
  - launched by the editor plugin in a real terminal buffer
  - then `exec`s into plugin runtime

- `handler_message`
  - structured control channel between editor plugin and backend for handler-owned clients
  - not the fullscreen terminal byte transport

- `action_request`
  - backend -> editor-plugin structured callback over `handler_message`
  - first built-in action:
    - `open_path`

## Layers And Responsibilities

### `jusi.domain`

Owns stable data shapes:

- session model
- cell execution model
- client transport model
- handler handoff model

### `jusi.application`

Owns use cases and orchestration rules:

- `StartSession`
- `AttachSession`
- `ExecuteCell`
- `InterruptCell`
- `HandlerMessage`
- `ShutdownClient`
- reconnect/disconnect/stop flows

This layer decides:

- when state changes
- when runtime methods are called
- when protocol events are emitted

### `jusi.infrastructure`

Owns concrete runtime/process integrations:

- managed kernel integration
- in-process client state and runtime support
- in-process handler control
- plugin runtime bridge/support
- client view rendering helpers
- debug timing

### `jusi.interfaces`

Owns protocol and top-level server entry:

- request parsing
- response/event encoding
- `ProtocolServer`

## Actors And Links

```mermaid
flowchart LR
    F[Editor Plugin]
    B[Backend Root Process\\npython -m jusi]
    K[Kernel Handle\\nmanaged or attached]
    C[Managed Client State\\nin-process]
    W[Handler Controller\\nin-process]
    T[Terminal Attach\\npython -m jusi client-process terminal-attach]
    P[Plugin Runtime\\npython -m jusi plugin-runtime]

    F <-->|Jusi protocol\\nrequest/response/event| B
    B <-->|Jupyter client API| K
    B -->|in-process transcript state| C
    B -->|in-process handler control| W
    W -->|transport metadata| B
    F -->|launch attach_cmd in terminal| T
    T -->|exec| P
    B <-->|controller method calls| W
    W <-->|plugin-runtime control request/response| P
    P -->|frontend action mailbox| B
```

## Protocol Surfaces

### Editor Plugin <-> Backend Root

Main notebook protocol.

Examples:

- requests:
  - `start_session`
  - `execute_cell`
  - `handler_message`
  - `inspect_client`
  - `shutdown_client`
- events:
  - `session_updated`
  - `cell_updated`
  - `client_updated`
  - `handler_message`
  - `healthcheck`

### Backend Root <-> Kernel

Not the Jusi protocol.

Mechanism:

- Jupyter client APIs
- shell/iopub/stdin channels

Used for:

- executing normal code cells
- consuming stream/output messages
- loading kernel extensions
- receiving plugin handoff mime payloads

### Backend Root <-> Handler Controller

In-process control boundary.

Used for:

- startup handshake
- execution result
- execution events
- status updates
- transport publication
- live handler message routing
- backend action requests from handler control

### Handler Controller <-> Plugin Runtime

Plugin runtime control request/response.

- Unix socket owned by plugin runtime
- synchronous request/response from handler-controller side

Used for:

- follow-up
- completion
- other live runtime control

### Plugin Runtime -> Backend Root

This path is asymmetric and minimal.

Used only when plugin runtime must emit an unsolicited backend callback.

Example:

- shell `jusi-open ...`

Mechanism:

- append JSON lines to the per-client action mailbox file
- backend root drains that file during `poll_client_updates()`
- backend root converts records into:
  - transcript `frontend_action_request`
  - `handler_message` callback with `message_type = action_request`

This is not the same path as normal `followup` / `complete`.

## Built-in Editor Callback

```mermaid
classDiagram
    class HandlerMessageEvent {
      notebook_id
      session_id
      client_id
      handler_id
      message_type = action_request
      payload
    }

    class ActionRequestPayload {
      action_type
      payload
    }

    class OpenPathPayload {
      path
      open_in?
      line?
      column?
    }

    HandlerMessageEvent --> ActionRequestPayload
    ActionRequestPayload --> OpenPathPayload
```

Built-in action:

- `action_type = open_path`

Semantics:

- `path` is required
- `open_in` is optional
  - known values:
    - `split`
    - `tab`
- `line` is optional and 1-based
- `column` is optional and 1-based

Other built-in editor actions:

- `action_type = yank_text`
  - `payload.text` is required
  - frontend writes it into the main editor register path

- `action_type = edit_path`
  - `payload.request_id` is required
  - `payload.path` is required
  - `payload.line` is optional and 1-based
  - frontend must later reply through `handler_message(message_type=action_result, ...)`

## Component Diagram

```mermaid
flowchart TB
    subgraph Frontend
        FE1[Notebook buffers]
        FE2[Client views]
        FE3[Terminal windows]
        FE4[Completion UI]
    end

    subgraph Interfaces
        I1[Protocol parser]
        I2[ProtocolServer]
        I3[Envelope/event encoder]
    end

    subgraph Application
        A1[Start/Attach/Reconnect]
        A2[ExecuteCell]
        A3[HandlerMessage]
        A4[Interrupt/Shutdown/Stop]
    end

    subgraph Infrastructure
        R1[Managed runtime]
        R2[Client runtime support]
        R3[Handler controller]
        R4[Plugin runtime bridge]
        R5[Client view builder]
    end

    subgraph Plugins
        P1[jusi_vd]
        P2[jusi_sql + providers]
        P3[jusi_shell]
    end

    FE1 --> I1
    FE2 --> I1
    FE3 --> I1
    FE4 --> I1
    I1 --> I2
    I2 --> A1
    I2 --> A2
    I2 --> A3
    I2 --> A4
    A1 --> R1
    A2 --> R1
    A2 --> R2
    A2 --> R3
    A3 --> R3
    A3 --> R4
    A4 --> R1
    A4 --> R2
    R3 --> P1
    R3 --> P2
    R3 --> P3
    R5 --> I3
    I3 --> FE1
    I3 --> FE2
    I3 --> FE3
    I3 --> FE4
```

## Flow: `print("lalala")`

This is the simplest kernel-owned code-cell path.

```mermaid
sequenceDiagram
    participant F as Frontend
    participant B as Backend Root
    participant K as Kernel
    participant C as Client State

    F->>B: execute_cell(code)
    B->>B: allocate client_id
    B->>B: activate client + set busy
    B-->>F: cell_updated(status=busy, owner=kernel, client_id=...)
    B->>K: execute_request
    K-->>B: iopub stream(stdout="lalala\\n")
    B->>B: append transcript event
    B-->>F: client_updated(revision++)
    F->>B: inspect_client(client_id)
    B-->>F: client view lines with stdout
    K-->>B: idle / execute done
    B->>B: set execution status done
    B-->>F: cell_updated(status=done)
```

Important points:

- no separate handler controller path
- no plugin runtime
- all execution authority stays in the Jupyter kernel path

## Flow: One Active Live Client

Example:

- `%%shell bash`
- `%%vd`
- `%%sql ...`

```mermaid
sequenceDiagram
    participant F as Frontend
    participant B as Backend Root
    participant K as Kernel
    participant C as Client State
    participant W as Handler Controller
    participant T as Terminal Window
    participant P as Plugin Runtime

    F->>B: execute_cell(magic cell)
    B->>B: activate client + busy
    B->>K: execute_request
    K-->>B: handoff mime
    B->>W: start controller(handler_id, handoff payload)
    W->>B: publish native_terminal transport
    W-->>B: handler status update(status=follow-up)
    B-->>F: cell_updated(owner=handler, status=follow-up, transport=native_terminal)
    F->>T: launch attach_cmd in editor terminal
    T->>P: exec plugin-runtime
    Note over P: visible terminal output only through terminal surface
    F->>B: handler_message(followup/complete)
    B->>W: route live handler message
    W->>P: plugin runtime control request
    P-->>W: control response
    W-->>B: complete_result or other handler callback
    B-->>F: handler_message(...)
```

Important points:

- fullscreen interaction is not on the notebook control channel
- `handler_message` stays a control channel
- the active client is still a normal Jusi client with a `client_id`

## Flow: Two Active Live Clients

Example:

- one live `%%vd`
- one live `%%shell`

```mermaid
flowchart LR
    F[Frontend]
    B[Backend Root]

    subgraph Client A
        CA[client-A]
        WA[controller-A]
        PA[plugin-runtime-A]
    end

    subgraph Client B
        CB[client-B]
        WB[controller-B]
        PB[plugin-runtime-B]
    end

    F <-->|protocol events + requests| B
    B --> CA
    B --> CB
    B --> WA
    B --> WB
    WA <-->|control| PA
    WB <-->|control| PB
```

Operational meaning:

- one session may supervise multiple live clients
- each live handler-owned client has:
  - its own `client_id`
  - its own handler controller
  - usually its own terminal/runtime surface
- follow-up/completion target the active `client_id`
- shutdown/interrupt are also routed per client/cell ownership

## Ownership Rules

### Backend Root Owns

- session lifecycle
- cell status truth
- runtime supervision
- handler supervision
- protocol routing
- healthchecks
- disconnect timeout logic

### Frontend Owns

- Vim rendering
- sign updates
- buffers and windows
- terminal window creation
- completion UI application
- built-in callback actions like `open_path`

### Handler Controller Owns

- plugin-side control semantics
- interpretation of `followup`
- interpretation of `complete`
- per-plugin interaction policy

### Plugin Runtime Owns

- live interactive process state
- shell state
- VisiData session state
- plugin-local terminal interaction

## Non-Goals

These are intentionally not part of the map:

- arbitrary Vimscript/Lua injection from backend
- fullscreen terminal bytes over `handler_message`
- frontend-side plugin implementations
- automatic remote-backend -> local-machine transfer hops

## Reading Order

If you are trying to understand a bug quickly:

1. determine whether it is:
   - kernel-owned
   - handler-controller-owned
   - plugin-runtime-owned
2. determine whether the problematic link is:
   - frontend <-> backend protocol
   - backend <-> kernel
   - backend <-> in-process handler controller
   - handler controller <-> plugin runtime
   - plugin runtime -> backend callback
3. then inspect the matching module family:
   - `interfaces/` for protocol/server
   - `application/` for orchestration
   - `infrastructure/runtime.py` for runtime/client supervision
   - `infrastructure/inprocess_handler_controller.py` for live handler control
   - plugin repo/package for runtime-specific behavior
