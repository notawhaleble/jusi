# ADR 0020: Cell Execution Uses One Uniform Artifact Interaction

- Status: accepted
- Date: 2026-09-04

## Context

The first frontend slice exposed backend distinctions directly. Ordinary code
required `JusiOpenOutput` and had no corresponding close operation, while a
plugin surface appeared automatically and required `JusiCloseClient`. Opening
ordinary output also moved focus despite execution not being a focus request.

Those distinctions are necessary inside core because bounded execution output
and a durable backend client have different owners and cleanup paths. They are
not useful as two user interaction models. From a cell, both are the current
execution artifact.

## Decision

The user-facing cell artifact interface has three operations:

- `JusiExecute` executes the cell and reveals the resulting artifact without
  moving focus away from the notebook.
- `JusiToggleFocus` moves from a source cell to its artifact, or from an
  artifact back to its source cell. If the artifact buffer is alive but has no
  window anywhere in the Neovim instance, it reopens that same buffer.
- `JusiClose` ends the current cell artifact and performs its required cleanup.

Closing an artifact's Neovim window with native window commands only hides its
projection. It does not close a backend client, stop a terminal bridge, or
discard ordinary output. This is why `JusiToggleFocus` can reopen it.

`JusiClose` is intentionally stronger. For bounded ordinary output it deletes
the terminal projection. For a plugin artifact it requests idempotent close of
the backend client; authoritative surface-close events then stop the bridge and
delete the terminal buffer. The kernel remains on.

There is at most one current execution artifact per model cell in the initial
frontend. Executing a cell that still owns plugin clients explicitly replaces
that artifact: Jusi closes those clients before submitting the new execution.
This also repairs multiple clients left by frontend versions predating this
rule. Continued SQL statements, shell commands, completions, and actions inside
a durable client remain client operations; an ordinary operation result never
implicitly ends that client.

Core continues to distinguish execution output, clients, surfaces, bridges,
and buffers. “Artifact” is the cell-oriented interaction abstraction, not a new
backend protocol resource or identity.

## Consequences

- `JusiOpenOutput` and `JusiCloseClient` are removed from the 1.0 command surface.
- Execution presentation and artifact cleanup are consistent across cell kinds.
- Presentation never steals focus merely because output or a surface appeared.
- Native window lifetime remains independent from backend resource lifetime.
- A future shortcut or mapping can bind the three operations without encoding
  output/client type checks.

## Verification

Headless frontend and end-to-end tests prove that:

- ordinary output appears in a terminal split while focus stays in the notebook
- a terminal plugin artifact also appears without stealing focus
- native window close hides but does not destroy an interactive artifact
- focus toggle reopens the same hidden buffer and returns to the source cell
- `JusiClose` deletes ordinary output or closes the exact cell-owned client
- client close leaves the kernel on

