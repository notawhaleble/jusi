# Frontend Notebook Model

## Boundary

The frontend notebook model is local editor state. It has no backend, kernel, client, execution, or transport dependency. Text determines structure; the model owns runtime cell identities; extmarks only anchor model resources to changing text.

## Parser

`lua/jusi/notebook/parser.lua` is a pure Lua state machine over exact lines. It recognizes only the accepted symbol-only delimiters and returns cells, active-body/history ranges, and local structural diagnostics.

Every opener is a recovery boundary. Encountering an opener while another cell is open finishes the earlier cell as invalid and begins the next cell. A malformed cell therefore cannot consume later cells.

## Live Model

`lua/jusi/notebook/init.lua` attaches the parsed model to a Neovim buffer.

- Cell IDs are opaque, monotonic within one notebook-model generation, and distinct from extmark IDs.
- Structural extmarks cover delimiter text. Invalidating the opener retires its identity under the current conservative policy; damaging the closer or history preserves identity while changing text validity. Undo or later insertion cannot restore an actually retired cell identity.
- Cells form a locally spliced linked model. Ordinary or structural edits do not rebuild or reindex an array of the whole notebook.
- Ordered enumeration is explicit and is not used by the edit callback.
- Active body and history text are read from current extmark-anchored ranges only when requested.

## Edit Paths

A non-structural edit whose owning cell still has exact, live delimiter anchors:

1. resolves the nearest opener anchor
2. validates only that cell's structural anchors
3. increments that cell's text revision
4. performs no parse, backend work, or whole-notebook enumeration

A structural or ambiguous edit reparses from the affected opener to the next surviving opener. The next opener is both the grammar recovery point and the reconciliation boundary. Only cells in that linked segment are retired, reused, or inserted. The old
segment begins at the model successor of the left boundary, including cells
whose deleted opener extmarks have disappeared; it must not begin at the first
surviving opener inside the region (Incident 0012).
Reconciliation is deferred beyond `on_lines` so Neovim undo finishes adjusting
extmarks before replacement anchors are created. Public model queries flush
pending work; `flush()` also allows explicit synchronization for measurement.
Unclosed cells remain addressable up to the next opener or EOF, allowing focus,
interrupt, and close while body submission is blocked.

See [ADR 0024](../adr/0024-cell-identity-survives-invalid-closing-structure.md)
and the [structural edit policy](cell-structural-edits.md) for guarantees and
opener-retirement cleanup, merge/undo rules, and remaining explicit-move decisions.

Initial load and explicit reload are the only normal full-parse paths. Reload creates a new model generation rather than preserving runtime cell identities through unknown wholesale text replacement.

## Verification

`tests/frontend/notebook_spec.lua` covers grammar, malformed recovery, active body/history extraction, identity preservation, identity retirement, partial delimiter edits, and neighboring-cell isolation.

`tests/frontend/benchmark.lua` uses a 10,000-line/1,000-cell notebook and asserts the budgets in `docs/1.0/performance-budgets.md`, including a maximum structural scan of one neighboring recovery region and no full parse during edit workloads.

Retired cell IDs are delivered asynchronously to notebook-runtime orchestration,
which shares the full close path with `JusiClose`. The model performs no backend
work; a retired opener closes its resources under ADR 0025.

Cell editing uses the same model identities and local change notifications. A
magic-header revision records changes independently from ordinary body edits,
so an alias edited away and back cannot accept a stale provider attribution.
Whole visible cells feed the isolated local language worker; its syntax spans
are extmark projections, not structural truth. See [ADR 0030](../adr/0030-cell-local-syntax-and-indentation.md).
