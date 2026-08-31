# Jusi 1.0 State And Resource Model

## Resources

### Local Service Process

An optional frontend-owned Python service process for one local notebook target.

- identity: `service_process_id`, distinct from the service's `supervisor_id`
- lifetime: explicit service start/stop or owning notebook-buffer destruction
- diagnostics: PID, bounded stderr, exit code/signal, and readiness failure
- transport disconnect and kernel stop do not by themselves destroy this process

Externally configured and remote service URLs have no frontend-owned service
process resource and are never terminated by local cleanup.

There is no mandatory local service process for a remote target. In that case
Neovim's HTTP/SSE transport connects directly, possibly through an SSH tunnel or
port forward, to the service running beside the target kernel.

### Supervisor

The authoritative service instance managing kernels and their child resources.

- identity: `supervisor_id`
- lifetime: service process or durable remote supervisor epoch
- owns: kernel generations, executions, clients, plugin workers, event sequence

A restarted non-durable supervisor receives a new identity and event epoch.
The supervisor is target-side. Its plugin catalogs and workers are scoped to a
notebook runtime generation, not to a Neovim instance or OS login session.

### Plugin Discovery

One short-lived process that imports versioned plugin catalog providers and then exits.

- identity: `discovery_id`, unique to one catalog attempt
- lifetime: one bounded discovery operation; it never becomes a plugin worker
- output: either one complete validated data-only catalog or one typed failure
- process boundary: the supervisor does not import provider packages

Every attempt uses a fresh interpreter and process group. A successful notebook
runtime owns the resulting immutable catalog snapshot, not the discovery
process. A failed entry prevents publication of a partial authoritative catalog.

### Notebook Runtime

One authoritative runtime generation binding a frontend notebook model, one
validated plugin catalog snapshot, and one kernel generation.

- identity: `runtime_id`
- owns correlations to: `notebook_id`, `discovery_id`, `kernel_id`
- catalog: immutable for this generation and discarded on replacement
- lifetime: successful start until replacement; it may remain inspectable with
  an `off` kernel after stop or observed kernel death

The health snapshot publishes the runtime and kernel together and validates
their notebook/kernel ownership. A live kernel without an authoritative runtime
is a protocol violation.

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
- lifetime: durable within its owning notebook runtime and independent from
  individual operation results or frontend buffer/window lifetime

A successful exact-plugin handoff creates the client. It remains a valid target
for its declared sequence of operations until explicit user close, demonstrated
fatal client/worker loss, or owning-runtime cleanup. Ordinary SQL statements,
shell commands, follow-ups, completions, action results, and recoverable plugin
errors do not implicitly close it.

A Neovim terminal buffer is a frontend projection of a client, not the client identity.

### Client Surface

A generic frontend projection requested by one durable backend client.

- identity: `surface_id`, owned by one `client_id`
- kind: `terminal` or `web`
- transport: explicit and target-safe; never inferred from plugin identity
- capabilities: generic input, resize, signal, focus, or browser controls
- content: backend-produced and opaque to core; interpreted by the terminal
  emulator or browser
- lifetime: explicitly closed or retired with its owning client/runtime

A client may produce recoverable application errors on its own surface. Loss of
a required surface is instead a typed core `client` failure because plugin
presentation may no longer be available.

### Plugin Worker

An isolated plugin-owned runtime when plugin behavior needs a separate process or failure boundary.

- identity: `plugin_worker_id`
- owner: one `client_id` and originating `execution_id` within one
  `runtime_id`, exact `plugin_id`, and `family_id`
- selection: the import reference comes only from the current immutable catalog;
  clients never submit an arbitrary worker entry point
- lifetime: explicitly fenced and stopped; never silently promoted to kernel or
  supervisor lifetime

The initial worker control path serializes bounded `execute`, `followup`,
`complete`, and `editor_action` requests. Payloads and results are opaque
JSON-compatible objects; core validates identity and declared capability.
“Opaque” means core does not interpret plugin-specific fields; it is unrelated
to client lifetime. The worker remains owned by its durable client across
ordinary operation results.
Concurrent interrupt and terminal interaction are deferred to transports that
can operate independently of an in-flight request.

Full restart cleans every worker before stopping the old kernel or publishing a
replacement runtime. Incomplete worker cleanup aborts replacement with the old
kernel still authoritative. Kernel stop attempts worker cleanup first but still
turns a successfully stopped kernel `off`, reporting any remaining worker
cleanup failure explicitly.

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
- Start performs fresh plugin discovery before spawning a kernel and publishes no runtime if discovery or startup fails.
- After Jupyter readiness, every catalog-declared kernel adapter is imported in
  the fresh kernel and must attest matching exact plugin version and family
  claims before readiness is authoritative.
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

Protocol v1 now exposes `restart_notebook`, targeting the current `runtime_id`,
`kernel_id`, and `notebook_id` while carrying a frontend-allocated
`next_notebook_id`. The response publishes the replacement runtime, discovery,
catalog, and kernel identities. A failure after teardown includes
`details.teardown_completed=true` so the frontend retires the old model and
runtime projections even though replacement startup did not succeed.

## Remote Checking

The frontend may show `checking` only while asking a remote supervisor:

1. whether the supervisor is reachable
2. whether it authoritatively owns the referenced kernel generation

The result must resolve to `on`, `off`, or a transport-level failure that leaves kernel truth unknown to that frontend. It must not resolve to `failed` or `disconnected` kernel states.
