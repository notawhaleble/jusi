# ADR 0043: Owned process death and transport loss

Status: accepted

## Decision

A service spawned by Neovim receives `serve --owner-stdin`. Neovim holds its
stdin writer open for that process's lifetime. EOF requests normal supervisor
shutdown, including when the editor is killed and cannot run exit callbacks.
An independently launched service does not enable this contract. Neither
network location nor transport loss grants the editor ownership of that service.

HTTP subprocesses have a separate owner pipe, independent of the request body.
A POSIX shell wrapper observes its EOF and terminates its exact curl child.
This avoids leaving an indefinite execute/input/followup request process behind
after editor death. No frontend-local service or proxy is introduced. The owner
pipe applies to local transport processes even for an external target.

The service periodically checks its owned kernel process. Observation does not
queue behind ordinary kernel work: active kernel adapter operations already
detect death. A busy plugin operation needs a separate death check because it
does not communicate with the kernel. Demonstrated death releases that work,
publishes the existing typed kernel failure and authoritative `off` event, and
retires the kernel's clients, surfaces and workers. A subsequent start allocates
a fresh kernel identity.

Explicit stop, restart and service shutdown first interrupt active work. If an
execution still prevents teardown after the operation's bounded wait, the
adapter kills its exact owned kernel process and teardown retries the operation
lane. This escalation is restricted to explicit teardown. Execution, input and
followup retain unlimited duration while their owner remains alive.

A plugin worker's private owner channel cannot reconnect. On channel EOF it
queues thread-affine close and starts a one-second watchdog before writing
diagnostics. If cleanup or work blocks, the watchdog terminates the worker's
owned POSIX process group. It checks that the worker is the group leader before
group termination. Broken diagnostic pipes cannot prevent cleanup.

The SSE curl connection has a ten-second low-speed threshold against the
service's one-second keepalive. A silent path failure therefore ends the event
connection. This is transport observation, not evidence of kernel death and not
an execution timeout. The frontend retains last-known kernel truth; reconnect
inspects authoritative state and replays events without resubmitting work.
Closing or losing the event connection also retires outstanding local HTTP
helpers. This closes transport requests, not target operations: remote work may
continue, and its result is reconciled through inspection/replay. It prevents a
discarded HTTP response from leaving a helper waiting forever after reconnect.

## Verification and limits

See [the reliability matrix](../architecture/lifecycle-reliability.md) and
[Incident 0017](../incidents/0017-process-death-cleanup.md). These probes cover
managed Jupyter and the terminal fixture on the development POSIX host. They do
not establish cleanup of arbitrary user-spawned daemons that detach from owned
groups, host power loss, or recovery into a new editor session. No heartbeat
lease makes an external runtime editor-owned.
