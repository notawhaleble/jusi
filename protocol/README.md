# Jusi Protocol

This directory contains the versioned, language-neutral Jusi 1.0 wire contract.

Protocol version `1` is a new HTTP/SSE contract. It is not the line-delimited stdio protocol whose historical envelopes also used the integer `1`.

## Baseline Transport

- HTTP commands and resource inspection
- SSE for ordered backend-to-frontend events
- Neovim terminal rendering for textual/ANSI output
- a dedicated PTY or stream only for genuinely interactive, bidirectional, or high-volume clients

Terminal presentation does not determine kernel ownership or lifecycle.

## Event Rules

- Every supervisor event has a monotonically increasing `sequence` within that supervisor's event epoch.
- Every event has an `event_id`, `occurred_at`, `trace_id`, `layer`, and `operation`.
- Resource events carry explicit typed resource references.
- Failures use the shared failure shape; free-form strings are supplementary diagnostics, not the taxonomy.
- Every declared event kind has a closed payload shape. Python and Lua reject missing fields, unknown payload fields, invalid outcomes, and resource/trace identity mismatches before dispatch.
- SSE resumption uses the last observed event identifier or cursor. The frontend does not fill gaps by guessing state.
- The SSE adapter emits an immediate comment to confirm that the stream is open. Comments carry no resource truth and consume no event sequence.
- Before opening SSE, the frontend inspects health for the supervisor identity, authoritative kernel snapshot, and retained event window. ADR 0008 defines when to replay and when to replace stale frontend state from that snapshot.

The initial workflow fixture is `fixtures/v1/scenarios/walking-skeleton.json`; `fixtures/v1/scenarios/event-payloads.json` covers every initial event kind. Concrete valid and invalid envelopes under `fixtures/v1/` are consumed by both Python and Lua conformance tests.

## Plugin Catalog

`schema/v1/plugin-catalog.schema.json` defines the data-only catalog boundary
from ADR 0010. The valid fixture deliberately contains two exact providers
claiming the same `sql` family; provider coexistence is not itself a conflict.
Duplicate exact `plugin_id` values are rejected by both Python and Lua.
