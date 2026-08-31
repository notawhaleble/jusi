# Jusi 1.0 Failure Taxonomy

## Goals

A failure must answer:

- where did it originate?
- during which operation?
- what typed reason occurred?
- which resource and user scope were affected?
- which trace contains the surrounding chronology?
- did a process exit, receive a signal, or write useful stderr?
- can the caller retry safely?

## Origin Layers

- `protocol`: invalid or incompatible wire data
- `frontend_transport`: HTTP/SSE reachability, cursor, or stream failure
- `frontend_model`: local notebook structure, cell identity, or controller precondition failure; this layer is currently local-only and is not emitted by the backend SSE stream
- `frontend_presentation`: local renderer selection or output-surface failure; this layer is currently local-only and is not emitted by the backend SSE stream
- `frontend_service`: local service spawn, readiness, exit, or owned-process cleanup failure; this layer is local-only and is not emitted by backend SSE
- `service`: request routing, persistence, configuration, or internal service failure
- `supervisor`: ownership, orchestration, cleanup, and child supervision
- `kernel`: kernel process, readiness, channels, or kernel protocol
- `execution`: code execution, input, interrupt, or output handling
- `client`: output/interaction resource failure
- `plugin_worker`: isolated plugin runtime or plugin control failure

The reporting layer must retain the originating layer rather than replacing it with its own.

## Typed Reasons

Initial stable reason codes:

- `invalid_request`
- `unsupported`
- `not_found`
- `conflict`
- `unreachable`
- `timeout`
- `cancelled`
- `spawn_failed`
- `readiness_failed`
- `process_exited`
- `process_signalled`
- `channel_closed`
- `protocol_violation`
- `kernel_died`
- `execution_error`
- `interrupted`
- `plugin_error`
- `cleanup_incomplete`
- `capacity_exceeded`
- `internal_error`

New codes require protocol fixtures and documented retry/scope semantics. User code exceptions use `execution_error`; they are not kernel failures.

## Failure Record

Required fields:

```json
{
  "failure_id": "fail_...",
  "trace_id": "trace_...",
  "layer": "kernel",
  "operation": "start_kernel",
  "reason": "readiness_failed",
  "message": "Kernel exited before becoming ready",
  "retryable": true,
  "scope": "kernel",
  "resource": {"kind": "kernel", "id": "krn_..."},
  "occurred_at": "2026-08-31T12:00:00Z"
}
```

Optional diagnostic fields:

- `process.pid`
- `process.exit_code`
- `process.signal`
- `process.stderr_excerpt`
- `process.stderr_truncated`
- `details`: bounded, non-secret structured data
- `caused_by_failure_id`

## Scope

Allowed initial scopes:

- `request`
- `transport`
- `execution`
- `cell`
- `client`
- `plugin_worker`
- `kernel`
- `supervisor`

Scope describes demonstrated impact, not guessed severity. Propagating to a wider scope requires a recorded causal link.

## Containment Examples

- Python exception from executed code: `execution / execution_error / execution`.
- Plugin process exits unexpectedly: `plugin_worker / process_exited / plugin_worker`; owning client may close, kernel stays `on`.
- SSE connection drops: `frontend_transport / channel_closed / transport`; kernel state is unchanged.
- Kernel process exits during execution: kernel failure scoped to `kernel`, with a caused execution failure scoped to `execution`; kernel becomes `off`.
- Stop cannot terminate one child: `supervisor / cleanup_incomplete`, with per-resource cleanup results.

## Redaction

Diagnostics must not include credentials, complete environment mappings, arbitrary cell bodies, or unbounded output. Stderr excerpts are bounded and marked when truncated. Trace correlation must not depend on logging sensitive payloads.

Execution diagnostics currently record only UTF-8 code byte count and line count. They do not retain the submitted body.
