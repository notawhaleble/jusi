# ADR 0052: Web-buffer output and URL actions

- Status: accepted
- Date: 2026-10-18

## Decision

Detect the optional `JusiWebBuffer` command at presentation time. Invoke it with
structured `nvim_cmd` arguments in a prepared split, capture the buffer that the
host creates, and preserve notebook focus for automatic output. The host owns
its browser session; Jusi owns cell identity and projection cleanup. Native
window close only hides output. Cell close/replacement deletes all of its rich
buffers and local temporary content. Multiple rich displays remain separate
projections retained until cell cleanup. New rich output reuses a visible
projection window for that cell, including a preceding text-output window.
Earlier text remains in its terminal buffer; later stream writes do not replace
the visible browser or allocate another split. Failed host opens restore the
previous buffer in a reused window.

Jupyter rich displays retain their MIME alternatives as a JSON object encoded
with `application/vnd.jusi.mime-bundle.v1+json`. Each ordered execution-output
chunk includes `bundle_id` and Boolean `bundle_end`; only the last chunk ends
the bundle. Chunks keep the existing 16 KiB UTF-8 event bound. One bundle has a
16 MiB UTF-8 ceiling; larger displays use their text/plain alternative or an
explicit size-limit message. The frontend stages chunks per cell, decodes only
a complete bundle, and fences retired execution identities. Plain-text-only
output retains its existing streaming representation.

The frontend prefers HTML, SVG, PNG, JPEG, GIF, then JavaScript when the host
supports web buffers. JavaScript MIME (`application/javascript` or
`text/javascript`, including IPython `%%javascript`) runs as a base64 script
resource in a standalone document with a native DOM `element` container. No
Jupyter widget runtime, jQuery, or Python bridge is implied. Script errors are
browser presentation content and appear in the document. It writes complete content to a private frontend-local HTML file and
opens that file URL. This works across remote kernel targets without sharing
filesystems or turning target paths into local URLs. Images use data URLs
inside the document. HTML is notebook-produced browser content, not escaped
terminal text. The core statusline uses an evaluated format item so hosts can
append browser status without extending a whole-line Vimscript expression.
Existing suffixes survive focus refresh. Without the host command, use text/plain, or an explicit
rich-renderer-required message when that alternative is absent. Failed renderer
creation reports a local presentation failure and retains text fallback.

This deliberately extends ADR 0018's bounded event output to finite rich
snapshots. It is not a transport for sustained application content or a live
web server. Rich snapshots currently occupy bounded chunks in retained SSE
history; they are not a durable artifact store. Jupyter display-id updates and
clear_output semantics remain outside this initial snapshot implementation.

## URL action

Extend generic editor actions with `open_url`. Its content is
`{action: "open_url", url: "https://…", text: ""}`. The empty text field keeps
existing snapshot framing, chunked fetch and acknowledgment unchanged. URL
content travels in private content fetches, never in ordered action metadata.
Only absolute HTTP(S) URLs without whitespace/control characters are accepted.
The frontend does not invoke a shell or interpolate an Ex command.

`jusi.editor_client.open_url(url)` delivers through an existing client's
`editor_actions` channel. `jusi.plugin_api.open_url(url)` constructs a worker
editor-action result. Application delivery captures and verifies the source
client window just as text-open does; reconnect deduplication and acknowledgments
are unchanged. The resulting web buffer is an independent snapshot. Missing
host support fails delivery; it never silently invokes an external browser.

URLs must be reachable from the editor host. Core neither rewrites loopback
addresses nor invents remote tunnels. A future web-only durable plugin client
still needs an explicit surface, attachment, authentication and lifecycle
contract. ADR 0017's reverse-proxy proposal remains deferred; this decision
accepts only finite rich output and generic external navigation.

## Verification

Shared valid/invalid fixtures cover schemas and Python/Lua validation. Tests
cover URL delivery acknowledgment, command injection, missing renderer, fallback,
multi-chunk real-kernel output, focus, hide, retirement, local-file deletion and
the service/kernel/SSE/Neovim route. Headless tests substitute the host command;
they do not claim to verify Electron browser rendering.
