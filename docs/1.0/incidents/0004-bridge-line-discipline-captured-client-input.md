# Incident 0004: Bridge Line Discipline Captured Interactive Client Input

- Status: reproduced and fixed in Jusi 1.0 development
- Observed in: the central SQLite/VisiData walking fixture
- Date: 2026-09-02

## Symptom

After manually focusing the VisiData terminal and entering Neovim terminal
input mode, ordinary VisiData keys such as `h`, `j`, `k`, `l`, and `q` appeared
as text on the screen instead of acting immediately. Enter was the only visibly
functional key. Ctrl-C ended the local terminal view, while the backend client
remained active; another execution therefore created a second client for the
same cell.

The same run displayed a VisiData warning containing `FileExistsError` while
initializing its isolated state directory.

## Confirmed Cause

The per-surface bridge read its local Neovim-terminal stdin without first
placing that TTY in raw mode. The local line discipline therefore echoed and
buffered ordinary bytes until Enter and interpreted Ctrl-C as a signal for the
bridge. Ctrl-C killed the frontend transport process, not the target-side
VisiData process.

The surviving backend client was correct under the attachment contract:
transport loss alone does not prove target application or client death.

The independent warning came from VisiData persistent-list initialization in
the isolated test fixture. Multiple lists could attempt to create the same
previously absent data directory.

## Correction And Regression Requirements

- The bridge saves its local stdin TTY attributes, selects raw mode before
  relaying input, and restores the attributes on all exit paths.
- Tests send single keys and Ctrl-C without Enter and require the exact bytes at
  the target boundary.
- Killing only the bridge retains the authoritative surface and client.
- When forwarded input makes the target application exit, core emits the typed
  terminal failure and retires its surface, worker, and client while the kernel
  remains on.
- The central VisiData fixture runs with its `nothing` option so it neither
  loads nor persists user state. This is fixture isolation, not production SQL
  plugin policy.

Automatic execution continues to leave editor focus in the notebook. Raw byte
transport does not imply automatic focus transfer.
