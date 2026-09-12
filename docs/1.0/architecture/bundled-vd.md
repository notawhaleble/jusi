# Bundled VisiData

Install `jusi[vd]` in the target service environment. The provider is bundled with
Jusi; VisiData 3 is an optional dependency. Restart the notebook runtime after
installing/updating so discovery and the kernel load the current adapter.

A cell evaluates one expression in the current Python kernel namespace:

```python
%%vd
[{'name': 'Alice', 'score': 10}, {'name': 'Боб', 'score': 20}]
```

For a pandas table, first create it in a plain Python cell, then use:

```python
%%vd
df
```

Use `JusiToggleFocus` to enter the terminal. Normal VisiData navigation and data
inspection work there. `zY` copies the displayed cell to Neovim's unnamed/yank
registers. Ctrl-O opens the current value as text in a new Neovim split. Selection
clipboard commands send tab/newline-separated display values. The application
reports success only after editor delivery is acknowledged. `JusiClose` closes
the source client and terminal; a delivered export buffer survives and can be
saved with ordinary `:write /chosen/path`.

Each execution creates a fresh snapshot/client. No-op legacy followups are not
advertised. This provider does not add notebook-level completion or interruption
of arbitrary work started inside VisiData. Ctrl-O opens a snapshot; it does not
edit the kernel value or write data back to its source.

Supported snapshots include standard Python containers/scalars, dates, Decimal,
bytes, NumPy data convertible to supported Python values, and pandas
DataFrame/Series tables. Table columns and index values are retained, including
duplicate column labels. pandas time values retain precision as text; complete
pandas dtype/object metadata and live object identity are not reconstructed.
Custom classes and cyclic values require explicit conversion by the caller.
Snapshots and copy/open have no total byte ceiling. Snapshots move through
private target-side artifacts; copy/open streams content to Neovim in chunks.
The data still needs memory in VisiData and the destination buffer/register.
Structural depth validation remains. See [ADR 0039](../adr/0039-user-data-is-not-a-control-frame.md).

User VisiData configuration and plugins load in the target application process.
The integration installs its copy/open hooks afterward. No VisiData code is
imported in the service, discovery provider, worker or kernel adapter.

See [ADR 0037](../adr/0037-bundled-visidata-provider.md),
[ADR 0036](../adr/0036-application-driven-editor-actions.md), and
[Incident 0014](../incidents/0014-embedded-visidata-startup-and-editor-actions.md).
