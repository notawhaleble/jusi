# Release procedure

Backend releases are installed with pip; frontend releases are Git tags consumed
by Neovim package managers. A release uses the same version for both. This guide
prepares and verifies artifacts; publication is a separate maintainer action.

## Prepare the candidate

1. Review [status](status.md) and the [changelog](../../CHANGELOG.md). Record
   unresolved checks honestly. Real remote transport-loss review and independent
   skill evaluation are currently deferred; do not mark them as completed.
2. Choose the candidate version in `pyproject.toml`. Keep development versions
   until selecting an actual release candidate or final release. Update the
   package development-status classifier to match that choice.
3. Update the changelog heading and date for the selected release. Verify
   installation instructions, requirements, optional extras and user controls.
4. Commit the candidate. The distribution gate clones committed source, so a
   dirty working tree is not a release reference.

The required source checks are listed in [CONTRIBUTING.md](../../CONTRIBUTING.md).
Run all three suites, then build into a new directory to avoid mixing artifacts
from different versions:

```sh
python -m pip install build twine
python -m build --outdir dist/candidate
python -m twine check dist/candidate/*
```

Use an empty `dist/candidate` directory for each candidate; retain previous
artifacts elsewhere when needed. The build produces an sdist and a wheel built
from that sdist. The wheel contains Python code and skill templates, while the
frontend is installed separately from Git.

Run the [isolated distribution gate](architecture/distribution-verification.md)
against the wheel, substituting its actual filename:

```sh
python scripts/check-distribution.py dist/candidate/jusi-VERSION-py3-none-any.whl
```

Check notebook import/export from the built artifact as well as the editor smoke
checks. Review the archive contents for unintended local files and verify that
the sdist contains the frontend, tests and maintainer documentation.

## Manual review and demo

Use a regular Neovim configuration for a short user-flow review:

- Start a fresh notebook; execute `40 + 2`, then exercise completion.
- Run `a = input('Name: ')`, submit a reply and inspect the output/status.
- Open `%%vd`, copy a value with `zY`, and open it with Ctrl-O.
- Park an output, execute another cell, then close the parked output.
- Stop the target and confirm output cleanup and the off badge.

For a demo, record those steps in a small notebook with invented sample data.
Show the initial installation/setup separately from the everyday start/edit/stop
flow. Save the recording only after reviewing its legibility and visible paths.
A recording has not yet been produced.

## Publish the verified pair

After reviewing the candidate and deciding to publish:

1. Create the `v<VERSION>` Git tag on the verified commit and push it to the
   canonical repository. Use the literal package version: for example, version
   `1.0.0rc1` corresponds to tag `v1.0.0rc1`.
2. Verify the tag is fetchable and points to that commit. `jusi install-skills`
   fetches this exact tag, so it must exist before the backend becomes available.
3. Upload only the verified wheel and sdist using the project's PyPI credentials
   or configured publishing workflow. Do not rebuild between verification and
   upload. Publishing a version is irreversible; never reuse it for different
   contents.
4. Create release notes from the changelog and link the installation guide.
5. From a fresh environment, install the published package and matching frontend
   tag, check `jusi --version`, start/execute/stop, and run `jusi install-skills`
   against the published reference. This checks delivery, not skill behavior.

If publication is partial, report which component is available and finish the
same verified pair. Do not move an already published tag to a different commit.
