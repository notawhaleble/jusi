# Jusi 1.0 Notebook Format

## Status

This is the accepted initial native 1.0 text format. It intentionally differs from the legacy `##` format used by `jusivim` 0.x.

The canonical filename extension remains `.vipynb`. Jusi 1.0 assigns these
buffers the Neovim filetype `jusi`; it does not reuse the behavioral meaning of
the legacy `jusinb` filetype.

The shared extension does not imply grammar compatibility. If a buffer has
exact legacy `##` delimiters and no native structural lines, Jusi 1.0 identifies
it as a 0.x notebook and refuses to attach or start a service. Explicit
conversion behavior is still deferred. A `##` line inside a notebook containing
native structure is ordinary cell or surrounding text, not a legacy delimiter.

## Cell Boundaries

Cells use symmetric, symbol-only boundary lines:

```text
╭──
print("hello")
╰──
```

- `╭──` opens a cell.
- `╰──` closes a cell.
- Boundary lines are structural and are excluded from execution.
- A boundary is recognized only when the complete line matches exactly at column zero.
- Cells cannot nest.
- An empty cell is valid.

The start boundary anchors the cell's runtime model identity through an extmark. The closing boundary validates its range. Extmarks do not define cell semantics and are not persisted as notebook structure.

## History Region

History is an optional suffix inside the cell box:

```text
╭──
%%sql main
select *
from current_orders;
╞══
select count(*) from orders;
├┄┄
select *
from orders
limit 20;
├┄┄
select *
from orders;
╰──
```

- `╞══` begins the history region and ends the active body.
- `├┄┄` separates history entries.
- `╰──` ends the final history entry and the cell.
- Entries are stored newest first.
- Magic history stores the submitted body without the active `%%...` header.
- The history region and all structural lines are excluded from execution.

The entry ranges in the example are:

```text
first entry  = after ╞══ until the first ├┄┄
second entry = after the first ├┄┄ until the next ├┄┄
last entry   = after the last ├┄┄ until ╰──
```

Capture, folding and restoration are implemented under [ADR 0031](adr/0031-foldable-followup-history.md).

Neovim may add readable virtual text such as a history count or replace a folded history suffix with a summary. That presentation is not stored in delimiter lines.

## Structural Recovery

Every opening boundary is also a parser recovery point. Given:

```text
╭──
unclosed cell

╭──
valid next cell
╰──
```

the second `╭──` diagnoses the previous cell as unclosed and begins a new cell. The parser must not consume the rest of the notebook while searching for a distant close.

Initial malformed cases include:

- `╰──` without a matching opener: orphan close
- `╭──` before the current cell closes: unclosed previous cell, then recovery
- `╞══` outside a cell or repeated inside one cell: invalid history boundary
- `├┄┄` before `╞══`: invalid history separator
- end of file inside a cell: unclosed cell

Diagnostics and execution blocking remain local to affected cells or text regions. Malformed notebook structure does not change kernel state or poison unrelated cells.

## Incremental Model

Text is the durable, reconstructable authority for structure. The in-memory notebook model is the operational authority while editing.

- ordinary body edits update only the owning cell
- structural edits reconcile only the bounded region around changed boundaries
- extmarks move existing cell anchors when earlier text changes
- projections update only changed cells
- full-buffer parsing is for initial load or exceptional recovery, not ordinary typing

## Open Format Decisions

- representation of a literal body line exactly equal to one of the reserved structural lines
- whether blank text outside cells is allowed or diagnosed
- explicit conversion behavior from legacy `##` notebooks

Source-only Jupyter import/export is described in [notebook conversion](notebook-conversion.md).
