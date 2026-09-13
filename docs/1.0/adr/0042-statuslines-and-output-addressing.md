# ADR 0042: Statuslines and editor-local output addressing

Status: accepted

## Decision

Notebook windows show the filename, modified flag, authoritative kernel on/off
view, configured target alias, and active cell mode. Before authoritative
inspection the kernel value is unknown. When transport is disconnected or
connecting, any retained kernel state is explicitly last-known and transport
status is shown separately. Rendering reads cached controller state; it never
inspects the backend or infers liveness. Controller state notifications request
a deferred statusline redraw.

Output and interactive-client windows show a short positive integer. This is an
editor-local output handle, separate from every backend client, execution and
surface identity. Handles are allocated monotonically across notebooks, remain
stable while the projection buffer survives (including hidden windows), and are
never reused in that Neovim session. Wiping the projection retires its handle.
No identifier is extracted from an opaque backend string.

`{id}G` and `{id}Q` in cell mode jump to the output's source cell or perform its
full cell-close lifecycle without depending on the cursor's current cell. The
same keys work in output buffers in Normal mode. Ordinary notebook Normal mode
uses `{id}\g` and `{id}\q`; explicit `JusiGotoClient [id]` and
`JusiCloseClient [id]` work from any buffer. A stale ID fails without falling back
to current-cell close. Resolution validates the current model cell and exact
projection buffer before acting. A fresh execution/projection gets a fresh ID.
Uncounted Q/\q closes the current cell; uncounted G/\g retains native last-line
navigation.

Notebook-local literal backslash mappings cover editing, contextual submission,
history, parking, restart and interrupt. They respect existing mappings and
remove only bindings still owned by Jusi. The obsolete rebuild shortcut and
additional completion-menu bindings are omitted.

Jusi statuslines are window-local. Leaving a Jusi buffer restores the previous
local statusline unless the user has replaced it. Rendering uses the requested
statusline window, including inactive windows, escapes filename percent signs,
and uses current colorscheme highlight groups without copied backgrounds.

## Verification

Frontend tests cover disconnected last-known state, inactive output labels,
theme changes and restoration; counted G/Q and backslash actions from another
cell; fresh IDs after replacement; and stale-ID rejection. The VisiData
end-to-end scenario closes the displayed client ID from an exported data buffer,
then verifies cleanup and continued use of the kernel namespace.
