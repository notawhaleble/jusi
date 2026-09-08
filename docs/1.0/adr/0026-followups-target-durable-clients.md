# ADR 0026: Followups Target Durable Clients

- Status: accepted
- Date: 2026-09-08

## Decision

`:JusiFollowup` submits the current cell body to its one existing authoritative
client. The command requires the client's declared `followup` capability.
It does not execute Python, repeat a kernel handoff, create an execution, or
replace the client, worker, or presentation. Kernel stdin remains the distinct
execution-owned `JusiInput` operation. Context-sensitive submission mappings
remain deferred.

The HTTP command is `followup`, with `client_id` and a string `body`, at
`POST /v1/clients/{client_id}/followups`. Core resolves the immutable owning
worker from the client; it never accepts a worker import reference or guesses
a replacement client by cell identity. Unknown clients fail `not_found`; retired
client identities fail `conflict`. Unsupported operations leave the client live.

The worker receives `handle("followup", {"body": ...})`. Body lines join with
newlines without trimming, evaluating, stripping a magic header, or adding a
trailing newline. Empty bodies are valid. Structural delimiters and history are
excluded by the notebook model. Invalid cell structure prevents submission.
The exact plugin interprets the body in its already established client context.

Each followup has its own operation/trace identity and ordinary operation
started/completed events referencing the client. The body and plugin result are
absent from those events. The HTTP response returns the existing client resource
and the opaque worker `result`. The explicit editor command reports delivery
only after successful worker response. That acknowledgement describes delivery;
core does not interpret application-level success/error fields in the result.
Plugin presentation remains plugin-owned; arbitrary result objects are not
rendered as text or used to determine client lifetime.

## Lifecycle and limits

Followups use the existing serialized operation lane and bounded worker channel
(10-second worker request budget). Repeated local submission while a request is
in flight is rejected. Requests are not automatically retried after transport
failure: delivery may already have occurred. A successful result never restores
a client removed by a later close event.

Terminal surface creation belongs to the original handoff. A followup requesting
new surfaces is a protocol violation. Fatal worker/channel failure closes that
client and its surfaces through normal cleanup, with process diagnostics retained;
cleanup failure retains ownership for retry. Other clients and the kernel survive.
An application error returned in an ordinary result is not a fatal worker error.

Close and restart serialize behind an in-flight bounded request. Concurrent
plugin interrupt is still deferred; this command does not simulate it with
kernel interrupt. No durable kernel state or cell execution outcome is added.

## Verification

- `tests/conformance/test_followup.py` and `tests/frontend/followup_spec.lua`
  consume the shared command and operation-event scenario.
- Supervisor tests cover application-result lifetime, unsupported capability,
  fatal diagnostics, isolated close, unknown identity and stale identity after
  replacement.
- The real-kernel/exact-worker integration test checks literal multiline and
  empty followups preserve worker context and the initiating execution identity.
- The terminal Neovim end-to-end test sends repeated followups to the existing
  worker, checks its result and unchanged surface, and checks command acceptance.
