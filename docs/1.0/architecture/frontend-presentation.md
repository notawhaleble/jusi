# Frontend Output Presentation

## Boundary

Presentation is a projection of execution output, never the owner of execution,
client, cell, or kernel truth. It receives an already correlated model cell ID
and output record from the controller.

Renderer choice depends on `media_type` and interaction needs. It does not
depend on whether output came from ordinary code, an error, or a plugin.

## Initial Text Renderer

Every `text/*` record is sent unchanged to the channel returned by
`nvim_open_term()`. Jusi does not remove, interpret, or recreate ANSI sequences.
This surface is non-interactive: it has no process, PTY, stdin, resize protocol,
or kernel-control responsibility.

The initial manager retains at most one output terminal per cell. A new
execution replaces and closes only that cell's previous terminal. Other cell
surfaces remain intact. Completed output stays available until replacement or
explicit frontend cleanup.

The terminal buffers are initially hidden. Window placement, focus, mappings,
and user commands are a later UI slice and do not alter renderer ownership.

## Deferred Media

No renderer is yet registered for non-text media. Such output produces a local,
cell-scoped `frontend_presentation/unsupported` failure without changing kernel
or transport state. Rich renderers will be added by media type rather than by
cell or plugin identity.

## Durable Plugin Client Surfaces

Execution-output projection is distinct from a durable plugin client surface.
Backend plugins expose generic terminal or web surface resources; they do not
send provider-specific presentation models to Lua.

- A render-only terminal surface sends bytes to `nvim_open_term()`.
- An interactive terminal surface additionally binds a dedicated target-side
  PTY/byte stream, input, and geometry updates.
- A web surface opens backend-owned content in a supported WebBuffer/browser.

The frontend owns buffer/window placement, focus, current geometry, input
routing, and explicit close. The backend owns application state and rendered
content. Fatal surface/client failures arrive through generic core failure
events; recoverable application errors remain content on the plugin surface.

The terminal surface projection is now implemented. `surface.created` opens a
native Neovim terminal split at its real geometry and starts the configured
repository bridge as a terminal job. The bridge command receives only the
configured target service URL and core `surface_id`; it never receives a
plugin-provided command, environment, or payload. Target bytes therefore flow
through Neovim's terminal emulator, while plugin application state and the PTY
remain at the target service.

`terminal_bridge_command` defaults to `{ "jusi", "terminal-bridge" }`. When a
local `service_command` ends in `serve`, setup derives the bridge command from
the same executable unless explicitly overridden. Remote service placement
does not change the bridge executable's local ownership.

Authoritative health reconciliation creates missing projections and removes
stale ones without duplicating existing terminal jobs. A `surface.closed` or
session teardown stops only that bridge job and deletes its buffer. Automatic
reattachment after a bridge failure is deferred until the frontend can persist
the exact consumed byte cursor; it must never resume from a guessed position.
