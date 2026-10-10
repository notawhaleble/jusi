# Installation

Jusi has two components: a Python backend installed with pip and a Neovim
plugin installed from GitHub. Use matching release versions of both.

## Python backend

Install into your chosen Python 3.9+ environment:

```sh
pip install jusi
```

For the bundled VisiData client:

```sh
pip install 'jusi[vd]'
```

Make that environment's `bin` directory available on Neovim's PATH. Activating
its virtual environment before launching Neovim is one way to do this. Check
which backend is selected with `jusi --version`.

## Neovim plugin

The frontend requires Neovim 0.11+, curl and a POSIX shell. Install
`notawhaleble/jusi` through your Neovim plugin manager, or use Neovim's native
package directory:

```sh
git clone https://github.com/notawhaleble/jusi.git \
  "${XDG_DATA_HOME:-$HOME/.local/share}/nvim/site/pack/jusi/start/jusi"
```

Restart Neovim. The plugin loads automatically; no runtimepath manipulation or
Python-side frontend installer is needed. If you use a custom `NVIM_APPNAME` or
package directory, adjust the destination accordingly. Disable legacy jusivim
before loading Jusi 1.0 in the same editor.

For a pinned installation, select the corresponding release tag in your plugin
manager or Git checkout and pin the same Python version with pip. Upgrade both
components together, after stopping active targets, then restart Neovim.

## A local virtual environment and a Docker target

For a minimal local Python kernel, create an environment containing Jusi and
register its interpreter as that environment's `python3` kernel:

```sh
python3 -m venv "$HOME/.venvs/jusi"
"$HOME/.venvs/jusi/bin/python" -m pip install 'jusi[vd]==1.0.5'
"$HOME/.venvs/jusi/bin/python" -m ipykernel install --sys-prefix --name python3
```

The `vd` extra is optional. Install your Python libraries and Jusi plugins into
this same environment. Explicit executable paths below let Neovim use it without
activating the environment or changing Neovim's PATH.

For Docker, put this `Dockerfile` in an empty directory:

```dockerfile
FROM python:3.12-slim
RUN python -m pip install --no-cache-dir 'jusi[vd]==1.0.5' \
    && python -m ipykernel install --sys-prefix --name python3
WORKDIR /work
CMD ["jusi", "serve", "--host", "0.0.0.0", "--port", "8765"]
```

Build it there, then run the service with its port published on host loopback:

```sh
docker build -t jusi-kernel:1.0.5 .
docker run --rm --name jusi-kernel -p 127.0.0.1:9000:8765 jusi-kernel:1.0.5
```

Keep that command running while using the Docker target. The service listens
on all interfaces inside the container so Docker can forward the port; the host
mapping uses `127.0.0.1`. See [Docker's port publishing guide](https://docs.docker.com/engine/network/port-publishing/).
Install kernel dependencies and plugins in the image. To access host data, add
an appropriate bind mount, such as `--mount type=bind,src=/absolute/data,dst=/work`.
Target-side paths and configuration refer to files inside the container.

Add this to `init.lua` (or your plugin manager's Jusi configuration callback):

```lua
local jusi_bin = vim.fn.expand("~/.venvs/jusi/bin/jusi")

require("jusi").setup({
  targets = {
    venved_jusi = {
      kind = "local",
      command = { jusi_bin, "serve" },
      kernel_name = "python3",
    },
    dockered_jusi = {
      kind = "remote",
      base_url = "http://127.0.0.1:9000",
      kernel_name = "python3",
      terminal_bridge_command = { jusi_bin, "terminal-bridge" },
    },
  },
})
```

Use `:JusiStart venved_jusi` or `:JusiStart dockered_jusi` in an open notebook.
`targets` is a table keyed by alias, not a list; supplying it replaces the default
alias table, so use these names instead of `:JusiStart local`. Lua reserves
`local`: if you choose that alias, write `["local"] = { ... }`.
Arguments are passed directly without shell expansion; use `vim.fn.expand()`
for `~` in executable paths. The bridge subcommand is spelled `terminal-bridge`.

The Docker kernel and service run inside Docker, but the terminal bridge runs
on the Neovim host using your local venv's Jusi. Both installations and the
frontend should use the same release. `:JusiStop` stops the selected kernel and
its clients; for the Docker target it leaves the externally started service
running. Stop the container separately with `docker stop jusi-kernel`.
See [target configuration](architecture/target-start-stop.md) for more options.

## First notebook

Open `example.vipynb` containing:

```text
╭──
40 + 2
╰──
```

Run `:JusiStart local` (or `:JusiStart venved_jusi` with the configuration
above), then `:JusiExecute` with the cursor in the cell. Its output should show
`42`. `:JusiStop` closes the kernel and owned service. You can also press Space to enter cell mode and Enter to submit the cell.

The default kernel is `python3`. Additional Python dependencies belong in the
kernel environment. Configure other executable paths, named environments and
remote targets through [target configuration](architecture/target-start-stop.md).
Backend configuration lives at `~/.jusi/jusi.toml` on the target.

With the `vd` extra installed, try a cell containing `%%vd` on its first body
line and `[{'value': 'hello'}]` below it. Its terminal supports `zY` copy and
Ctrl-O open. See [VisiData usage](architecture/bundled-vd.md).

Use `:JusiTrace` to inspect service, kernel and transport failures.

## Plugin authors

Run `jusi install-skills` to install the plugin and family development skills
with matching reference source. See the [skills guide](skills.md).
