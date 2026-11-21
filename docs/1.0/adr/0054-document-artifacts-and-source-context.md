# ADR 0054: Document artifacts and source context

- Status: accepted and implemented; pending the 1.0.7 release
- Date: 2026-11-02

Extend the existing capability-checked editor action channel with document
delivery and source snapshot requests. No plugin connects to Neovim RPC.
Application IPC remains target-local; HTTP carries content between machines,
and SSE carries bounded action metadata. Existing rich MIME output is unchanged.

Artifacts use a versioned, streamed manifest and base64 data records. Transfers
stage privately at each end, validate names, lengths and SHA-256 before use,
and never interpret a target pathname as an editor pathname. Requests and
replies share this encoding. Replies are accepted only for the pending action's
owning editor, with ordered offsets, completion and the existing inactivity
lease. Client closure cancels pending transfers.

Source capture is opt-in through a source_context capability. A palette
submission captures the host's immutable document snapshot before switching
windows. An opaque context identity travels separately from literal prompt text
on initial execution and followup. Context is never found by matching prompts.
Requests without a selection can explicitly snapshot a bound source document.

Presentation providers declare MIME types. Core selects a compatible installed
provider, optionally constrained by an explicit provider preference. Independent
documents are reusable by content identity and survive client close. Managed
documents belong to one client slot, overlay its terminal, and close with that
client. No reading-specific branches belong in core or the host.

The host negotiates source.snapshot with installed reader workers. Each request
has its own identity and private staging directory; page navigation does not
retarget accepted work. Readers export text with provenance and an unannotated
full-page PNG from their immutable document, independently reporting component
failure. Raw image bytes never enter worker control frames. This does not expose
provider HTTP endpoints or Neovim IPC to document scripts.

Application helpers expose these operations through the existing client channel;
they are not new worker-result commands. Snapshot cancellation shuts down that
request, releases partial replies and cancels pending host work while retaining
the client for subsequent requests. Delivery acknowledgment means provider
readiness, not merely successful upload.

The generic artifact limit is 256 MiB across 1–16 files, with 16 KiB metadata,
24 KiB decoded blocks and 64 KiB wire records. A reader snapshot is additionally
limited by the host to eight files / 32 MiB. Transfer does not add filesystem
mounts, general RPC, live HTTP tunneling or widget support. Existing finite rich
MIME bundles already travel from remote kernels and retain their own contract.

Python/Lua conformance fixtures, backend ownership/reply/cancellation tests,
frontend tests and headless assisted-reading integration verify the contract.
Real PDF/DjVu worker, host and Electron tests verify native page export and reader
compatibility. No deployment on two physical machines or authenticated agent
session was used for this verification. See the [API guide](../architecture/document-artifacts.md)
and [current status](../status.md).

## Ownership clarification, 2026-11-21

Independent reuse requires a matching provider and content ID on a buffer whose
independent open reached readiness. URL slots have a separate namespace from
document slots. Binding a reader as a snapshot source grants access to capture
its contents. Lifetime and deletion authority stay with its existing owner.
Both delivery transports check an inferred main-file digest against the bytes
transferred before committing the open.

Every palette selection replaces the cell's unsubmitted capture. Expiry and
teardown resolve a waiting capture as a failed action so its lease can end.

The target carries the source ID as an optional field of the worker request
envelope for opted-in execute and followup operations. Workers can implement
`handle_with_context(operation, payload, *, source_context)` to receive the
original provider payload and the separate ID (or None). Existing two-argument
handlers receive a compatibility copy with the ID when present. A name collision
rejects that request explicitly, preserving the provider's private field.
