# ADR 0039: User data is not a control frame

Status: accepted

Supersedes the total content-size bounds in ADRs 0035, 0036 and 0037.

## Decision

Copy/open text and VisiData snapshots have no product-imposed total byte limit.
The previous 512 KiB text ceiling, 768 KiB snapshot ceiling, 100,000 snapshot
node ceiling and 4 MiB aggregate pending-text ceiling are removed. Control
messages remain bounded. Transfer chunk sizes and concurrent action counts
bound work in transit; they do not cap the total size of a selection or file.

This does not promise constant memory for the destination: a register, Neovim
buffer and VisiData dataset need memory for their content. Snapshot conversion
and the internal editor-invoked API still materialize application values.
Available memory/disk, invalid text and application failures remain observable
failures, not reasons to preemptively reject an arbitrary number of bytes.
UTF-8/no-NUL text, supported snapshot types, cycle rejection and structural
snapshot depth validation remain unchanged.

## Application-driven copy/open

The target-local socket accepts a versioned application.editor_action_begin
header with empty content text, followed by same-request
application.editor_action_chunk frames. Each carries UTF-8 text of at most
65,536 bytes and an eof boolean; non-final empty chunks are invalid. The existing
1 MiB encoded control frame accommodates JSON escaping without restricting the
accumulated content. The old inline request remains accepted.

The receiver spools chunks into a private temporary file before publishing the
action. The manager owns it through acknowledgment, expiry, recipient replacement
or client/runtime close. An incomplete upload never publishes an action and its
temporary file is closed on failure. There remain at most 32 pending actions;
pending content is stored on disk, without a total byte ceiling.

GET /v1/editor-actions/{id}?editor_id=...&offset=N returns bounded content plus
offset, next_offset, and eof. Offsets count UTF-8 bytes; chunks never split a
character. The response repeats the same action and content metadata.
Every fetch verifies the exact recipient and pending source lifetime. A delivered
acknowledgment requires a fetch reaching the end. The original inline GET remains
available for older callers; the current frontend always requests chunks.

Successful chunk fetches renew a 30-second inactivity lease. This replaces the
fixed total delivery deadline for the chunked flow. A slow transfer making
progress can continue beyond 30 seconds. The helper waits for the final
acknowledgment or socket closure; it does not impose another total deadline.
Socket upload inactivity and frontend connection/control timeouts remain bounded.

Neovim writes chunks into a private local temporary file. It checks contiguous
offsets, consistent metadata, source ownership and the remaining lease, then
applies the complete content once. Open reads that local snapshot into a new
independent unsaved buffer; copy updates registers. Partial downloads never
mutate the destination. Files are removed after delivery/failure, disconnect,
runtime retirement and normal editor exit. Completed action outcomes remain
replay-safe through the existing acknowledgment cache.

Only content crosses the HTTP boundary. Target file paths, SSH and shared
frontend/target storage are not required.

## Internal editor-invoked exports

Worker editor-action results use an explicit private data framing extension:
big-endian 0xffffffff, ASCII JEA1, then repeated big-endian chunk lengths and
bytes, terminated by a zero length. Chunks are at most 64 KiB and also respect
the configured frame limit. Their concatenation is one ordinary UTF-8 JSON
worker.result for editor_action. Other messages cannot use this framing.

The sender stages encoded output before publishing the header. The receiver
spools the bounded chunks before decoding and validating the ordinary result.
This removes the internal export frame ceiling without relaxing ordinary
control-message framing. This API retains its full-result HTTP response and
has no public editor commands; application-driven exports use chunked HTTP.

## Kernel-to-VisiData snapshots

The managed kernel factory owns a private artifact directory for each kernel
generation and supplies it through JUSI_KERNEL_ARTIFACT_DIRECTORY. The generic
jusi.kernel_artifacts.publish_json helper writes data-only JSON into a private
file and returns a small reference for a provider's private handoff payload.
It never publishes an artifact as frontend state or evaluates its contents.

VisiData uses this reference instead of embedding data in Jupyter/worker control.
Its worker checks the private regular-file reference, copies it in bounded
blocks into client-owned staging, and removes the source. The application decodes
only the existing supported snapshot data and removes its staging file. Worker
close removes staging; kernel teardown removes unconsumed artifacts, including
those left by a rejected handoff. Failed publication removes its partial file.

This is target-local storage between processes of one managed kernel target,
not a remote file delivery route or a hostile-code security sandbox. Snapshot
materialization still uses memory in the kernel and application; the service
and worker no longer decode or retain the large snapshot.

## Verification

Shared Python/Lua fixtures cover stream headers, chunks and offset responses.
Regression tests cover UTF-8 boundaries, malformed/empty chunks, complete-fetch
acknowledgment, lease renewal, recipient checks and temporary-file cleanup.
A file-helper test transfers 6.5 million UTF-8 bytes, beyond the old per-action
and pending-byte limits. Worker tests export beyond the control frame limit and
reuse the same worker. The real VisiData/Neovim scenario snapshots, copies and
opens a value above 1 MiB, then verifies source-close independence and reuse.
