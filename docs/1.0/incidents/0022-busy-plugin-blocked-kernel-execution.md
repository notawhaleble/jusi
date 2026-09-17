# Incident 0022: Busy Plugin Blocked Kernel Execution

- Date: 2026-09-17
- Scope: supervisor operation scheduling

## Evidence

The user reported that a busy plugin cell prevented other cells from executing,
although an idle followup-capable client did not. The frontend execute controller
does not gate requests on notebook-wide busy marks. The supervisor instead held
its global operation lock throughout established-client worker I/O, making
unrelated Python executions wait for a plugin followup to return.

## Correction

[ADR 0047](../adr/0047-plugin-work-does-not-own-the-kernel-lane.md) separates
established-client work from kernel serialization and admits at most one active
operation per client. Existing close fences and exact interrupts are retained.
Regression coverage exercises all three established-client operation kinds and
real Python execution while a terminal fixture followup is still blocked.
