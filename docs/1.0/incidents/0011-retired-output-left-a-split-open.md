# Incident 0011: Retired Output Left A Split Open

- Date: 2026-09-08
- Scope: frontend presentation cleanup

## Evidence

After cell deletion, the user observed that its output window remained open.
Both ordinary and interactive terminal cleanup deleted their output buffers
without explicitly closing windows displaying them. Neovim can substitute
another buffer and leave those splits behind.

## Correction

Before deleting a retired output buffer, close all windows currently displaying
that exact buffer. Recheck buffer identity before each close, including after
window autocmds. A former output window that now displays another buffer is
not owned by this cleanup. Hidden output still retires without window work.
If Neovim refuses to close its final window, retire the output buffer and let
Neovim replace its contents rather than exit the editor. Native window close
remains hide-only; backend lifecycle semantics are unchanged.

The shared helper covers ordinary output, interactive terminal retirement, and
terminal launch failure. Frontend tests check multiple output windows, reused
windows, idempotence, and the last-window fallback. The real-kernel cell-merge
test asserts removal of both the retired output buffer and its window.
