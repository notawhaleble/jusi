# Quickstart demo

Copy `quickstart.vipynb` to a working location before editing it. Install Jusi
with the `vd` extra and the matching Neovim frontend as described in the
[installation guide](../docs/1.0/installation.md).

A short recording can follow this sequence:

1. Open the copy and call `:JusiStart local`. Execute the first cell to show `42`.
2. Press Space for cell mode. Press `S` on the first cell to park its output;
   the `~` mark appears. Navigate to the second cell with `j` and submit with
   Enter. Show `45` and that the parked output survives.
3. Move to `%%vd` and submit. Press Ctrl-\ twice to focus its terminal. Select
   a name, press `zY`, and then Ctrl-O to open the value in a split. Close that
   exported split with `:close` after returning to Normal mode if needed.
4. Toggle focus back to the notebook. Submit the input cell, replace its body
   with `Ada`, and submit again. Show the literal reply and `Hello, Ada!`.
5. Call `:JusiStop` and show the off badge and closed runtime outputs.

Optional completion shot: create a cell with `B`, type `from time imp`, and press
Tab. Use native completion keys to select and accept the result.

Use invented data only. Choose a readable terminal font and keep the notebook
and output visible. This is a recording walkthrough, not an automated demo or
an existing video. Kernel input replaces the active cell body during this flow,
so use a fresh copy for another take.
