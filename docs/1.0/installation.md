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

## First notebook

Open `example.vipynb` containing:

```text
╭──
40 + 2
╰──
```

Run `:JusiStart local`, then `:JusiExecute` with the cursor in the cell. Its output
should show `42`. `:JusiStop` closes the kernel and owned service. You can also
press Space to enter cell mode and Enter to submit the cell.

The default kernel is `python3`. Additional Python dependencies belong in the
kernel environment. Configure other executable paths, named environments and
remote targets through [target configuration](architecture/target-start-stop.md).
Backend configuration lives at `~/.jusi/jusi.toml` on the target.

With the `vd` extra installed, try a cell containing `%%vd` on its first body
line and `[{'value': 'hello'}]` below it. Its terminal supports `zY` copy and
Ctrl-O open. See [VisiData usage](architecture/bundled-vd.md).

Use `:JusiTrace` to inspect service, kernel and transport failures.
