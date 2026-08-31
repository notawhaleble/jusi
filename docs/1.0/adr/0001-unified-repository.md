# ADR 0001: Unified Jusi 1.0 Repository

- Status: accepted
- Date: 2026-08-31

## Context

The 0.x Python backend and Vim-compatible frontend evolved in separate repositories with duplicated continuity notes and conversational contract synchronization. Protocol, implementation, and tests could drift.

## Decision

Jusi 1.0 places the Python service, root-level Neovim runtime, shared protocol, fixtures, tests, and durable design records in this repository.

The sibling `jusivim` remains the legacy 0.x product. Its Vimscript is neither moved nor modified.

Root-level `lua/`, `plugin/`, `ftplugin/`, and related directories allow ordinary Neovim plugin managers to install the repository directly. Python remains in the `src/` layout.

## Consequences

- Cross-language protocol changes can be atomic.
- One CI graph can run conformance and end-to-end tests.
- The repository contains both a Python distribution and a Neovim plugin, so ownership and packaging boundaries must remain explicit.
- Historical 0.x documentation must be visibly isolated.
