# Incident 0038 — Ordinary files lost line numbers after displaying Jusi output

With `:set number relativenumber`, opening a regular file from a Jusi output
split could hide both kinds of line numbers. Terminal setup wrote the window's
defaults along with the current display options. Neovim then used those
defaults when displaying another buffer.

Output and interactive terminal windows now set their gutters with explicit
local scope. The presentation regression opens a Python buffer in a split
from an output terminal and a JSON buffer in the former terminal window.
