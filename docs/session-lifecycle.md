# Session Lifecycle Draft

This document defines the intended backend state transitions for the first implementation milestone.

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
- `starting -> connected`: kernel and initial prepared client are ready enough for execution
- `starting -> failed`: startup or attach failed
- `connected -> disconnected`: transport or remote-link failure while the kernel may still be alive
- `disconnected -> starting`: reconnect or attach flow begins again
- `disconnected -> failed`: reconnection is no longer possible or session identity is lost
- `connected -> stopping`: accepted `stop_session`
- `stopping -> stopped`: managed kernel stopped
- `connected -> failed`: unrecoverable backend or kernel failure

Rules:

- a session is not considered operational until the initial prepared-client path is also healthy enough for execution
- `failed` should always record a reason
- `disconnected` is not equivalent to kernel death
- for externally hosted kernels, `stop_session` should mean "stop this Jusi session binding" rather than "shut down the remote kernel"
- managed-session transport loss should tear down the session instead of entering `disconnected`
- `stop_session` should acknowledge promptly at `stopping`; the later `stopped` event may complete asynchronously after teardown finishes

## Prepared Client

States:

- `missing`
- `spawning`
- `binding`
- `ready`

Transitions:

- `missing -> spawning`: backend begins provisioning a client view
- `spawning -> binding`: backend-owned prepared client exists but frontend buffer binding is not complete
- `binding -> ready`: frontend reports a real client-buffer binding for the prepared client
- `spawning -> missing`: provisioning failed
- `ready -> spawning`: ready client was consumed by execution; replacement provisioning starts
- `binding -> missing`: session stop or failure before binding completes
- `ready -> missing`: session stop or failure

Rules:

- prepared-client state is notebook-session local
- the prepared client is a session-level warm execution resource, not a per-cell reservation
- only one prepared client is considered current for the notebook at a time
- execution consumes the current prepared client atomically from the frontend point of view
- once consumed, that client becomes the executing cell's active client and is no longer part of session prepared state
- replacement preparation begins only after that consume
- the backend must not claim `ready` until frontend buffer binding is acknowledged

## Cell Execution

States:

- `pending`
- `busy`
- `follow-up`
- `done`
- `error`
- `interrupted`
- `parked`

Transitions:

- `pending -> busy`: accepted execution on this cell
- `busy -> follow-up`: kernel execution returned but the cell remains active in a follow-up workflow
- `busy -> done`: execution finished without error
- `busy -> error`: execution ended with an error
- `busy -> interrupted`: interrupt was applied successfully
- `follow-up -> done`: follow-up workflow finished
- `follow-up -> error`: follow-up workflow failed
- `follow-up -> interrupted`: follow-up workflow was interrupted or cancelled

Rules:

- busy state is cell-local, not session-global
- the executing cell retains the consumed client reference
- terminal `done`, `error`, and `follow-up` updates retain that same client reference unless a later client-lifecycle event tears it down or changes ownership explicitly
- replacement prepared-client provisioning is independent from the active cell retaining its client
- a cell remaining in `follow-up` must not block execution of other cells once prepared-client state returns to `ready`
- `parked` remains a deliberate keep-output state, not a generic shutdown result
- a managed runtime may emit the initial `busy` cell update immediately and the terminal `done`/`error`/`follow-up` cell update later as a separate event while execution output is still being accumulated
- a managed runtime may also pause an active `busy` execution on Jupyter `input_request`; `input_reply` resumes that same execution rather than starting a new one

## Client Lifecycle

Client lifecycle is separate from cell execution status.

States:

- `active`
- `shutting_down`
- `shutdown`

Rules:

- graceful client teardown should be represented through client lifecycle fields where possible
- prepared-client teardown and cell-client teardown must not overload execution status
- managed-session transport loss should tear clients down immediately
- attachable-session transport loss may preserve session identity while client ownership becomes uncertain
- client runtime may maintain an internal derived view snapshot from lifecycle and execution data, but that view model does not define session or cell state by itself
- the derived client view snapshot may also expose a monotonic backend-owned `revision` for polling consumers; that revision is not a session/cell lifecycle state

## Execution Ownership

Cell activity may be owned by different execution controllers.

Owner kinds:

- `kernel`
- `handler`
- `unknown`

Rules:

- interrupt intent must be routed through the current execution owner
- owner kind is independent from user-visible cell status
- a magic cell may remain in `busy` while being owned by a `handler`
- reconnect may restore an active session without restoring a trustworthy execution owner, which should be represented as `unknown`

Interrupt policy:

- `kernel` owner: route interrupt to the kernel
- `handler` owner: route interrupt to the handler-specific cancellation path
- `unknown` owner: fail explicitly rather than pretending interruption succeeded

## Frontend Callback Mapping

Backend events should map cleanly onto the Vim-side callbacks:

- `session_updated` -> `jusi#session#callback_session()`
- `prepared_updated` -> `jusi#session#callback_prepared()`
- `cell_updated` -> `jusi#session#callback_cell()`

Combined envelopes are acceptable later, but the first implementation should prefer simple explicit events.

## External Kernel Note

External kernels exist specifically to survive local transport breakage such as SSH failure.

That implies:

- the backend must distinguish "lost linkage" from "kernel stopped"
- reconnect should target the existing remote kernel/session identity
- the user should be able to continue after the link is restored without treating the kernel as newly started
