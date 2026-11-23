# Quickstart demo

Copy `quickstart.vipynb` to a working location before editing it. Install Jusi
with the `vd` extra and the matching Neovim frontend as described in the
[installation guide](../docs/1.0/installation.md).

Follow the [recorded quickstart](../docs/assets/jusi-1.0.gif) in Neovim.

1. Open the copy and call `:JusiStart local`. Execute the first cell to show `42`.
2. Press Space for cell mode. Press `S` on the first cell to park its output;
   the `~` mark appears. Navigate to the second cell with `j` and submit with
   Enter. Show `45` and that the parked output survives.
3. Move to `%%vd` and submit. Press Ctrl-\ twice to focus its terminal. Select
   a name, press `zY`, and then Ctrl-O to open the value in a split. Close that
   exported split with `:close` after returning to Normal mode if needed.
4. Toggle focus back to the notebook. Run the input cell. At Neovim’s `Name:` prompt,
   type `Ada` and press Enter. The output shows `Hello, Ada!`.
5. Call `:JusiStop` and show the off badge and closed runtime outputs.

Optional completion shot: create a cell with `B`, type `from time imp`, and press
Tab. Use native completion keys to select and accept the result.
