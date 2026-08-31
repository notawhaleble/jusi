# Jusi 1.0 State And Resource Model

## Resources

### Supervisor

The authoritative service instance managing kernels and their child resources.

- identity: `supervisor_id`
- lifetime: service process or durable remote supervisor epoch
- owns: kernel generations, executions, clients, plugin workers, event sequence

A restarted non-durable supervisor receives a new identity and event epoch.

### Kernel

One concrete kernel generation.

- identity: `kernel_id`
- association: optional notebook/model reference
- state: `off` or `on`
- owner: one supervisor

`checking` is an exceptional remote-facing view while supervisor reachability and ownership are being verified. It is not a kernel-process lifecycle phase and is never used by the local supervisor as a stored intermediate state.

Transitions:

- no current generation / `off` -> `on`: successful start operation and readiness observation
- `on` -> `off`: successful stop, observed death, ownership loss resolved as absent, or cleanup
- `off` -> `off`: idempotent stop

There is no kernel reconnect transition.

### Operation

A bounded attempt to change or inspect resources.

- identity: `operation_id`
- trace: `trace_id`
- kind: `start_kernel`, `stop_kernel`, `restart_notebook`, `execute`, `interrupt`, `cleanup`, `inspect`, or transport-specific operations
- outcome: `pending`, `running`, `succeeded`, `failed`, or `cancelled`

Operation outcome is not copied into kernel state. A failed stop can leave an observed kernel `on`; an unexpected process exit leaves it `off` even though no stop operation succeeded.

### Execution

One request evaluated by one kernel generation.

- identity: `execution_id`
- owns correlation to one `kernel_id`, model `cell_id`, and optional `client_id`
- outcome: `pending`, `running`, `succeeded`, `failed`, `interrupted`, or `cancelled`

Execution outcome is local to that execution. It is not a notebook or kernel health state.

### Client

A backend-visible output or interaction resource associated with an execution or plugin worker.

- identity: `client_id`
- capabilities: declared presentation and interaction requirements
- lifetime: independent from frontend buffer/window lifetime

A Neovim terminal buffer is a frontend projection of a client, not the client identity.

### Plugin Worker

An isolated plugin-owned runtime when plugin behavior needs a separate process or failure boundary.

- identity: `plugin_worker_id`
- owner: one client or execution
- lifetime: never silently promoted to kernel or supervisor lifetime

### Frontend Transport

One HTTP/SSE connectivity epoch between a frontend and supervisor.

- identity: `transport_id`
- loss or renewal does not change kernel state
- resumption uses event cursor/replay or authoritative resource inspection
- every stream epoch begins with inspection of supervisor identity, kernel snapshot, and retained event window
- supervisor replacement or an unavailable cursor replaces stale frontend resource state from that snapshot; a replayable cursor consumes missing events in order

### Notebook And Cell

Frontend model resources.

- `notebook_id`: frontend model identity
- `cell_id`: stable model identity, anchored to text with extmarks

Backend resources may reference these identifiers for correlation, but do not own their coordinates or presentation state.

## Start Semantics

- Start is accepted only when no owned live kernel generation exists, unless an idempotency key identifies the same in-flight request.
- The operation may be `running` while the visible kernel remains `off`.
- Readiness changes the kernel to `on` and emits the authoritative event.
- Startup failure leaves the kernel `off` and returns a structured failure.

## Stop Semantics

- Stop targets a concrete `kernel_id` or explicitly requests cleanup of the current owned generation.
- Repeating stop is successful and reports the resource as already absent/off.
- Stop response distinguishes `stopped`, `already_absent`, and `failed` resource results.
- Observed kernel death changes state to `off` even if cleanup of secondary resources continues.

## Restart Semantics

`restart_notebook` is a composite product operation, not a kernel state and not an in-place kernel-process restart.

- It fences and removes the current kernel generation and all owned executions, clients, plugin workers, channels, and runtime bindings.
- It preserves current buffer text and undo history but rebuilds the frontend notebook model and extmarks from that text.
- It reloads configuration and target resolution from canonical sources.
- It rediscovers plugins and rebuilds capabilities and kernel-extension configuration without surviving import/discovery caches.
- It creates a new kernel process and `kernel_id`.
- It publishes a fresh authoritative plugin/capability snapshot for the new generation.
- Teardown failure prevents a silent second kernel from being started.
- Failure after successful teardown leaves the kernel `off` and does not restore stale runtime state.

The protocol command for this operation is intentionally deferred until schema, Python, Lua, fixtures, and conformance coverage can land atomically.

## Remote Checking

The frontend may show `checking` only while asking a remote supervisor:

1. whether the supervisor is reachable
2. whether it authoritatively owns the referenced kernel generation

The result must resolve to `on`, `off`, or a transport-level failure that leaves kernel truth unknown to that frontend. It must not resolve to `failed` or `disconnected` kernel states.
