# Incident 0025: Second Notebook Statusline And Terminal Bridge Launch

A Docker-backed notebook showed only a filename in its statusline. Plugin
cells reported `frontend_transport / channel_closed` with Neovim E475:
`'jusi' is not executable`.

The statusline defect reproduces offline: open two notebook buffers in the
same window. Neovim restores window-local options for the second buffer, but
Jusi's saved original statusline was also acting as an installation flag.
Refresh now reinstalls the notebook expression even when the window already
has a saved original value. Leaving Jusi still restores that original value.
This defect is independent of kernel placement.

The terminal trace identifies a frontend-local launch failure. Interactive
surfaces require a local `jusi terminal-bridge` even when the service, kernel,
plugin worker and terminal application run in Docker. The default executable
was unavailable on the editor's PATH. Configure `terminal_bridge_command`
with a local Jusi executable, or expose that executable on Neovim's PATH.
The reported editor's active configuration has not yet been identified.

Launch now checks executable availability before allocating a projection,
reports `frontend_presentation / spawn_failed` scoped to the client, and names
the configuration setting in the missing-executable message. A jobstart
exception after projection allocation now closes its window and deletes its
buffer. It does not change backend client or kernel truth.

`tests/frontend/statusline_spec.lua` checks the installed statusline across
two notebooks in one window. `tests/frontend/presentation_spec.lua` covers
missing executables and a throwing jobstart, including resource cleanup.
