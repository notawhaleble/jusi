# ADR 0041: Cached runtime palette and tab-local cell focus

Status: accepted

## Decision

`:J[!] notebook [magic [entry]] [extra arguments...]` retains the legacy
palette workflow. Plain requests append a fresh cell. Magic requests reuse the
first valid cell whose header matches the requested token prefix, or append a
new cell. Reuse preserves the opener identity and history. A supplied range or
visual selection replaces the active body; selection capture precedes window
changes. Bang calls contextual submission, including pending input and durable
client followups. It does not introduce another execution path.

Completion lists loaded notebook buffers, then discovered magics, then configured
aliases. Offline notebooks remain available for plain cell creation. Duplicate
basenames use path labels; backslash escaping supports spaces in notebook names.
A notebook visible in the current tab is reused. Otherwise the palette opens it
as the leftmost full-height vertical split in that tab, preserving the source
window and its existing layout. Output and focus
windows are selected in the anchor notebook's tab, even if the same buffer has
another view elsewhere.

The optional runtime `palette` field is an immutable generation-local mapping
from discovered magic to `{entries: [alias, ...]}`. The target builds it from
catalog families and table-valued entries in the matching configuration section
(the legacy `[magic.alias]` convention). Only alias names are public. Scalar
settings, undiscovered sections, provider names and configuration values stay
private. Families with different configuration conventions can still be used
through manually authored cells; no generic provider resolution is inferred.
Start and restart rebuild metadata; completion reads the frontend cache without
backend calls. Older runtime snapshots fall back to catalog magic names.

`Ctrl-\ Ctrl-\` toggles cell/output focus in Normal, Insert and terminal input
modes. A global default is installed only if that mode has no existing global
binding; user buffer-local mappings take precedence. From unrelated buffers it
focuses the first visible notebook in the current tab, then searches other tabs
in tab order. Hidden notebook buffers are not opened by this fallback. Entering an interactive terminal
starts terminal input; returning to a notebook lands in Normal mode. The native
terminal escape and completion-menu keys remain unchanged. Detach removes only
buffer-local bindings still owned by Jusi.

## Verification

Shared health fixtures reject malformed/duplicate palette entries in Python and
Lua. Supervisor restart tests verify refreshed aliases and private-value
redaction. Frontend tests cover Unicode selection, history/identity preservation,
contextual submission, escaped completion, tab-local splits and mapping ownership.
The real-kernel suite checks startup metadata, palette cell execution and focus
round trips alongside the existing input/followup tests.
