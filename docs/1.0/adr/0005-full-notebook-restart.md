# ADR 0005: Restart Replaces The Full Notebook Runtime

- Status: accepted
- Date: 2026-08-31

## Context

The 0.x restart path preserves substantial notebook, backend, configuration, plugin, presentation, and client state. Configuration reload and plugin rediscovery therefore require separate features or a manual sequence of stop, buffer wipeout, reopen, start, and sometimes a complete editor restart.

Plugin installation and plugin-code changes are a primary reason users restart during normal development. An in-place Jupyter kernel restart does not provide the required product semantics.

## Decision

The user-visible restart operation is a full replacement of the notebook runtime. It is not an in-place `restart_kernel` call and does not reuse backend-derived notebook state.

Restart preserves user-authored editor state:

- current buffer text, including unsaved edits and textual history
- undo history
- the notebook buffer and, where practical, cursor/window placement
- user mappings and unrelated editor state

Restart discards and rebuilds runtime-derived state:

- the current kernel generation and kernel channels
- executions, pending input, interrupts, and completion requests
- clients, terminal attachments, plugin workers, and related child processes
- cell execution bindings, statuses, runtime presentation overrides, and backend-derived output associations
- notebook model cells and their extmarks; the current text is parsed again as a fresh model
- cached configuration, target resolution, plugin catalog, capabilities, palettes, and kernel-extension lists
- imported plugin code or discovery caches that could conceal an installed or edited plugin

The replacement sequence is:

1. fence the current kernel generation from accepting new work
2. stop and clean up all resources owned by that generation
3. discard the frontend's runtime-derived notebook model and projections without changing buffer text or undo history
4. reload configuration from canonical sources
5. rediscover plugins and rebuild capabilities in a fresh discovery/import process boundary where module caching would otherwise survive
6. parse the current buffer text into a new notebook model with new runtime cell identities
7. start a new kernel process with a new `kernel_id`
8. load freshly resolved kernel extensions and publish a new authoritative capability/plugin snapshot

No execution, client, worker, cell-runtime, or kernel identity survives restart.

## Failure Semantics

- If owned resources cannot be safely stopped or fenced, restart fails visibly and does not silently start a second replacement kernel.
- If teardown succeeds but configuration, discovery, or startup fails, the kernel is `off` and the failure identifies the originating layer and operation.
- Restart does not roll back to stale runtime state after a failed replacement.
- A future force-restart operation, if introduced, must explicitly report any resources it could not terminate.

## Consequences

- Restart is deliberately more expensive than an in-place kernel restart.
- Plugin and configuration development no longer requires buffer wipeout or editor restart.
- The service architecture must provide a fresh process boundary wherever Python import or entry-point caches could preserve plugin code.
- Stale events are rejected through replaced resource identities.
- The wire shape for restart must be introduced atomically with schema, Python, Lua, fixtures, and conformance tests; this ADR does not add that protocol command yet.
