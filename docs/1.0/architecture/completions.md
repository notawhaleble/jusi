# Completion requirements for the next slice

Status: implemented; wire contract and menu behavior are specified in
[ADR 0027](../adr/0027-completions-use-explicit-source-ranges.md). Manual user
testing is pending.

Regular cells complete in their Jupyter kernel scope. Send the entire cell body
before the cursor as completion context. Text after the cursor must neither
influence completion nor be removed or rewritten, including adjacent suffixes:

```
from time import s|lalala
from time import sleep|lalala
```

Plugins need body/cursor context and must control the replacement extent rather
than inherit a core-wide Vim word heuristic. Distinguish small `word`, whitespace
delimited `WORD`, and the full body prefix. Explicit coordinate units and ranges
must make Unicode, multiline context, and before-cursor replacement unambiguous.
Core must validate ranges and reject stale responses after text/cursor/cell/client
changes. Menu selection previews and acceptance must preserve the untouched suffix;
cancellation must restore the original prefix. Use Vim's native menu and default
input behavior, with Tab only invoking completion. Earlier-line changes are
finalized on acceptance; their current-line portion previews natively.

Allow empty-prefix requests. A SQL plugin must be able to offer schemas after
`select * from ` and then tables after selecting a schema. Core must not reject
requests globally just because the current word is empty.

Review the read-only sibling `../jusivim` implementation and its tests before
implementation. Initial inspection found `handler_followup_payload` in
`autoload/jusi/session.vim` and `apply_completion_result`, which consumes item
`value`, `label`, `detail`, `documentation`, `kind`, `start_col`, and `end_col`.
The legacy request supplies `cell_text`, cell metadata/main lines, `cursor_row`,
`cursor_col`, `line_text`, and `current_word`; the frontend also retains
cell/client/handler identity and line snapshots.
These are use-case evidence, not a wire contract to copy. In particular, inspect
range preview, cancellation, stale response handling, word-vs-WORD and suffix
regressions before choosing the 1.0 payload.
