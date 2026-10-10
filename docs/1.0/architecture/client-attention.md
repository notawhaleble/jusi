# Plugin attention

Plugins use attention to announce a question, approval request, or completed
background work. The core does not interpret prompts or answer on the user's
behalf. The plugin's own UI remains responsible for those interactions.

Add `"attention"` to the plugin family's catalog `capabilities`. For a
`terminal_interactive` client, Jusi supplies `JUSI_EDITOR_ACTION_SOCKET` to its
terminal application's environment. Preserve that variable in subprocesses
that need to publish attention. No `editor_actions` capability is required
unless the application also uses copy/open/diff.

```python
from jusi.editor_client import request_attention, update_attention, clear_attention

pending = request_attention(
    kind="action_required",
    message="Approval requested",
)
# Continue servicing the plugin UI; the helper only waits for service acceptance.
# When the user answers in your application, or the request is withdrawn:
clear_attention(pending)

finished = request_attention(kind="notice", message="Background work finished")
# Optional correction without another alert:
update_attention(finished, kind="notice", message="Results ready to inspect")
```

Keep summaries short (up to 240 characters) and omit private prompt content,
credentials and control sequences. IDs belong to the originating client.
Resolve/withdraw outstanding requests when the plugin no longer needs input.
Use a new ID for genuinely new attention; updating an existing ID does not ring
again. A notice may already have been dismissed when an application tries to
update it, in which case the helper raises `EditorDeliveryError`. Clearing an
already cleared item is harmless.

The service accepts attention while the editor is disconnected. Closing the
client clears its attention. These helpers don't wait for a human or for the
plugin's ongoing worker call to return. An unavailable channel or unconfirmed
acceptance raises `EditorDeliveryError`; do not automatically retry creation,
since the first request may have succeeded. In this version the channel is
provided to terminal applications; worker handlers should arrange publication
through their application rather than import frontend code or write BEL bytes.

## Neovim controls

- `:JusiAttention` visits a pending client, offering a picker if needed.
- `:JusiAttention!` dismisses completion notices without visiting.
- Visiting a client clears its notices, but never resolves approval/questions.
- Notebook and client statuslines retain an attention count.
- With Neovim's default tabline, the owning tab has an amber background for
  questions/approvals or blue for completion notices, plus `!N`. The tabline is
  visible while attention is pending, even for a single tab. Previous native
  tabline visibility is restored when attention clears.

Tab highlights follow visible client buffers. When a client is hidden, its
notebook's tabs provide the fallback. A mirrored notebook does not mark a tab
when the client is visible elsewhere. Visiting a tab cannot clear a question or
approval request. The indicator survives unrelated messages.

Custom tablines are preserved. They can read
`require("jusi.attention_tabline").summary(tabpage_handle)`, which returns
`count`, `action_required`, and `notice`, and refresh on the
`User JusiAttentionChanged` event. `JusiTabAttention` and `JusiTabNotice` are
customizable highlight groups. Set `attention.tabline = false` to disable the
default tabline enhancement.

Messages do not steal focus. Neovim focus events suppress bells while focused;
otherwise a portable best-effort terminal bell accompanies new attention.
Terminal configuration determines its audible/visual effect. The default uses
the controlling terminal on the editor host, independently of the remote PTY
and Neovim's error-bell settings. Unknown focus uses the same best-effort bell.

```lua
require("jusi").setup({
  attention = {
    bell = true, -- true (default), false, or function() ... end
    tabline = true, -- enhance the default tabline while attention is pending
    notify = function(items, context)
      -- Optional host-side desktop notifier. items is a coalesced list.
      -- context.focused and context.focus_known describe Neovim's focus view.
    end,
  },
})
```

Notification hooks are optional; the in-editor message and pending indicators
remain available. A hook must return promptly. Bell requests are coalesced and
rate-limited. No OS notification tool or terminal-specific escape extension is
required. See [ADR 0049](../adr/0049-client-attention.md).
