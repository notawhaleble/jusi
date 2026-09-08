# Incident 0009: Status Marks Missing Terminal Colors

- Date: 2026-09-08
- Scope: Neovim status presentation

## Evidence

The user saw uncolored status symbols and no yellow never-executed openers.
Highlight inspection showed correct RGB foreground definitions; the running
Neovim also had `termguicolors` enabled. Read-only inspection of its local RPC
socket showed an Apple Terminal environment and a yellow opener in Neovim's
rendered grid. The original groups defined no `ctermfg` fallback. Advising the
user to enable RGB output did not resolve their display issue.

## Correction

Define both RGB and 256-color foregrounds for every cell-status group. Terminal
users can retain `notermguicolors`; Jusi does not change this editor option.
The frontend suite checks both palettes. Virtual-text placement before the
opener is a separate presentation concern, unchanged by this correction.
