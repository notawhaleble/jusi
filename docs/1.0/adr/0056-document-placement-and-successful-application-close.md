# ADR 0056: Document names, explicit tab placement, and successful application close

- Status: implemented; part of the unpublished 1.0.7 patch
- Date: 2026-11-03

## Problem

Document delivery replaced source basenames with `document.ext`, and independent
views always opened in a split. A plugin that only bootstraps and opens a book
could not finish successfully: its terminal application's exit was treated as
fatal loss, leaving a durable followup client unless the user closed it.

## Decision

`open_document` preserves the source basename by default and accepts `name=`
for temporary conversions. Artifact names are flat UTF-8 strings up to 255 bytes;
separators, colon, controls, `.` and `..` remain forbidden. Both transports retain
the same validation, immutable snapshot and ownership rules.

Document offer/open actions accept optional `placement="default" | "tab"`.
Omission retains right splits for independent documents and source overlays for
client-owned documents. Tab placement explicitly focuses a new tab; reuse focuses
the existing visible view or reveals a hidden buffer in a new tab. This option
is generic and does not change text exports, URL opens, or default document layout.

The private typed terminal request accepts `close_on_success`, default false.
This is an explicit close policy chosen by the worker during construction, not
an interpretation of ordinary operation results. After observing process exit
code zero, the terminal manager reports success to the supervisor, which closes
that exact client through normal explicit cleanup. It emits normal surface/client
closure events, preserves independent documents and unrelated clients, and does
not report a terminal failure. Nonzero exit, signal, missing diagnostics and
unopted-in exits retain the existing fatal path. No frontend plugin branching,
new kernel states, or result-driven client lifetime is introduced.

The read plugin uses this policy only for `%%read TARGET --open`: create missing
harness files, deliver the book and wait for reader readiness, then exit. Empty
ordinary cells retain the interactive client. Initial questions continue to use
the assistant normally.

## Verification

Shared schema/Python/Lua fixtures cover placement and close policy. Tests cover
Unicode filenames, path rejection, HTTP/shared staging, visible and hidden reuse,
unchanged default placement, successful scoped cleanup, error exits, worker
process framing, and a real read application exiting after headless Neovim
acknowledges delivery. Independent books survive closure. Existing frontend and
end-to-end suites exercise the unchanged default callers.
