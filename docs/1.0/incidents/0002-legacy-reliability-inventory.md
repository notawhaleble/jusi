# Incident Inventory 0002: Legacy Reliability Evidence

- Status: investigation backlog
- Sources: audited 0.x code, tests, ignored continuity notes, and sibling legacy repository at the commits in `../../legacy/0.x/PROVENANCE.md`

## Candidate Regression Scenarios

- execute completion racing with stop and overwriting terminal execution state
- startup failure leaving a durable intermediate state
- async stop failure leaving teardown indefinitely incomplete
- malformed protocol input terminating or silently disappearing
- plugin runtime failure producing inconsistent client/cell ownership
- stale attach environment or supervisor PID surviving target/session replacement
- output update semantics for `display_id` and `clear_output(wait=true)`
- missing IOPub idle with a valid shell reply
- input request and interrupt ordering
- stale frontend callbacks after kernel or client replacement
- structural cell edits resurrecting deleted runtime identity
- large-notebook whole-buffer reparsing and rendering on ordinary edits

## Use

Each scenario must be rewritten against 1.0 invariants. Legacy expected states such as `failed`, `disconnected`, prepared clients, or repair rituals must not be copied into new fixtures.
