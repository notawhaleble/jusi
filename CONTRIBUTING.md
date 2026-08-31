# Contributing

Read the root `AGENTS.md` and the scoped instructions for the area you are changing.

## Current Phase

The active package is the narrow Jusi 1.0 service walking skeleton. Extend it only along reviewed boundaries; do not reintroduce 0.x lifecycle or transport concepts.

## Test Baseline

Use the environment linked to the current checkout:

```sh
.venv/bin/python -m pytest -q
```

Do not use `python -m unittest`; it discovers zero tests in this repository. The harness separates unit, integration, conformance, frontend, and end-to-end suites under `tests/`.

## Documentation

- `docs/1.0/` is normative for the rewrite.
- `docs/legacy/0.x/` is frozen historical evidence.
- `docs/1.0/status.md` is concise continuity state, not a diary.
- Architectural decisions belong in `docs/1.0/adr/`.
- Reproducible failures and unknown-cause observations belong in `docs/1.0/incidents/`.

## Protocol Changes

A protocol change is complete only when the schema, Python implementation, Lua implementation, fixtures, and conformance tests agree in the same change.
