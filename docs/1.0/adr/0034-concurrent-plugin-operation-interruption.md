# ADR 0034: Concurrent, Exact Plugin Operation Interruption

- Status: accepted
- Date: 2026-09-09

## Decision

An established client's ordinary `followup`, `complete`, or `editor_action`
operation has a supervisor `operation_id`. Health's optional `client_operations`
list and ordered operation events expose that identity with its `client_id` and
kind. `interrupt_client` names both identities and bypasses the serialized
operation lane. It never falls through to newer work. `JusiInterrupt` selects
this work before considering the cell's kernel execution.

The supervisor supplies the operation ID as the private worker request ID.
Ordinary requests remain serialized. The private framed channel multiplexes
replies by registered request ID; writes cannot interleave. A child control
reader invokes `worker.interrupt()` concurrently with ordinary work, only while
the exact request is active. Factory creation, `handle()`, and `close()` remain
on the same thread, preserving thread-affine sessions. This replaces ADR 0011's
single blocking reader and completes the established-client portion of ADR 0021.

The hook requests cancellation promptly; it must not wait for `handle()` or close
the session. A successful hook response acknowledges the request, not completion.
A plugin raises `OperationInterrupted` when work actually stops. Core completes
that operation as `cancelled`, without a synthetic failure, preserving its
client and surfaces. Repeated interrupt while the same accepted work is active
returns `already_requested`.

`OperationRejected` produces an explicit `worker.rejected` response. These
recoverable failures preserve the worker and client. Unexpected exceptions,
invalid control data, and demonstrated process/channel loss remain fatal.
Interrupt-hook exceptions and control deadline expiry do not themselves prove
client death. Fatal interrupt-channel failures explicitly clean the client even
if the ordinary operation completed concurrently.

Followups have no default execution deadline, including their HTTP requests.
Connection establishment, completion, export, and lifecycle controls remain
bounded. Explicit close fences new client submissions and can terminate a busy
worker without waiting for its request lock. Stop/restart and observed required
terminal loss use the same independent cancellation boundary before serialized
cleanup. Cleanup is distinct from a session-preserving interrupt.

## Boundary

This slice covers work submitted through core's established-client operation
API. Initial worker construction remains a bounded handoff. Work started wholly
inside a plugin's terminal application has no core work lease yet; a plugin must
not advertise that a terminal process signal implements this contract. Such work
publication is a separate contract extension, to be designed with its use cases.

## Verification

- `tests/backend/integration/test_plugin_worker.py`: two successive exact
  interruptions, stale targets, failing hooks, recoverable errors, usable worker
  afterwards, and ordinary-thread affinity.
- Shared Python/Lua command, worker, and health fixtures validate exact identity.
- `tests/e2e/terminal_surface_spec.lua`: responsive health during followup,
  cancellation, stale HTTP rejection, same client/surface, and closing busy work.
