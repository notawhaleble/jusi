# Incident 0024: Library Path Aliases Duplicated Plugin Discovery

A container failed kernel startup with `plugin_discovery / conflict` and
`Duplicate plugin_id: jusi_shell`. Its plugin metadata appeared under both
`/opt/app-root/lib/` and `/opt/app-root/lib64/`.

A fixture with `lib64` symlinked to `lib` reproduced the failure in the fresh
discovery process, both through explicit search paths and through the normal
Python environment. `importlib.metadata.distributions()` enumerated the same
installation through each path. Jusi loaded both registrations and its catalog
validator rejected the duplicate ID before the kernel could start. Whether the
reported container paths are symlinks still needs confirmation.

Discovery now resolves search paths to identify directory aliases and scans
each directory once, preserving the first path's position. Python import paths
are unchanged. Distinct installations with identical plugin IDs and versions
continue to fail with a typed conflict; there is no deduplication by plugin ID
or package name.

The integration tests in `tests/backend/integration/test_plugin_discovery.py`
cover both discovery paths and rejection of genuinely separate installations.
