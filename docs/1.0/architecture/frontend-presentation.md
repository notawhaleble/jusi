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
