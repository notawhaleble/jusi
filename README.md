# Jusi

Jusi brings Jupyter kernels and interactive tools into Neovim. Edit plain-text
notebooks, execute cells, and work with their outputs in native splits. Notebook
editing, navigation and history remain available without a running kernel.

![Jusi 1.0 demo](docs/assets/jusi-1.0.gif)

- Cell-local syntax, indentation and completion.
- Interactive terminal clients, including bundled VisiData.
- Followup history, output parking and keyboard navigation.
- Local or remote kernels through a target-side Python service.
- Plugin-driven copy, open and diff display in Neovim.

## Install

Install the backend in your Python 3.9+ environment:

```sh
pip install jusi
# Or include VisiData:
pip install 'jusi[vd]'
```

Install `notawhaleble/jusi` through your Neovim plugin manager, or use a native
package directory:

```sh
git clone https://github.com/notawhaleble/jusi.git \
  "${XDG_DATA_HOME:-$HOME/.local/share}/nvim/site/pack/jusi/start/jusi"
```

The frontend requires Neovim 0.11+, curl and a POSIX shell. Keep the backend
and frontend on matching releases and make `jusi` available on Neovim's PATH.
See [installation](docs/1.0/installation.md) for configuration and upgrades.

## First notebook

Save this as `example.vipynb` and open it in Neovim:

```text
╭──
40 + 2
╰──
```

Run `:JusiStart local`, then `:JusiExecute` inside the cell. The result opens in
a split. Press Ctrl-\ twice to toggle focus between the cell and its output.
Use `:JusiStop` to close the kernel and its owned service.

Space toggles cell mode: Enter submits, `j`/`k` navigate cells, `B` creates a
cell below, and `S` parks an output so it survives later executions.
See the [user guide](docs/1.0/usage.md) for all controls, input, history and status.

## Jupyter notebooks

```sh
jusi import-ipynb notebook.ipynb
jusi export-ipynb notebook.vipynb -o exported.ipynb
```

Import preserves code, Markdown and raw sources as ordinary native cells.
Export creates code cells. Outputs and followup history are excluded.
See [conversion](docs/1.0/notebook-conversion.md) for details.

## Learn more

- [Local and remote targets](docs/1.0/architecture/target-start-stop.md)
- [VisiData: explore data, copy values and open content](docs/1.0/architecture/bundled-vd.md)
- [Backend configuration](docs/1.0/configuration.md)
- [Plugin authoring skills](docs/1.0/skills.md): install with `jusi install-skills`
- [Contributing and tests](CONTRIBUTING.md)
- [Release notes](CHANGELOG.md)

Jusi 1.0 requires Neovim; it does not support Vim or legacy Jusi notebook
syntax. [Architecture](docs/1.0/architecture/overview.md),
[design intent](docs/1.0/intent.md), and [implementation status](docs/1.0/status.md)
are maintained separately from the user guides.
