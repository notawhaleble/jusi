# Plugin Architecture Draft

This document defines the first concrete direction for Jusi handler/plugin support.

The goal is to preserve MVP-level flexibility without letting plugin-specific behavior leak into the backend core session model.

## Core Idea

A plugin has at least two parts:

- magic command definition
- display handler

The backend core is responsible for:

- incorporating plugin execution into normal Jusi session/client lifecycle
- plugin discoverability/loading
- status consistency
- a structured communication channel between plugin backend code and frontend code
- spawning and supervising one handler worker process per active handler-owned cell/client

The plugin is responsible for:

- providing the kernel-side magic handoff shape
- starting its own display handler logic inside the worker process
- interpreting plugin-specific commands and follow-up actions
- deciding when its interaction mode changes, for example from a VisiData-like table flow into a shell terminal flow

## Why This Model

The MVP showed that plugin behavior is broader than “cell execution adapter” or “renderer kind”.

Examples:

- `%%pplx` fits the current transcript/view pipeline well
- `%%sql` wants reusable VisiData-oriented follow-up and completion behavior
- `%%oc` starts as a VisiData-oriented workflow and may later transition into an interactive shell session while still needing plugin-defined frontend/backend commands such as container copy

So the stable split should be:

- core owns lifecycle, supervision, consistency, and channel plumbing
- plugin owns interaction semantics through its display handler

## Plugin Author Mental Model

If you are adding a new plugin, you should mainly think about:

1. what kernel-side magic handoff payload the plugin emits
2. which display handler worker class the plugin starts with
3. which frontend/backend commands the plugin needs
4. which follow-up and completion semantics it opts into or customizes

You should not have to redesign session lifecycle, reconnect policy, client allocation, or supervision.

## Public Contract Draft

### `MagicCommand`

Defines how a plugin claims a cell.

Responsibilities:

- identify the plugin entry, for example `%%oc`
- parse initial plugin-specific arguments/config
- emit a Jusi handoff mime payload that tells backend which handler worker to start

Important:

- magic name and handler id do not have to match
- this is required for cases like `%%sql`, where one magic may route to different handlers/providers

### `DisplayHandler`

This is the real plugin runtime unit inside a dedicated worker process.

Responsibilities:

- start plugin-owned interaction
- receive frontend-originated plugin messages
- emit plugin state changes and action requests through the core channel
- expose runtime snapshot/state
- handle interrupt and stop

Important:

- the display handler is not limited to one fixed UI mode
- it may transition between modes as part of plugin logic
- for example a plugin may begin in a VisiData-like table flow and later switch into a shell/terminal interaction flow
- one handler worker owns exactly one active cell/client for its whole lifetime
- normal worker exit should be interpreted by core as cell status `done`
- unexpected worker death should be interpreted by core as cell status `error`

### `HandlerContext`

Core-owned context passed to the display handler.

Minimum responsibilities:

- session metadata
- active client identity
- active cell identity
- structured event emission helpers
- structured frontend-command helpers
- subprocess/runtime helper seams
- status publication helpers

This context should stay transport-agnostic and Vim-agnostic.

Expected startup identity includes:

- `notebook_id`
- `session_id`
- `client_id`
- `cell_id`
- `handler_id`
- explicit `magic_name`
- raw kernel handoff payload and metadata

### `FrontendChannel`

Core-owned structured channel between plugin backend and frontend.

This exists because some plugins need more than plain output rendering.

Examples:

- plugin-defined follow-up requests
- plugin-defined completion requests
- command dispatch from plugin runtime into frontend action bindings
- frontend replies back into plugin runtime
- terminal-side escape or signal bridges normalized into structured channel messages

The goal is to make “weird but useful” flows explicit architecture instead of accidental hacks.

## Status And Consistency Rules

Core still owns:

- session state
- active client lifecycle
- active execution ownership
- disconnect/reconnect/stop semantics
- child-process supervision
- teardown on backend root-process loss

Plugins must fit inside that frame.

So even if a plugin is highly custom, the backend should still guarantee:

- one coherent active execution owner
- one coherent client lifecycle
- consistent stop/disconnect behavior
- visible failure/interrupt status
- observable plugin state through core-managed snapshots/events

## Reusable Handler Bases

