# Contributing

The repository contains the Python backend, Neovim frontend, shared protocol,
and tests. Read [AGENTS.md](AGENTS.md) and the scoped instructions before changing
resource ownership or protocol behavior. Architecture and implementation status
live under [docs/1.0](docs/1.0/status.md); legacy documents are historical evidence.

## Development environment

With Python 3.9+ and Neovim 0.11+ available:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
```

The frontend also uses curl and a POSIX shell. Run the suites from the repository
root; integration tests need loopback HTTP and Jupyter ZeroMQ sockets:

```sh
.venv/bin/python -m pytest -q
nvim --headless -u tests/frontend/minimal_init.lua -l tests/frontend/run.lua
nvim --headless -u tests/frontend/minimal_init.lua -l tests/e2e/run.lua
```

Use pytest; `python -m unittest` discovers no tests here.

The Python suite includes process-death tests and takes several minutes. The
isolated distribution test is opt-in; see [distribution verification](docs/1.0/architecture/distribution-verification.md).
Development fixtures are test tools, not user-facing plugins.

To try this checkout with your regular Neovim configuration, replace the path:

```sh
nvim --cmd "lua dofile('/absolute/path/to/jusi/scripts/nvim-dev.lua')" example.vipynb
```

The launcher disables legacy jusivim for this invocation and selects the
checkout's `.venv` backend and bridge. Use `:JusiStart jusi` and `:JusiStop`.
It does not change your permanent configuration.

## Changes and releases

Keep Python, Lua, schemas and fixtures in agreement when changing the protocol.
Add focused regression coverage for failures, and update the relevant user guide
when behavior changes. Record ownership decisions in ADRs and concrete incidents
in incident records. Keep the status snapshot factual rather than appending a diary.

Plugin authors should use the [authoring guide](docs/1.0/skills.md) in a separate
plugin project. Release maintainers should follow the
[release procedure](docs/1.0/releasing.md).
