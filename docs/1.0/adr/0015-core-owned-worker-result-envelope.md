# ADR 0015: Core Owns The Worker Operation Result Envelope

- Status: proposed
- Date: 2026-09-01

## Context

ADR 0011 correctly makes exact-provider request and response payloads opaque to
core. Taken literally for the complete worker response, however, that leaves no
generic way to publish media or carry presentation metadata. Sending the whole
object to Neovim would require generic frontend code to understand every exact
plugin. Interpreting provider data inside the supervisor would violate the
plugin boundary in the opposite direction.

An earlier version of this proposal also asked every operation result whether
the client should remain `open` or become `closed`. Review rejected that model:
a plugin client is durable. Running one SQL statement, shell command, follow-up,
or completion does not raise a client-lifetime question.

## Durable Client Lifetime

A successful exact-provider handoff creates a durable plugin client. Ordinary
worker operation results never decide whether that client continues to exist.

The client ends only when:

- the user explicitly requests client close/stop
- the client or worker suffers a demonstrated fatal failure
- its owning notebook runtime is explicitly stopped, fully restarted, or lost

An operation-level error is not automatically fatal. It fails that operation
while the client remains available unless the worker process/channel is lost or
the plugin explicitly reports an unrecoverable client failure.

`open`, `closed`, `busy`, and `follow-up` therefore do not belong in ordinary
worker results. In-flight work is represented by operation identity/outcome;
client existence is represented by the authoritative client resource.

## Proposed Result Boundary

Keep each operation's plugin-owned request and response payload opaque, but
place response data inside a small core-owned result envelope:

```json
{
  "presentation": {"syntax": "sqlite", "indent": "sql"},
  "outputs": [
    {"output_kind": "result", "media_type": "text/x-ansi", "data": "2 rows\n"}
  ],
  "payload": {"provider_owned": "value"}
}
```

The closed, versioned envelope has two generic responsibilities:

- `presentation`, when present, contains bounded editor presentation hints
  transported by core without selecting a renderer
- `outputs` contains bounded media records following the same media-driven
  presentation rules as kernel output

`payload` remains a bounded JSON object whose meaning belongs to the declared
plugin family/exact provider. Core may correlate and forward it but never uses
its contents to decide kernel, runtime, worker, or client truth. Here “opaque”
means only “not semantically interpreted by core”; it says nothing about
lifetime, secrecy, encoding, or visibility.

After a successful kernel handoff, core would:

1. validate exact plugin/version/family/magic identity against the immutable
   runtime catalog
2. allocate a durable client and start its exact worker
3. send the opaque handoff payload as the worker's `execute` request
4. validate the outer operation-result envelope
5. publish worker/client identity and media records in deterministic order
6. complete only the owning execution while retaining the client for later
   declared operations

Catalog capabilities and interaction kind remain authoritative. A worker
cannot add capabilities, change identity, select its own transport, or submit a
process command to the frontend. The first connected slice supports
`request_response` workers only. Terminal/PTY clients require their separately
designed target-side transport and are not tunneled through this envelope.

If kernel execution itself fails, no worker is started. A handoff combined with
a failed kernel reply is a protocol violation, not a partial client start.

## Alternatives Rejected By This Proposal

### Let Each Operation Decide Client Lifetime

Plugin clients represent continuing workflows. Treating every result as a
possible “last result” introduces a lifecycle branch with no product meaning
and invites accidental cleanup after ordinary SQL, shell, or follow-up work.

### Forward The Entire Opaque Result

This preserves worker opacity but forces generic Neovim code to know exact
provider response formats before it can render output.

### Let Core Interpret Provider Payloads

This recreates the 0.x handler hierarchy inside the service and makes plugin
evolution part of core behavior.

### Model `busy` And `follow-up` As Client States

Those labels mix operation progress and UI affordances with lifetime. A durable
client plus explicit in-flight operations conveys the facts without a second
hidden state machine.

## Consequences If Accepted

- ADR 0011 must be clarified: plugin payloads are opaque; the surrounding
  operation-result envelope is a shared contract.
- Schema, Python, Lua, fixtures, events, and tests must change atomically.
- Kernel and plugin text with the same media type take the same presentation
  path.
- Exact-provider presentation can refine family defaults without letting the
  provider choose a Neovim surface.
- Explicit client close and fatal client loss require typed operations/events;
  neither is inferred from an ordinary result.
- Asynchronous worker events, action requests, and terminal transport remain
  later explicit contracts rather than being smuggled into `payload`.
