# Release version mismatch blocked kernel startup

## Observation

Preparing `1.0.0rc1` changed the installed distribution version, but
`jusi.__version__` still contained the literal `1.0.0.dev0`. Bundled VisiData
catalog entries used that literal. Fresh discovery correctly rejected the
provider because its version differed from installed package metadata.

This blocked kernel startup even for ordinary Python cells. The Python discovery
test, Neovim end-to-end suite and isolated wheel gate all exposed the mismatch.
No candidate was published.

## Fix

`jusi.__version__` now reads installed distribution metadata through
`importlib.metadata.version`, as the CLI already does. `pyproject.toml` is the
single version source. Provider validation remains strict.

## Verification

`tests/backend/unit/test_package_version.py` compares both the exported version
and bundled provider catalog version to installed metadata. Discovery, real-kernel
end-to-end tests and the distribution gate verify startup after version changes.
