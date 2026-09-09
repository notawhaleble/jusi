# ADR 0033: Completed Output Retention

Status: accepted

## Decision

Implement the previously recorded kernel-wide output-cleanup requirement at
accepted execution start. Controller events provide exact kernel, notebook,
cell and execution identities. Frontend cell lifecycle owns retention and full
artifact cleanup. This introduces no protocol or kernel-state changes.

Both explicit execution and contextual Enter submission reach the same event
hook. Input and followup operations retain their current presentation. Rejected
new execution preserves other outputs; an accepted execution cleans final
outputs even if that execution later fails.

The current pending presentation identity or durable client identifies the
artifact. Known success, failure, interruption and cancellation are final.
Running cells, live followup clients, parked artifacts, unknown outcomes and
other kernel generations are protected. Cleanup never classifies a followup
client as final solely from its initiating execution's completion.

`JusiPark` toggles a frontend retention flag on the current artifact's cell.
It works from source or projection context. Cell-mode `S` invokes it; a `~` beside the outcome symbol projects retention
without replacing the outcome color. Close, opener retirement and direct re-execution clear it;
restart discards it with the old runtime. Unparking defers cleanup until a new
execution starts. Full cleanup uses the existing cell close path, including
client teardown and late-output fencing.

## Verification

`tests/frontend/output_retention_spec.lua` covers final outcomes, busy and
followup protection, kernel scope, parking/unparking and late-output fencing.
`tests/e2e/kernel_input_spec.lua` executes through the actual cell-mode Enter
mapping and verifies removal of another cell's completed output; parked outputs
survive new executions and remain subject to opener-retirement cleanup.
