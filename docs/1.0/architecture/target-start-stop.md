# Starting and stopping notebook targets

With the frontend installed and `jusi` on PATH, the default local target needs
only `:JusiStart local`. Use `:JusiStop` from the notebook or one of its outputs.

Configure named profiles in your Neovim configuration:

```lua
require("jusi").setup({
  targets = {
    dev = {
      kind = "local",
      command = { "/path/to/environment/bin/jusi", "serve" },
      kernel_name = "python3",
    },
    server = {
      kind = "remote",
      base_url = "http://127.0.0.1:9000",
      kernel_name = "python3",
      terminal_bridge_command = { "/local/environment/bin/jusi", "terminal-bridge" },
    },
  },
})
```

`:JusiStart dev` starts an owned local service and its kernel. `command` is
an argv list, without shell expansion. The local service chooses an available
port. The kernel name is a Jupyter kernelspec available at that target.
For simple `jusi serve` commands the local terminal bridge uses the same
executable; specify `terminal_bridge_command` explicitly for custom launchers.
The bridge always runs beside Neovim, including for remote targets.

`:JusiStart server` connects directly to the service at that URL and starts its
kernel. The example URL can be a port-forward to a target-side service. Starting
SSH connections, containers or remote services is not yet automated. A target
already serving another live notebook is rejected.

`:JusiStop` closes runtime resources and output splits, preserving notebook
text. Local targets also stop their owned service. Remote services stay alive.
A stop issued during startup cancels the launch or cleans up once the pending
kernel-start operation resolves. Failed cleanup is reported; retry stop after
resolving the failure. `:JusiTrace` retains received diagnostics.

The resolved profile stays fixed until stop; `:JusiRestart` uses that profile's
kernel name and reloads target-side runtime configuration. `targets` replaces
the alias table when supplied to `setup`. Optional `timeout_ms` controls local
service readiness, not execution or input waiting.

## Test with regular Neovim and legacy jusivim installed

From this checkout, use its absolute path:

```sh
nvim --cmd "lua dofile('/Users/niku/Documents/dev/jusi/scripts/nvim-dev.lua')" /path/to/notebook.vipynb
```

This keeps your ordinary configuration and other plugins, disables the legacy
jusivim plugin for this invocation, and loads the current checkout with its
`.venv` service and bridge. No permanent configuration is changed. It defines
the `jusi` local alias, so run `:JusiStart jusi`, then `:JusiStop` when done.
Use a native 1.0 notebook; legacy notebook conversion is still separate.
