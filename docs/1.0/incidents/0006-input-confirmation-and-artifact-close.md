# Incident 0006: Missing Input Confirmation And Close Left Input Waiting

- Status: reproduced and fixed in Jusi 1.0 development
- Date: 2026-09-07

## Symptoms And Cause

Executing `input('lalala: ')` and replying `ololo` displayed
`lalala: 'ololo'`. The apparent echo was actually Python's expression result,
appended directly to the prompt. `a = input('lalala: ')` produced no visible
confirmation because assignments have no expression result. The frontend had
no accepted-input presentation path.

`JusiClose` only deleted the ordinary output projection. A kernel waiting for
input remained blocked, and subsequent executions queued behind it. Late
execution output could also recreate an explicitly closed projection.

## Correction

An ordered acceptance event confirms the submitting frontend's exact trace
before it echoes its transient reply text and a newline. This creates
`lalala: ololo` for both forms. Bare input's actual Python result remains on
the next line; the frontend does not parse or strip Python representations.
Password replies and replies submitted by another frontend use a value-free
acceptance marker. Public backend events still contain identities only.

Closing a pending-input artifact interrupts its exact execution, then retires
that execution's presentation after acknowledgement. Late output cannot
resurrect it or remove a newer artifact. Native window close still only hides.
Input acceptance does not prematurely change execution outcome to succeeded.

## Verification

Frontend tests cover literal echo after ordered acceptance, replay, duplicate
submission, password redaction, late-output fencing, and delayed close against
a newer artifact. The real Neovim scenario covers both bare and assigned input,
close from a projection, native hide, interruption, and successful execution
immediately after abandoning input.
