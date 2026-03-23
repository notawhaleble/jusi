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

## Prepared Client

States:

- `missing`
- `spawning`
- `ready`

Transitions:

- `missing -> spawning`: backend begins provisioning a client view
- `spawning -> ready`: client view is ready for execution handoff
- `spawning -> missing`: provisioning failed
- `ready -> spawning`: ready client was consumed by execution; replacement provisioning starts
- `ready -> missing`: session stop or failure

Rules:

- prepared-client state is notebook-session local
- only one prepared client is considered current for the notebook at a time
- execution consumes the current prepared client atomically from the frontend point of view

## Cell Execution

States:

- `pending`
- `busy`
- `follow-up`
- `done`
- `error`
- `interrupted`

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
- replacement prepared-client provisioning is independent from the active cell retaining its client
- a cell remaining in `follow-up` must not block execution of other cells once prepared-client state returns to `ready`

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
