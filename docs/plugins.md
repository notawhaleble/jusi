# Plugin Contract

This document defines the backend contract for Jusi display-handler plugins.

## Overview

A plugin has two backend-facing pieces:

- a kernel-side magic implementation that emits a Jusi handoff payload
- a display-handler implementation controlled by backend runtime and, when needed, a plugin runtime

Core backend responsibilities:

- session lifecycle
- client lifecycle
- handler supervision
- structured frontend/backend routing through `handler_message`
- native-terminal transport advertisement for interactive handlers

Plugin responsibilities:

- claim one or more magic names
- emit a valid handoff from the kernel side
- implement handler interaction semantics
- define any plugin-specific follow-up or completion behavior

## `MagicCommand`

`MagicCommand` declares the magic names a handler can serve, for example `%%vd` or `%%sql`.

Important:

- magic name and handler id do not need to match
- multiple handlers may claim the same magic name
- final routing is decided by validated kernel handoff, not by frontend header parsing

## `DisplayHandlerSpec`

`DisplayHandlerSpec` is the backend registration unit for a handler.

Core fields:

- `handler_id`
- `factory`
- `magic_commands`
- `handoff_validator`
- `kernel_extension_modules`
- `presentation`
- `family_presentation`

The registry uses these specs to:

- load plugin handlers from `jusi.display_handlers`
- advertise session-level metadata
- validate handoff payloads
- start the correct handler control path after execution

## `DisplayHandler`

A display handler owns one active handler-controlled cell/client pair for its lifetime.

Handler responsibilities:

- start plugin interaction
- process frontend-originated plugin messages
- emit frontend events or action requests
- expose handler snapshot state
- handle interrupt and stop

Handler lifecycle rules:

- normal handler exit maps to cell status `done`
- unexpected handler death maps to cell status `error`
- interrupt routing depends on execution owner kind

## `HandlerContext`

`HandlerContext` is the core-owned runtime context passed into a handler.

It provides:

- active notebook/session/client/cell identity
- resolved `magic_name`
- raw handoff content and metadata
- event emission helpers
- frontend action helpers
- backend action helpers
- status and transport publication helpers

The context stays transport-agnostic and Vim-agnostic.

## Base Classes

The supported base classes are:

- `BaseHandler`
- `BaseTerminalHandler`
- `BaseVdHandler`
- `BasePluginRuntimeVdHandler`

Use them as convenience layers, not as protocol replacements.

Purpose:

- `BaseHandler` defines the handler-facing hook shape
- `BaseTerminalHandler` advertises native-terminal transport and terminal startup
- `BaseVdHandler` adds VisiData-oriented terminal/bootstrap behavior
- `BasePluginRuntimeVdHandler` forwards `followup` and `complete` requests to the live plugin runtime

Common VisiData yank/open/edit behavior does not live on the handler base anymore.
It is installed centrally by core `plugin-runtime` bootstrap so VisiData-based
plugins get it automatically.
Plugin runtimes can also emit backend runtime records such as execution status
updates through `jusi.visidata_support.set_plugin_execution_status(status)`.

The plugin runtime bootstrap also installs generic `BaseSheet.jusi_followup`
and `BaseSheet.jusi_complete` helpers. Simple VisiData plugins can bind their
runtime object with `jusi.visidata_support.bind_visidata_runtime()` and handle
requests through `handle_followup(payload)` / `handle_complete(payload)`.
Complex plugins may override plugin-runtime request dispatch when their active
mode is not a normal VisiData sheet workflow.

## Session Metadata

Core may publish three plugin-related metadata surfaces.

### `session.plugin_specs`

`session.plugin_specs` describes broad pre-execution editor defaults keyed by magic name.

Use:

- syntax selection
- indentation selection
- follow-up support
- completion support

`family_presentation` feeds `session.plugin_specs`. If `family_presentation` is omitted, backend falls back to `presentation`.

Example:

```python
DisplayHandlerSpec(
    handler_id="sqlite",
    magic_commands=(MagicCommand("sql"),),
    family_presentation={"syntax": "sql", "indent": "sql", "followup": True, "completion": True},
    presentation={"syntax": "sqlite", "indent": "sql", "followup": True, "completion": True},
)
```

Rules:

- keys are magic names
- values are family-level defaults
- provider-family plugins should keep these values provider-neutral

### `cell.presentation`

`cell.presentation` is optional cell-level metadata published after a concrete handoff is resolved.

Use:

- provider- or handler-specific syntax selection after execution

Rules:

- starts from the matched handler spec `presentation`
- may be overridden by handoff metadata
- is authoritative for that executed cell when present

### `session.palette`

`session.palette` is the backend-owned frontend creation palette keyed by magic name.

Use:

- plugin cell discovery
- plugin cell creation commands such as `:J`

Rules:

- section names match claimed magic names exactly
- every installed plugin family should appear
- plugins without config-backed aliases use `entries: []`
- config-backed entries come from `session.target.config[magic_name]`
- multiple providers claiming the same magic contribute to the same section
- entry order follows the delivered session config order

Example:

```json
{
  "palette": {
    "vd": {"entries": []},
    "shell": {"entries": []},
    "sql": {"entries": ["analyticsdb", "mysqlitedb"]}
  }
}
```

## Handler Channel

The structured plugin control channel is `handler_message`.

Directions:

- frontend -> backend
  - `followup`
  - `complete`
- backend -> frontend
  - `action_request`
  - handler-defined events

This channel is for control semantics, not fullscreen terminal rendering.

## Native Terminal

Interactive handlers should advertise terminal attachment through client transport metadata:

- `transport.kind = native_terminal`
- `transport.attach_cmd`
- `transport.attach_env`
- `transport.session_id`
- `transport.client_id`
- optional `transport.handler_id`

The attach bridge is:

- `python -m jusi client-process terminal-attach`

## Kernel Handoff

Plugins should register real kernel extension modules and emit a Jusi handoff payload from the kernel side.

The validated handoff decides:

- handler identity
- resolved magic family
- handler startup payload
- cell-level presentation overrides when needed

Frontend should not infer concrete plugin/provider identity from cell headers or local config.
