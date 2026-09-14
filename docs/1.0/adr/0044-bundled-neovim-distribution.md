# ADR 0044: Bundle the Neovim runtime in the Python distribution

Status: accepted

## Decision

The Python wheel includes `lua`, `plugin`, `ftdetect` and `ftplugin` below
`jusi/_frontend`. These remain normal repository-level Neovim runtime directories
for development and Git plugin managers; the build maps them into package data
without maintaining another source copy. The source archive contains these
inputs, so building a wheel from the archive preserves the same frontend.

`jusi frontend-path` prints the installed runtime directory. Neovim adds it to
runtimepath during startup and loads the plugin normally. No command copies,
overwrites or removes files in the user's Neovim configuration. A pip upgrade
updates Python and Lua together; the user restarts Neovim afterward. An editable
install reports its own checkout only when bundled runtime data is absent.
Missing runtime data fails explicitly instead of returning a nonexistent path.
`jusi --version` reports installed distribution metadata.

The base package remains sufficient for ordinary kernel work. VisiData remains
an optional extra. No frontend dependency is added to the target-side service;
remote-only hosts need neither Neovim nor an SSH installation.

## Verification

`python -m build` builds the wheel from the source archive. The opt-in packaging
test installs that artifact in a fresh environment, checks wheel members and
dependency consistency, then starts Neovim outside the checkout with an isolated
home and runtimepath. It verifies plugin loading, notebook filetype detection,
the installed kernel environment, execution and owned stop. A second run adds
the VisiData extra and verifies its terminal and backend-driven copy/open.
The temporary installation path includes a space. Neither run can rely on
editable imports, test plugin discovery or development launch scripts.

The [installation guide](../installation.md) distinguishes preview artifacts
from the still-separate publication step. Skill/reference-source distribution
is a later task, not a dependency of frontend installation.
