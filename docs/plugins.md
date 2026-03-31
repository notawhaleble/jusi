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

The plugin is responsible for:

- claiming a cell
- starting its own display handler logic
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

1. how the plugin claims a cell
2. which display handler the plugin starts with
3. which frontend/backend commands the plugin needs
4. which follow-up and completion semantics it opts into or customizes

You should not have to redesign session lifecycle, reconnect policy, prepared-client handling, or supervision.

## Public Contract Draft

### `MagicCommand`

Defines how a plugin claims a cell.

Responsibilities:

- identify the plugin entry, for example `%%oc`
- parse initial plugin-specific arguments/config
- return a plugin match/config object for execution

### `DisplayHandler`

This is the real plugin runtime unit.

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

### `HandlerContext`

Core-owned context passed to the display handler.

Minimum responsibilities:

- session metadata
- active client identity
- structured event emission helpers
- structured frontend-command helpers
- subprocess/runtime helper seams
- status publication helpers

This context should stay transport-agnostic and Vim-agnostic.

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
- prepared-client lifecycle
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

1. define `MagicCommand`, `DisplayHandler`, `HandlerContext`, and `FrontendChannel`
2. implement plugin discovery/loading
3. add one tiny transcript-oriented plugin to prove the minimal path
4. add first-party reusable handler bases, especially a VisiData-oriented base
5. only then add larger first-party plugins like `%%vd`, `%%sql`, or `%%oc`

This keeps the architecture explicit before any one plugin starts defining the whole system accidentally.
