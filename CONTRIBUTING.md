# Contributing

## Development Environment

Use an editable install in a virtual environment.

Example:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

An editable install is the preferred workflow because bundled plugins such as
`jusi_vd` are discovered through installed package metadata.

## Running Tests

Run the backend test suite through the active environment.

Example:

```sh
python -m unittest
```

For focused work, run only the affected test modules.

## Documentation

Public-facing docs should:

- describe the current supported contract
- avoid local repository path references
- avoid historical or migration framing unless strictly necessary
- treat the editor side as a regular Vim/Neovim plugin rather than a local sibling checkout

## Repository Scope

This repository is the backend component.

Plugin-specific behavior that does not belong in core should live in:

- a bundled first-party package when it is part of the supported base experience
- a separate plugin package/repository when it is not core backend functionality
