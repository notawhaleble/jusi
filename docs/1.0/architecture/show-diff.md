# Show a diff from a plugin application

A terminal application belonging to a Jusi client with editor_actions can call:

```python
from jusi.editor_client import show_diff

show_diff(
    "print('before')\n",
    "print('after')\n",
    before_name="before.py",
    after_name="after.py",
    filetype="python",
)
```

Neovim opens a new tab with two native diff windows. Before is on the left,
after on the right; focus moves to after. Both are read-only snapshots.
Use ordinary window/tab navigation and :tabclose to leave the comparison.

The call returns when Neovim acknowledges display. It does not wait for review
or report acceptance/rejection. Nothing is written back to the application or
its files. The buffers survive closing the plugin client or stopping its kernel.

Capture both values before starting background delivery when the application
needs its UI to remain responsive. Report EditorDeliveryError inside the
application, without automatically retrying an uncertain delivery.

Names are basename hints, not paths. The common filetype is optional.
Empty sides and identical content are supported. Transfers have no total byte
ceiling and use the same remote-capable chunked channel as copy/open; the final
buffers still require memory for their content.

A worker handling an internal editor_action selection can instead return
jusi.plugin_api.show_diff(...) with the same arguments. This constructs a worker
result; it does not initiate an application-channel request.

To test without another plugin, run the terminal fixture described in
[backend-driven editor actions](backend-driven-editor-actions.md) and type
action:diff in its terminal. It compares the fixture's selection with that text
plus an added line.

See [ADR 0040](../adr/0040-show-diff-is-display-only.md) for the wire and lifecycle
contract. A future accept/reject or edit-and-return feature would be separate.
