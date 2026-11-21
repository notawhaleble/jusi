# Incident 0031: Hidden managed URL pages blocked replacement

- Date: 2026-10-27

Opening a row in jusi-web failed with `frontend_delivery_failed` when both its
terminal and its previous managed page were hidden. Destination capture rejected
the action before fetching its slot, leaving the old page alive. Separately,
`surface:close()` marked itself closed before buffer deletion; a deletion error
could make subsequent replacement forget a still-live page. Worker-returned
managed opens also used the current window without preserving the source
terminal, unlike application opens.

Managed URL delivery now captures the verified source identity even while
hidden and resolves placement across all tabs after fetching the content. A
hidden source is revealed in a new window without overwriting unrelated
buffers. Application and worker-result opens use the same source-aware path.
Page close becomes final only after successful cleanup, preserving retryability.
Unkeyed snapshots keep their original captured-window validation.

The external jusi-web real service/kernel/VisiData/Neovim regression reproduces
the old rejection and checks hidden drilldown, bookmark replacement in another
tab, hidden direct URL followup, worker-returned URL replacement, deletion of
every previous page, and isolation of another cell's page. Frontend tests also
cover a page mirrored across tabs and injected buffer-deletion failure followed
by successful replacement. Browser rendering uses a substitute host command.
