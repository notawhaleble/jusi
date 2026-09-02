# Incident 0005: Projection Commands Used A Terminal Screen Row As A Notebook Row

- Status: reproduced frontend defect and fixed in Jusi 1.0 development
- Observed in: the central SQLite/VisiData walking fixture
- Date: 2026-09-02

## Symptom

After exiting VisiData and attempting to execute its notebook cell again,
`:JusiExecute` repeatedly reported that the cursor was not inside a cell.

An initial report was mistakenly interpreted as a failure of
`:JusiCloseClient`. Review found that close had the same underlying
projection-coordinate defect, but execution was the user-observed failure.

## Confirmed Defect

Jusi projections carry their owning notebook and model cell identities, and an
interactive projection additionally carries its exact client identity. Despite
that, `execute`, `open_output`, and part of `close_client` read the current
window's cursor row and passed it to the notebook model even when the current
buffer was a terminal projection. A VisiData table row is not a notebook-text
coordinate.

This failed reliably whenever the terminal screen row fell outside the source
cell's line range and could appear to work accidentally near the top of a small
terminal.

The complete target-exit path was also tested with the terminal focused. Once
surface cleanup finishes, Neovim returns to the notebook buffer, the cell model
and extmarks remain valid, and a no-argument execution succeeds. The original
manual sequence could therefore have issued the command while a projection was
still current during asynchronous cleanup; command correctness must not depend
on that timing.

## Correction And Regression Requirement

Cell-oriented commands invoked from a Jusi projection select its stored
`b:jusi_cell_id`. From notebook text they select the model cell under the
notebook cursor. `close_client` additionally selects a projection's exact
`b:jusi_client_id`, while an explicitly supplied client ID remains authoritative.

Frontend tests place a projection cursor on row 12 while its notebook has only
four lines and require execution of the stored model cell and closure of the
exact client. The real Neovim test focuses a terminal as its target exits, then
requires the surviving notebook model to execute again and create a new client.
Plugin-owned screen coordinates never enter the notebook model.
