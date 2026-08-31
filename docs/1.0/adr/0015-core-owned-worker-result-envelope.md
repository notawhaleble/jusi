# ADR 0015: Core Owns The Worker Result Envelope

- Status: proposed
- Date: 2026-08-31

## Context

ADR 0011 correctly makes exact-provider request payloads and results opaque to
core. Taken literally for the complete worker response, however, that leaves no
generic way to publish media, establish whether a client remains usable, or
carry presentation metadata. Sending the whole object to Neovim would require
the core frontend to understand every exact plugin. Interpreting provider data
inside the supervisor would violate the plugin boundary in the opposite
direction.

The 0.x implementation is evidence for the needed concepts, not its protocol:
plugin runtimes emitted execution events, client-lifetime changes, presentation
metadata, and plugin messages separately. Its `busy`/`follow-up` states,
callback topology, and `handler_message` envelope should not be restored.

## Proposed Decision

Keep each operation's plugin-owned request and response payload opaque, but
place the response payload inside a small core-owned client-result envelope:

```json
{
  "client": {
    "disposition": "open",
    "presentation": {"syntax": "sqlite", "indent": "sql"}
  },
  "outputs": [
    {"output_kind": "result", "media_type": "text/x-ansi", "data": "2 rows\n"}
  ],
  "payload": {"provider_owned": "value"}
}
```

The closed, versioned envelope has three responsibilities:

- `client.disposition` is `open` or `closed`; it is a lifetime instruction, not
  an execution or notebook state
- `client.presentation`, when present, contains bounded editor presentation
  hints transported by core without selecting a renderer
- `outputs` contains bounded media records following the same media-driven
  presentation rules as kernel output

`payload` remains a bounded JSON object whose meaning belongs to the declared
plugin family/exact provider. Core may correlate and forward it but never uses
its contents to decide kernel, runtime, worker, or client truth.

After a successful kernel handoff, core would:

1. validate exact plugin/version/family/magic identity against the immutable
   runtime catalog
2. allocate and start a worker already bound to the execution's `client_id`
3. send the opaque handoff payload as the worker's `execute` request
4. validate the outer client-result envelope
5. publish the worker/client identity and media records in deterministic order
6. retain the worker only for `open`, otherwise stop it explicitly
7. complete only the owning execution; worker failure normally leaves the
   kernel `on`

Catalog capabilities and interaction kind remain authoritative. A worker
cannot add capabilities, change identity, select its own transport, or submit a
process command to the frontend. The first connected slice supports
`request_response` workers only. Terminal/PTY clients require their separately
designed target-side transport and are not tunneled through this envelope.

If the kernel execution itself fails, no worker is started. A successful
handoff and a failed kernel reply in the same execution is a protocol violation,
not a partial client start.

## Alternatives Rejected By This Proposal

### Forward The Entire Opaque Result

This preserves worker opacity but forces generic Neovim code to know exact
provider response formats before it can render output or manage a client.

### Let Core Interpret Provider Payloads

This recreates the 0.x handler hierarchy inside the service and makes plugin
failures and evolution part of core behavior.

### Model `busy` And `follow-up` As Client States

Those labels mix operation progress and UI affordances with lifetime. An open
client plus explicit in-flight operations conveys the same facts without a
second hidden state machine.

## Consequences If Accepted

- ADR 0011 must be clarified: plugin payloads are opaque; the surrounding
  worker result envelope is a shared contract.
- Schema, Python, Lua, fixtures, events, and tests must change atomically.
- Kernel and plugin text with the same media type take the same presentation
  path.
- Exact-provider presentation can refine family defaults without letting the
  provider choose a Neovim surface.
- Asynchronous worker events, action requests, and terminal transport remain
  later explicit contracts rather than being smuggled into `payload`.
