# Interactive terminal development fixture

This directory is a test-only import path and distribution metadata record. It
is not part of the Jusi package and must never be enabled by the production
service implicitly.

When the directory is explicitly placed on `PYTHONPATH`, fresh discovery finds
the `terminal_fixture` exact plugin. A `%%terminal_fixture` cell then traverses
the real kernel adapter, handoff, worker, terminal-surface request, target PTY,
WebSocket, bridge, and Neovim terminal projection. The application reports its
first and later terminal geometries and echoes input with ANSI styling.

For a manual run, create an untracked `/tmp/jusi-terminal.vipynb` containing:

```text
╭──
%%terminal_fixture
manual fixture
╰──
```

From the repository root, launch a clean Neovim with the fixture explicitly on
the child-process import path:

```sh
PYTHONPATH="$PWD/tests/fixtures/terminal_plugin${PYTHONPATH:+:$PYTHONPATH}" \
  nvim --clean \
  --cmd "set runtimepath^=$PWD" \
  --cmd "lua require('jusi').setup({service_command={'$PWD/.venv/bin/jusi', 'serve'}})" \
  /tmp/jusi-terminal.vipynb
```

Run `:JusiServiceStart`, `:JusiStartKernel`, place the cursor in the cell, and
run `:JusiExecute`. The interactive split should report its initial geometry;
typing there should echo through the target PTY. Return to the notebook and run
`:JusiCloseClient`, then `:JusiServiceStop`. Explicit close must remove the
terminal while the kernel remains on until service stop.
