# Session Lifecycle Draft

This document defines the intended backend state transitions for the current contract slice.

## Session

States:

- `idle`
- `starting`
- `connected`
- `disconnected`
- `stopping`
- `stopped`
- `failed`

Transitions:

- `idle -> starting`: accepted `start_session` or `attach_session`
- `starting -> connected`: session and initial prepared client are usable enough to continue
- `starting -> failed`: startup/attach failed
- `connected -> disconnected`: linkage loss while the durable session may still exist
- `disconnected -> starting`: reconnect begins
- `connected -> stopping`: accepted `stop_session`
- `stopping -> stopped`: teardown completed
- `connected -> failed`: unrecoverable failure

Rules:

- backend is launched by `jusivim`
- sessions are durable/reconnectable by default
- one backend root process is expected to hold one durable session record
- frontend may keep a persisted reconnectables registry across Vim lifetimes
- timed-out durable sessions do not need proactive frontend expiry events
- backend now actively tears timed-out disconnected sessions down on the backend side
- stale frontend reconnectable entries may still be discovered later on attach/reconnect attempts
- backend session metadata currently keeps explicit `target` only
- disconnected sessions now also carry `expires_at` timeout metadata
- backend now also drives frontend-link liveness with explicit healthchecks while sessions are `connected`
- missed frontend healthcheck replies transition the session into normal `disconnected` timeout handling
- `endpoint`/backend residence stays outside core backend session state
- `disconnected` is not equivalent to kernel death
- `failed` should record a reason
- externally attached `connection_file` sessions may also participate in a small sidecar peer registry so stop can fan out to other attached Jusi root processes
- that same sidecar now carries the shared disconnect timeout deadline for attached peers
- known issue: suspended Vim, for example via `Ctrl-Z`, may look like link loss to backend healthchecks

## Prepared Client

States:

- `missing`
- `spawning`
- `binding`
- `ready`

Transitions:

- `missing -> spawning`: backend begins provisioning
- `spawning -> binding`: backend-owned prepared client exists, frontend buffer not bound yet
- `binding -> ready`: frontend acknowledges the real Vim buffer
- `ready -> spawning`: execution consumed the prepared client; replacement begins
- `spawning -> missing`: provisioning failed or session teardown
- `binding -> missing`: teardown before bind completed
- `ready -> missing`: teardown or disconnect

Rules:

- prepared client is session-scoped, not cell-scoped
- only one prepared client is current at a time
- execution consumes the current prepared client atomically from the frontend point of view
- replacement preparation begins only after that consume
- backend must not claim `ready` before `bind_prepared_client`

## Cell Execution

States:

- `pending`
- `busy`
- `follow-up`
- `done`
- `error`
- `interrupted`
- `parked`

Rules:

- cell status is separate from prepared-client readiness
- active cell keeps the consumed client identity until a later lifecycle event changes it
- `follow-up` does not block later execution once the next prepared client becomes `ready`
- `parked` remains reserved for deliberate keep-output semantics
- `input_reply` resumes the same active execution after `input_request`

## Client Lifecycle

States:

- `active`
- `shutting_down`
- `shutdown`

Rules:

- client teardown is separate from interrupt
- prepared and active-cell client teardown should surface through `client_state`
- client view snapshots are derived state, not lifecycle state

## Execution Ownership

Owner kinds:

- `kernel`
- `handler`
- `unknown`

Rules:

- interrupt routing depends on owner kind
- owner kind is independent from cell status
- disconnect may degrade active execution ownership to `unknown`
- reconnect should not invent a false owner if it cannot be restored safely
