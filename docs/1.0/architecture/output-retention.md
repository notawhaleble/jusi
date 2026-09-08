# Output Retention On New Execution

- Status: user requirement recorded; implementation deferred
- Date: 2026-09-07

A real new `JusiExecute` should clean completed, unparked cell outputs across
its kernel, not merely replace the current cell's output. Final outcomes include
successful and failed execution (user-facing done/error). Input submission and
plugin followup submission do not trigger this cleanup.

Busy work and artifacts awaiting a followup remain by design. A durable plugin
client must not be classified as final solely because its initiating kernel
execution completed; its interaction lifetime is distinct.

An explicit cell-oriented command, tentatively `JusiPark` or `JusiPersist`,
should retain an output across subsequent executions. The command name and
visual status are not yet selected. Retention is conceptually separate from
execution outcome: a retained artifact may still have a successful or failed
execution outcome. It adds no kernel state.

Before implementation, resolve:

- the authoritative classification of final, busy, and followup artifacts;
- whether interrupted/cancelled artifacts are automatically cleaned;
- cleanup timing if the new execution is rejected or fails to start;
- re-executing the parked cell itself, including whether multiple retained
  artifacts per cell are supported (the initial invariant permits only one);
- unpark behavior and retention scope across restart;
- presentation of retention alongside execution status.

Opener retirement is already defined by ADR 0025: it fully closes the owning
cell regardless of parking. Parking does not protect resources from owner deletion.

Current `JusiExecute` behavior remains unchanged until this dedicated slice.