The public plugin contract should stay small, but first-party reusable handler bases are still desirable.

Examples:

- `BaseDisplayHandler`
- `VDHandler`
- `TerminalHandler`

These are convenience layers, not the contract itself.

Why:

- `%%sql` may want shared VisiData follow-up and completion primitives
- `%%oc` may also want those same VisiData primitives for its early flow, then later transition into shell-like behavior
- other plugins may not want VisiData semantics at all

So VisiData support should likely be a reusable first-party handler base, not a global assumption in the backend core.

Current code status:

- backend registry now allows one magic to map to multiple handlers, with explicit handoff validation by `magic_name` and `handler_id`
- managed runtime now recognizes a first Jusi handoff mime shape from kernel output and surfaces it as a structured handoff event
- matched handler-owned executions now start in a dedicated `handler-worker` subprocess rather than running handler logic inside the backend root process
- backend root process remains the router/supervisor for worker/frontend traffic
- a reusable terminal-hosted handler base now exists in backend code and owns:
  - native-terminal transport preparation
  - terminal command/environment advertisement
  - handler-side terminal-oriented control hooks
- a reusable VisiData-oriented handler base now also exists on top of that terminal host and exposes shared hooks for:
  - copy
  - completion
  - follow-up
- current built-in `%%vd` now reuses that terminal-hosted base instead of owning PTY details directly
- current built-in `%%vd` now also reuses the VisiData-oriented base instead of defining those hook names ad hoc
- current built-in `%%vd` now does its first real job:
  - parse a kernel-side object expression from cell body
  - ask backend core to materialize that expression into a source file
  - advertise a native-terminal attach command that launches VisiData against that source
- the next handler-base work is about giving the shared VD hooks richer plugin-facing semantics, not about re-solving PTY lifecycle again

Next architecture tightening:

- one handler worker process now owns one active matched handler-owned cell/client
- every plugin client should assume native terminal as its frontend plane
- handler/frontend traffic should continue to route through the backend root process
- live plugin/runtime traffic should use a worker stdin/stdout protocol, not env/argv after startup
- validated kernel handoff now takes precedence for worker startup when present
- backend no longer activates workers directly from header parsing in `ExecuteCell`
- the in-memory runtime now emits synthetic handoffs for magic cells so the test/stub path still exercises the same worker-based activation model

Native-terminal pivot note:

- the earlier PTY-byte path proved the interactive/plugin model
- fullscreen interactive clients now pivot through native editor terminal buffers instead
- long-term terminal-hosted plugins should prefer:
  - backend-owned session/handler lifecycle
  - structured `handler_message` control semantics
  - native editor terminal rendering attached to a backend-provided bridge/client-process substrate

So the terminal-hosted handler base should evolve toward:

- starting and supervising the live interactive resource
- advertising a native-terminal attach substrate for the owning `client_id`
- keeping follow-up/completion/plugin commands on the structured handler channel

not toward indefinitely extending raw terminal rendering over the notebook control channel.

## First-Class Communication Use Cases

The architecture should explicitly support flows like these:

- plugin asks frontend to run a registered plugin command
- frontend routes that command through the structured channel back into plugin/backend code
- plugin backend uses helper functions or subprocess invocations to complete the action
- plugin runtime may also rely on in-band client signals, but those should be normalized through the core channel where possible

This is important for plugins like `%%oc`, where user actions can span:

- VisiData-like navigation
- custom backend helpers such as `oc cp`
- shell session entry through `oc rsh`

## What Core Should Not Do

Core should not:

- hardcode VisiData behavior into all plugins
- force one renderer taxonomy too early
- require plugin authors to reimplement supervision and session framing
- bake frontend-specific command names directly into backend core logic

## First Implementation Direction

The first implementation slice should focus on the contract, not on a large plugin set.

Recommended order:

1. define the kernel handoff mime contract
2. define the handler worker startup payload and stdin/stdout protocol
3. move live handler runtime into one worker process per active handler-owned cell/client
4. rebase first-party reusable handler bases, especially the VisiData-oriented base, onto that worker model
5. then rewrite concrete plugins like `%%vd`, `%%sql`, or `%%oc` against that worker model

This keeps the architecture explicit before any one plugin starts defining the whole system accidentally.
