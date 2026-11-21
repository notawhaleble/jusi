# ADR 0055: Early document reuse and negotiated local staging

- Status: implemented; part of the unpublished 1.0.7 patch
- Date: 2026-11-02

## Problem

Document reuse in ADR 0054 was checked only after uploading, fetching and
decoding the complete artifact. A 114 MiB PDF expanded to 153 MiB and required
roughly 2,450 sequential HTTP fetches, each with a subprocess. Local targets paid
the same cost. Reopening an existing reader unnecessarily paid it again.

## Decision

Keep `jusi.editor_client.open_document()` unchanged. Add a bounded identity offer
before content delivery and an optional shared-staging transport beneath it.
Neither reading plugins nor presentation providers choose the transport.

`offer_document` carries the ordinary slot/provider/lifetime options, an artifact
manifest as content, and an optional opaque shared-session token. It requires
`editor_actions`, not `source_context`. The owning frontend checks MIME/provider
compatibility and an already-open independent document before requesting bytes.
Its reply is exactly `{transport: "reused" | "shared" | "http"}`. A reused view is
bound to the requesting client's slot. Client-lifetime views still replace their
previous contents instead of reusing another client's presentation.

Shared staging requires an owned local-service launch. The frontend creates a
private directory with a random challenge and passes only its path in process
environment. The service must read that challenge, write its acceptance into the
same directory, and echo the challenge in readiness. The frontend verifies both
observations. A loopback URL, host name, or `kind=local` alone is insufficient.
Wrappers, containers and failed handshakes continue to use HTTP. Independently
connected services have no shared-staging authority, even on localhost.

The service's existing client action broker grants each application a separate
staging directory and the negotiated session token through launch environment.
After a `shared` offer response, the core helper copies the files into a fresh
private artifact directory. Copies are read-only, never hard links. The default
main-file identity is verified during copying; a changed source rejects the
operation. No base64 is generated.

`open_shared_document` carries the manifest, token and a random artifact ID.
There is no source path on the wire. The frontend resolves that ID only within
the action's authoritative client namespace under its negotiated directory.
It rejects missing files, directories/symlinks masquerading as files, unexpected
files and size mismatches. Before opening the provider it moves the snapshot
into frontend-owned storage, or copies it if rename crosses filesystems.
Therefore service/client cleanup cannot remove an adopted independent document.
Normal provider readiness, delivery acknowledgment, cancellation, view ownership
and buffer-wipe cleanup remain in force.

If snapshot staging becomes unavailable before committing the presentation, the
helper can safely fall back to HTTP. Once a shared-open request is submitted,
uncertain delivery is not retried through a different transport. Replays retain
the existing action-ID deduplication contract. A second identity check at open
handles a document that became available between offer and content delivery.

## Bounds and verification

Offers/shared-open manifests and offer replies are bounded to 64 KiB. Existing
artifact file-count, filename, metadata and 256 MiB aggregate limits apply to
both transports. The shared token grants no extra plugin capability and does not
add HTTP authentication or document-to-host IPC. Installed application code
remains trusted under Jusi's existing process model.

Shared Python/Lua fixtures cover both actions and the reply. Tests cover local
startup negotiation, failed handshake fallback, client isolation, symlink
rejection, native-copy fallback, immutable copies, adoption surviving service
cleanup, late failures, and reuse without encoding. Reading integration runs
over both paths with the same public helper and lifecycle assertions.

The real-book benchmark is recorded in
[document delivery measurements](../performance/document-artifacts-2026-11-02.md).
One-request streaming HTTP and local acceleration of source snapshot replies
remain separate future transport improvements; this change optimizes document
opens and leaves those existing mechanisms intact.
