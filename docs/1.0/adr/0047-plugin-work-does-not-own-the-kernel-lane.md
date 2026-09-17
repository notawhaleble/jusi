# ADR 0047: Plugin Work Does Not Own The Kernel Lane

- Status: accepted
- Date: 2026-09-17

## Decision

Established-client followup, completion and editor-action work runs outside the
supervisor's serialized kernel/lifecycle lane. Admission under the state lock
checks client identity, capability, the close fence and any active operation for
that same client. A second operation on a busy client receives a recoverable
conflict rather than queuing stale input. Different clients and kernel execution
can proceed concurrently; exact operation identities still govern interruption.

Close and runtime teardown fence admission under that same state lock and stop
busy workers through the existing independent control path. Kernel operations
remain serialized. Initial worker construction and surface publication remain a
bounded serialized handoff, as specified in ADR 0034. No protocol or durable
resource-state changes are introduced.

This replaces ADR 0034's runtime-wide serialization of established-client work.
A busy cell remains an operation projection and conveys no kernel occupancy.

## Verification

Parameterized supervisor tests hold each plugin operation open while another
client and a Python cell finish, and reject same-client overlap without losing
the original interrupt identity. The real terminal end-to-end scenario executes
Python during a blocked followup, then interrupts the original plugin operation
while retaining its client and terminal.
