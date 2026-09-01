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
- Consecutive `execution.output` events are ordered terminal writes. Their data
  must be concatenated without inserting separators; the service may split one
  kernel text message at a UTF-8-safe boundary to keep retained events bounded,
  as specified by ADR 0018.

The initial workflow fixture is `fixtures/v1/scenarios/walking-skeleton.json`; `fixtures/v1/scenarios/event-payloads.json` covers every initial event kind. Concrete valid and invalid envelopes under `fixtures/v1/` are consumed by both Python and Lua conformance tests.

## Plugin Catalog

`schema/v1/plugin-catalog.schema.json` defines the data-only catalog boundary
from ADR 0010. The valid fixture deliberately contains two exact providers
claiming the same `sql` family; provider coexistence is not itself a conflict.
Duplicate exact `plugin_id` values are rejected by both Python and Lua.
One kernel-extension module may belong to only one exact plugin in a catalog.

## Kernel Plugin Control

`schema/v1/plugin-kernel.schema.json` defines the private kernel adapter
attestation and exact-provider handoff records from ADR 0013. These records use
dedicated Jupyter MIME types; they are consumed by the target-side service and
never presented as cell output. The service accepts at most one handoff per
execution and bounds each encoded control record to 1 MiB. A handoff remains
opaque until its exact plugin, version, family, and magic identity match the
immutable runtime catalog.

## Plugin Worker Control

`schema/v1/plugin-worker.schema.json` defines the generic private control
envelope from ADR 0011. It is not an HTTP/SSE or terminal transport. Python and
Lua consume the same identity, operation, result, and failure fixtures even
though only the Python supervisor/worker boundary currently carries these
messages. Plugin-owned payload and result objects remain opaque to core. Every
successful worker result also carries a `core_requests` array. The first
supported typed request is `terminal_surface.create`: the worker supplies a target-side argv,
absolute-or-null cwd, environment overrides, and generic terminal capabilities;
it never supplies a frontend command or credentials.
These requests are private backend control data and are never forwarded as an implicit
frontend plugin API. Frontend-visible plugin behavior uses versioned generic
client/surface, control, action, and failure contracts from ADR 0015.

## Plugin Client Lifecycle

A successful exact-plugin handoff creates a durable `client` resource and an
exact target-side worker. `client.created` announces that identity; ordinary
plugin application errors remain plugin-rendered, while fatal worker/control
failures use `failure.occurred` and retire only that client. The explicit,
idempotent `close_client` command stops the exact worker and emits
`client.closed`. Kernel stop and full notebook restart retire remaining clients
as runtime cleanup.

## Terminal Surfaces

A terminal surface is a public resource owned by one client and notebook
runtime. Health snapshots include all current `surfaces`, while
`surface.created` and `surface.closed` provide the ordered lifecycle. The
descriptor contains only generic capabilities and a relative WebSocket endpoint
using subprotocol `jusi.terminal.v1`; it never exposes a target process command,
environment, or authentication data to the frontend. Terminal bytes and attach
control belong to the dedicated stream described by ADR 0016, not SSE.

`schema/v1/terminal-stream.schema.json` closes every JSON text control on that
WebSocket. The client begins with `attach`, naming the surface, a fresh
`attachment_id`, the next output byte cursor it expects, and the actual terminal
geometry. The server sends `attached` only after it has accepted that cursor and
applied and verified those rows and columns at the target PTY. `resize` and
`resized` share a fresh `resize_id`; the acknowledgement echoes the exact
geometry that was applied. Rows and columns are integers from 1 through 65535.

Cursors are canonical decimal strings in the inclusive uint64 range
`0..18446744073709551615`. A cursor names the next byte to consume, so replay
starting at cursor `N` begins with byte `N`. Strings preserve all uint64 values
across JSON implementations, including Lua runtimes whose numbers cannot
represent every uint64 exactly. An unavailable retained cursor yields the typed
`cursor_expired` failure; it never silently attaches at a newer position.

Server-to-client terminal output uses binary WebSocket frames with this fixed
10-byte header followed by uninterpreted terminal bytes:

| Offset | Size | Meaning |
| --- | ---: | --- |
| 0 | 1 | framing version, exactly `1` |
| 1 | 1 | frame type, exactly `1` (terminal output) |
| 2 | 8 | starting byte cursor, unsigned big-endian uint64 |
| 10 | remaining | raw PTY output bytes |

The next cursor after consuming a frame is its starting cursor plus the raw
payload byte length. Client-to-server binary WebSocket frames contain only raw
PTY input bytes and have no Jusi header. Neither direction decodes UTF-8 or ANSI.
Empty output payloads are valid framing-wise, though implementations need not
emit them.

Stream failures are closed JSON controls with operation `attach`, `resize`, or
`stream` and one of `busy`, `cursor_expired`, `not_found`,
`protocol_violation`, or `channel_closed`. They describe this attachment only;
they do not alter kernel state. The first protocol version permits one active
input attachment per surface, so a competing attach fails with `busy`.
