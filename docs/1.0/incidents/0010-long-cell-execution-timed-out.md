# Incident 0010: Long Cell Execution Timed Out

- Date: 2026-09-08
- Scope: execution lifetime

## Evidence

The user reported `sleep(60)` failing after a few seconds. The ordinary HTTP
execution path inherited the supervisor’s ten-second default, which the kernel
adapter enforced by interruption. Input also had a separate 300-second budget,
and the frontend capped the execution HTTP request at 320 seconds.

## Correction

ADR 0029 removes default execution and input deadlines and the execution HTTP
total timeout. Connection and control/resource deadlines remain independent.
Stop/restart must interrupt active execution before joining the serialized
operation lane, even without a pending input; otherwise removal of the old
timeout could leave teardown waiting indefinitely.

Real-kernel tests advance the adapter clock past both old limits and prove input
completion and explicit interruption. Supervisor tests prove stop and restart
interrupt unbounded work without input. HTTP/frontend tests check the absence
of execution deadlines; end-to-end tests retain lifecycle coverage.
