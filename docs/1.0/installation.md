# Installing Jusi 1.0

Jusi needs Python 3.9+, Neovim 0.11+, curl and a POSIX shell on the editor machine.
The tested host is macOS; Linux uses the same POSIX interfaces. Windows support
is not established. A remote target needs Python and the Jusi package; it does
not need Neovim or SSH. SSH is optional transport forwarding.

The Python wheel includes the matching Neovim frontend. A source checkout and
separate frontend plugin installation are not required. Do not load legacy
jusivim and the 1.0 frontend in the same Neovim process.

## Install a preview artifact

1.0 is still a development build. Use its wheel rather than an unqualified
`pip install jusi`, which may select the legacy release. In these commands,
replace the wheel path with the artifact you downloaded or built:

```sh
python3 -m venv ~/.local/share/jusi/venv
~/.local/share/jusi/venv/bin/python -m pip install /path/to/jusi-1.0.0.dev0-py3-none-any.whl
```

To include the bundled VisiData client, install the same wheel with its extra:

```sh
~/.local/share/jusi/venv/bin/python -m pip install '/path/to/jusi-1.0.0.dev0-py3-none-any.whl[vd]'
```

Once a stable 1.0 is published, the corresponding index installation is
`python -m pip install 'jusi[vd]>=1,<2'` inside the chosen environment. No release
is published by the build or verification commands below.

## Load the frontend

Add this to `init.lua`, before opening a notebook. Adjust the environment path
if you installed elsewhere:

```lua
local jusi_bin = vim.fn.expand("~/.local/share/jusi/venv/bin")
vim.env.PATH = jusi_bin .. ":" .. vim.env.PATH
local runtime = vim.fn.system({ "jusi", "frontend-path" })
assert(vim.v.shell_error == 0, runtime)
vim.opt.runtimepath:prepend(vim.trim(runtime))
```

Neovim loads `plugin/jusi.lua` normally during startup. This also makes the
service and terminal bridge available to Jusi's default local target. The
frontend path belongs to the installed package; pip upgrades replace it along
with the backend. Restart Neovim after an upgrade. Do not keep a second Jusi
frontend on runtimepath.

For an existing plugin-manager installation from Git, keep that installation
instead of the runtimepath snippet, and install the Python package from the same
version. Editable development installs return their checkout from
`jusi frontend-path`; regular wheels always return their bundled runtime.

## First notebook

Open `example.vipynb` with this content:

```text
╭──
40 + 2
╰──
```

Run `:JusiStart local`, then `:JusiExecute` with the cursor in the cell. Its output
should show `42`. `:JusiStop` closes the kernel and owned service. You can also
press Space to enter cell mode and Enter to submit the cell.

The default kernel is `python3`. Kernelspecs are resolved at the target;
additional Python dependencies belong in the kernel environment. The isolated
distribution check verifies the default kernel uses the installed environment.
For named environments and remote endpoints, see
[target configuration](architecture/target-start-stop.md). Backend TOML lives
on the target at `~/.jusi/jusi.toml`.

With the `vd` extra installed, try a cell containing `%%vd` on its first body
line and `[{'value': 'hello'}]` below it. Its terminal supports `zY` copy and
Ctrl-O open. See [VisiData usage](architecture/bundled-vd.md).

Check the selected installation with `jusi --version` and `jusi frontend-path`.
Use `:JusiTrace` for service, kernel and transport failures.

## Build and verify an artifact

From a development checkout:

```sh
python -m pip install build
python -m build
python scripts/check-distribution.py dist/jusi-1.0.0.dev0-py3-none-any.whl
```

The default build produces a source archive, then builds the wheel from that
archive. The smoke checker creates a fresh virtual environment, installs only
the wheel and declared dependencies, and runs Neovim outside the checkout with
an isolated home and runtimepath. It verifies base execution/start/stop, then
installs the `vd` extra and verifies its terminal, copy and open. It checks both
dependency sets with `pip check`. Dependency installation needs a package index
or populated pip cache. All subprocess waits are bounded.

The same gate is discovered by pytest and skips unless explicitly enabled:

```sh
JUSI_DISTRIBUTION_WHEEL=dist/jusi-1.0.0.dev0-py3-none-any.whl python -m pytest -q tests/packaging
```

Plugin-development skills and their matching reference source installer remain
a separate distribution step; `frontend-path` does not install skills.
