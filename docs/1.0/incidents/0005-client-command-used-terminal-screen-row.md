# Incident 0005: Client Close Used A Terminal Screen Row As A Notebook Row

- Status: reproduced and fixed in Jusi 1.0 development
- Observed in: the central SQLite/VisiData walking fixture
- Date: 2026-09-02

## Symptom

After exiting VisiData, starting another client, and invoking
`:JusiCloseClient` from its terminal, Jusi repeatedly reported that the cursor
was not inside a cell. Moving the VisiData cursor among table cells did not
help.

## Confirmed Cause

The terminal projection carried both its notebook and exact client identities,
so Jusi correctly found the owning frontend session. Client selection then
ignored that projection identity and passed the terminal screen's cursor row to
the notebook model. A VisiData table row is not a notebook-text coordinate.

The error could appear to work accidentally near the top of a small terminal
when its screen row happened to overlap a notebook cell's line range.

## Correction And Regression Requirement

When `close_client` is invoked from a client projection, it selects the exact
`b:jusi_client_id` attached to that buffer. When invoked from the notebook, it
continues to select clients through the model cell under the notebook cursor.
An explicitly supplied client ID remains authoritative in either context.

Frontend and real Neovim tests place the cursor on a terminal row outside the
notebook's range and require the exact client close request. Plugin-owned table
coordinates never enter the notebook model.
