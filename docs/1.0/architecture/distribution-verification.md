# Distribution verification

This is a maintainer check for artifacts before publication. User installation
is documented separately in the [installation guide](../installation.md).

```sh
python -m pip install build
python -m build
python scripts/check-distribution.py dist/jusi-1.0.0.dev0-py3-none-any.whl
```

The build creates a source archive and builds the backend wheel from it. The
wheel contains Python code, with no bundled Neovim frontend.

The checker installs the wheel and declared dependencies in a fresh virtual
environment. It separately clones the Git frontend into an isolated native
Neovim package directory, then runs outside the development checkout with a
fresh home. It verifies automatic package loading, filetype detection, kernel
environment, execution/start/stop, and optional VisiData terminal/copy/open.
Both dependency sets pass `pip check`; temporary paths include spaces. The gate
also installs the wheel-provided authoring skills with a committed source
reference, repeats installation, and proves user-edited skills are preserved.

By default the frontend clone comes from the local repository's HEAD. To verify
an exact release source, pass `--frontend-repo URL --frontend-ref TAG`. This
checks the intended release pair without depending on its prior publication.
Dependency installation needs a package index or populated pip cache.

The same opt-in gate is discovered by pytest:

```sh
JUSI_DISTRIBUTION_WHEEL=dist/jusi-1.0.0.dev0-py3-none-any.whl python -m pytest -q tests/packaging
```

Publishing the backend and tagging the matching frontend are separate release
steps. These verification commands do neither.
