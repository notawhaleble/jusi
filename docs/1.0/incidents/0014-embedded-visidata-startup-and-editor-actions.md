# Incident 0014: Embedded VisiData Startup and Editor Action Isolation

- Date: 2026-09-09
- Status: resolved

## Symptoms

The first real bundled `%%vd` scenario rendered Greek text as blanks and showed
`FileExistsError` for VisiData's data directory in a fresh home. After those were
fixed, sending Ctrl-O as soon as copy reached Neovim reported that the previous
`deliver` command was still running and did not open the value.

## Causes and Fixes

The embedded application bypasses VisiData CLI initialization. Python inherited
`C.UTF-8`, which is unavailable on the reference macOS host, leaving curses in a
non-UTF-8 character locale. The application now selects an installed UTF-8
character locale before initializing VisiData/curses. Kernel text and terminal
bytes remain unchanged.

VisiData 3.4's `StoredList.path` uses an existence check followed by `mkdir` without
`exist_ok`. Concurrent first-use writers can both observe an absent directory.
The application creates VisiData's data directory idempotently before starting
its UI or loading user plugins; it does not patch the installed VisiData package
or disable user configuration to hide the problem.

A sheet-associated VisiData background task blocks another command's task until
it finishes. Editor mutation precedes the copy acknowledgment's arrival at the
application, so Ctrl-O could arrive inside that gap. Copy/open now capture their
text synchronously and use `vd.execAsync(..., sheet=None)`: the bounded delivery
belongs to the application/client, and no longer counts as work mutating the
source sheet. Source-client cleanup still closes its action channel and process.

## Verification

`tests/e2e/vd_spec.lua` uses a fresh home, asserts Unicode rendering without the
state-directory error, and sends Ctrl-O immediately after the unnamed register
changes. It verifies the exported content and independent buffer lifetime.
`tests/backend/integration/test_vd_plugin.py` verifies capture/delivery hooks and
application sheet construction in isolated processes. An earlier import of
`PythonAtomSheet` from the wrong module was also caught by the real startup path;
the application-construction test now covers that path directly.
