# ADR 0011: Plugin Workers Use A Private Bounded Control Channel

- Status: accepted
- Date: 2026-08-31

## Context

Exact plugins need code outside both the kernel and the long-lived service. The
control path must preserve worker, client, execution, operation, and trace
identity without making terminal interaction part of the kernel-control
protocol. It must also remain usable when a plugin writes arbitrary text or
terminal escape sequences to standard output.

## Decision

Core starts one exact-plugin worker for one owning client/execution in the
initial implementation. A worker is never shared implicitly across notebook
runtimes or promoted to kernel lifetime.

The worker process uses a private, bounded, length-prefixed JSON control channel
over descriptors duplicated from its launch pipes. Before importing plugin
code, the child duplicates both machine descriptors, redirects ordinary stdin
to null, and redirects ordinary stdout to stderr. Plugin output therefore cannot
corrupt control frames and remains available as bounded process diagnostics.
This channel is an internal supervisor/worker transport, not a terminal stream
and not a frontend transport.

The language-neutral envelope records protocol version, worker identity,
request identity, trace identity, and a generic operation. Core validates the
envelope and declared catalog capability but does not interpret plugin-owned
payload or result objects. Requests are serialized per worker in v1. The initial
operations are `execute`, `followup`, `complete`, and `editor_action`; lifecycle
shutdown is a separate control kind. True interrupt cannot be implemented over
a channel blocked by an in-flight request, so it remains deferred with the
dedicated concurrent-control design.

The Python `worker_entry_point` resolves inside the child process to a factory.
The factory receives an immutable `WorkerContext` and returns an object whose
`handle(operation, payload)` method returns a JSON-compatible object. An
optional `close()` method is invoked during graceful shutdown. Neither discovery
nor the service imports this entry point.

PTY input/output, resize, signals, and high-volume streaming require a separate
capability and transport. They will not be tunneled through this control
channel.

Process isolation is a reliability boundary, not a security sandbox. A worker
still runs with the launching user's filesystem and environment permissions.

## Consequences

- A worker crash or malformed response is attributable to one
  `plugin_worker_id` and normally leaves the kernel on.
- Worker stdout cannot become accidental protocol data.
- Control messages and plugin results have explicit size and time limits.
- Full restart and cleanup can terminate worker process groups independently
  and report `stopped`, `already_absent`, or a typed failure.
- The one-request-at-a-time baseline is intentionally simple; concurrency can
  later use multiple owned workers or a versioned multiplexing decision.
