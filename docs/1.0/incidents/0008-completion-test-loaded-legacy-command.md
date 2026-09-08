# Incident 0008: Completion E2E Loaded An Installed Legacy Command

- Date: 2026-09-08
- Status: resolved

## Observation

The first 1.0 completion end-to-end test called `JusiComplete` and received the
0.x error `Jusivim request completion requires a .vipynb notebook buffer`. No
1.0 completion request was sent, although the Python tests passed.

## Cause

`tests/frontend/minimal_init.lua` only prepended the repository to runtimepath.
Neovim still loaded installed user plugins and packages at startup. A legacy
`plugin/jusi.vim` defined the same command after the 1.0 plugin loaded. Changing
HOME inside the e2e runner happened after plugin loading and did not isolate it.

## Resolution

The minimal init now sets runtimepath to this repository plus `$VIMRUNTIME`,
and packpath to `$VIMRUNTIME` for bundled packages. User plugins/configuration
are excluded before startup plugin loading. No sibling or installed plugin was
modified. The completion e2e invokes the public command and checks the resulting
menu and source text, so command shadowing cannot silently pass.
