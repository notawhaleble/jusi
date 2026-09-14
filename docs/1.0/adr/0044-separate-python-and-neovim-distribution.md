# ADR 0044: Separate Python and Neovim distribution

Status: accepted (revised before release)

## Decision

Users install the Python backend with pip and the Neovim frontend from the
GitHub repository through a plugin manager or native package directory. Releases
pair a Python distribution version with a matching frontend Git tag.

The wheel contains the backend only. There is no Python command to locate or
install the frontend and no wheel-owned runtimepath integration. Repository
`lua`, `plugin`, `ftdetect` and `ftplugin` directories remain a conventional
Neovim plugin. `jusi --version` reports installed Python distribution metadata.

The source archive retains development inputs needed for building and testing.
An isolated distribution gate installs the backend wheel and separately clones
the frontend into a native Neovim package directory. It verifies base operation
and optional VisiData copy/open without editable Python imports or user config.

User installation instructions describe the release workflow. Preview artifact
builds and unpublished-version verification belong in maintainer documentation.
The initial bundled-frontend approach was withdrawn following product review.

See [installation](../installation.md) and
[verification](../architecture/distribution-verification.md).
