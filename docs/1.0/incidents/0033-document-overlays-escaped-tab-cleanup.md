# Incident 0033: Document overlays escaped tab cleanup

- Date: 2026-11-03

Running a read explanation from a dedicated book tab placed a notebook mirror
on the left and the client below it. The explanation then replaced the client's
terminal buffer in that split. `\Q` removed the notebook mirror but left the
Markdown and the durable client in followup state.

Tab cleanup enumerated ordinary output, rich output and terminal buffers, but
not the client-owned document slots. The hidden terminal had no windows, so its
cell was never selected for close. A second gap prevented session discovery
from the document alone after the notebook mirror was manually closed.

Core now enumerates terminal and `lifetime="client"` document views through a
shared helper. Tab cleanup resolves document ownership from the session's slots
and controller clients. Sharing across tabs includes either view kind;
independent books and entirely hidden clients remain outside selection.

Client-close events retire terminal surfaces before closing owned documents.
Consequently, deleting an explanation buffer alone could leave an empty split
after its hidden terminal had already disappeared. Owned-document teardown now
closes windows still displaying that document before removing its buffer and
releasing staging. The separate `close_document` action still reveals the
terminal without closing its client. Repurposed windows remain untouched.

`tests/frontend/tab_cleanup_documents_spec.lua` exercises the mapping, delayed
completion across tab switches, missing notebook mirror, mixed view sharing,
independent books, hidden clients, retry, staging release and repurposed windows.
`tests/e2e/terminal_surface_spec.lua` uses the real service and busy terminal
worker with a fixture Markdown provider, verifying complete client/surface
retirement, removal of the explanation split and followup state, and preservation
of the source notebook, unrelated view and running kernel.

Validation: 367 core Python tests passed (one opt-in skip), both Neovim suites
passed, and 28 jusi-read tests passed (three expected skips).
