# ADR 0037: Bundled VisiData Uses the Plugin Boundary

Total data-size limits described below are superseded by
[ADR 0039](0039-user-data-is-not-a-control-frame.md).

- Status: accepted
- Date: 2026-09-09

## Decision

`%%vd` is bundled under `jusi.plugins.vd` and discovered through the same
`jusi.plugins.v1` entry-point group as external providers. Its exact provider ID
is `jusi_vd`, family ID is `visidata`, and magic is `vd`. Package and provider
versions agree. VisiData is optional (`jusi[vd]`); catalog discovery and kernel
startup do not import it. Missing VisiData fails the attempted client launch with
an installation message, while the kernel remains usable.

The kernel evaluates the cell body as one Python expression in its current user
namespace. Its result becomes a bounded, data-only snapshot in the ordinary exact
provider handoff. No code, import reference or pickle is decoded by the worker or
terminal application. Python containers preserve nesting and non-string mapping
keys. Scalars include bytes, dates/times, timedeltas, Decimal and nonfinite floats.
NumPy arrays/scalars convert to their supported Python values. pandas DataFrame
and Series values become tables with an index column, ordered columns (including
duplicate names), and row values. pandas timestamps/timedeltas retain their full
precision as display strings. This is a browsing snapshot, not a reconstructed
pandas object with its complete dtype metadata or a live kernel reference.

Cycles, custom objects, unsupported data types, excessive depth/node count and
oversized values fail visibly before publishing a handoff. Encoded snapshots are
bounded to 768 KiB, with conservative allocation accounting while traversing data;
selecting a smaller value is the current large-data path. A larger artifact
transport would be a separate contract. Legacy behavior evidence is
`../jusi-0.x/src/jusi_vd/`; its pickle and environment-payload mechanism is not
retained.

The worker owns a private directory and 0600 snapshot file for its terminal
application, validates the data, and requests a normal geometry-gated terminal.
The application consumes and removes that file; worker cleanup removes the owned
directory idempotently. These are target-side implementation details. No path
crosses into the frontend. The worker imports neither IPython nor VisiData;
VisiData and its configuration load only in the terminal application.

## Interaction

The client declares `execute` and `editor_actions`. Legacy `BaseVdHandler` declared
followup but implemented a no-op; the new provider does not advertise it or
create misleading history. It does not claim plugin completions or a core
interrupt hook for arbitrary VisiData-internal work. Normal VisiData terminal
interaction stays application-owned. `JusiClose` performs full client cleanup.

VisiData `zY` captures the current displayed value. Selection clipboard commands
capture display values as tab-separated columns and newline-separated rows, as in
the legacy integration. Ctrl-O captures the current value as text and opens it in
an independent Neovim split. Copy/open helpers run after capture on a VisiData
background thread and report success only after editor acknowledgment (ADR 0036).
Failures are shown inside the still-usable application. Ctrl-O is snapshot-open;
it does not invoke an external editor, update the original value or imply
writeback. User VisiData configuration/plugins load before these Jusi bindings.

## Verification

Tests cover snapshot round trips and rejection, pandas/index/duplicate-column
handling, process import boundaries, private-file cleanup, VisiData key hooks,
and application sheet construction. The real Neovim scenario exercises bundled
discovery, expression evaluation in the kernel namespace, VisiData rendering,
`zY`, Ctrl-O, source close, exported-buffer independence and fresh client reuse.
The independent plugin-development skill evaluation is reserved for the planned
new `%%todo` plugin, rather than inferred from this bundled implementation.
