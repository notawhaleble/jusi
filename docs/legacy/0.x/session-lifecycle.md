# Session Lifecycle

This document defines the backend state transitions for the contract slice.

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
- `starting -> connected`: session startup or attach completed enough to accept execution
- `starting -> failed`: startup/attach failed
- `connected -> disconnected`: linkage loss while the durable session may still exist
- `disconnected -> starting`: reconnect begins
- `connected -> stopping`: accepted `stop_session`
- `stopping -> stopped`: teardown completed
- `connected -> failed`: unrecoverable failure

Rules:

- backend is launched by the editor plugin
- sessions are durable/reconnectable by default
- one backend root process is expected to hold one durable session record
- the editor plugin may keep a persisted reconnectables registry across editor lifetimes
- timed-out durable sessions do not need proactive editor expiry events
- backend actively tears timed-out disconnected sessions down
- stale reconnectable entries may still be discovered later on attach/reconnect attempts
- backend session metadata keeps explicit `target` only
- backend session payloads may include `plugin_specs`, which are editor-facing presentation defaults keyed by magic name for plugins installed in this kernel environment
- backend session payloads may include `palette`, which is the editor-facing creation palette keyed by magic name and may include empty entry lists for built-in or non-config plugins
- disconnected sessions carry `expires_at` timeout metadata
- backend drives editor-link liveness with explicit healthchecks while sessions are `connected`
- missed editor healthcheck replies transition the session into normal `disconnected` timeout handling
- `endpoint`/backend residence stays outside core backend session state
- `disconnected` is not equivalent to kernel death
- `failed` should record a reason
- externally attached `connection_file` sessions may also participate in a small sidecar peer registry for shared disconnect timeout deadlines
- stop/restart cleanup unregisters only the current Jusi root process from that sidecar; peers are not signaled
- known issue: suspended Vim, for example via `Ctrl-Z`, may look like link loss to backend healthchecks

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

- cell status is separate from client allocation/binding details
- active cell keeps its client identity until a later lifecycle event changes it
- `follow-up` does not block later execution
- `parked` remains reserved for deliberate keep-output semantics
- `input_reply` resumes the same active execution after `input_request`
- handler-owned cells may publish `presentation` after execution handoff, when the backend has resolved the concrete plugin/provider presentation better than the session-level defaults
- session stop clears live runtime identity for active cells instead of leaving stale client ownership behind
- client-close of a follow-up cell normalizes that execution to `done` before clearing live runtime identity

## Client Lifecycle

States:

- `active`
- `shutting_down`
- `shutdown`

Rules:

- client teardown is separate from interrupt
- active-cell client teardown should surface through `client_state`
- once backend clears live runtime identity, `client_id`, `runtime_mode`, and transport metadata disappear from cell payloads rather than remaining as empty placeholders
- client view snapshots are derived state, not lifecycle state
- transcript-style client views may still use invalidation plus `inspect_client`
- native-terminal handler clients should render through the advertised terminal attach transport rather than `inspect_client`
- `inspect_client` remains useful for inspection/debugging of those clients, not as the primary fullscreen rendering path

## Execution Ownership

Owner kinds:

- `kernel`
- `handler`
- `unknown`

Rules:

- interrupt routing depends on the tracked runtime mode and must stay consistent with owner kind
- owner kind is independent from cell status
- disconnect may degrade active execution ownership to `unknown`
- reconnect should not invent a false owner if it cannot be restored safely
- `unknown` owner means there is no longer an active runtime controller for that execution
