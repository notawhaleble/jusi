# Web presentation

With `JusiWebBuffer` installed (for example in `jusi-neovim`), rich Jupyter
outputs automatically open in web buffers without moving notebook focus. If the
cell already has a visible output window, the web buffer replaces its contents
in that same window. Earlier text remains in its retained terminal buffer.
Supported MIME alternatives, in preference order: `text/html`, `image/svg+xml`,
`image/png`, `image/jpeg`, `image/gif`, `application/javascript`, `text/javascript`.
Plain Neovim displays `text/plain` instead.

For matplotlib, enable its inline backend in your notebook:

```python
%matplotlib inline
import matplotlib.pyplot as plt
plt.plot([1, 2, 3], [1, 4, 2])
plt.show()
```

A dependency-free example using IPython is:

```python
from IPython.display import HTML, display
display(HTML('<h1>Hello from Jusi</h1>'))
```

IPython's `%%javascript` runs browser JavaScript in its own web document:

```javascript
%%javascript
const heading = document.createElement('h1');
heading.textContent = 'Hello from JavaScript';
element.appendChild(heading);
```

`element` is a native DOM element provided as the output container. Use standard
DOM APIs, canvas and browser APIs; jQuery, Jupyter widgets, notebook globals and
Python-variable access are not supplied. JavaScript errors are shown in the
page. Each display gets its own document; variables do not carry across cells.
Kernel execution does not wait for browser execution; script errors appear in
the page rather than changing the kernel execution outcome.

Rich output belongs to the cell: hide its window normally, use `JusiToggleFocus`
to return to the latest rich projection, or `JusiClose` to retire the cell's
output. New execution and runtime cleanup remove its buffers and temporary
files. Each display is a snapshot; display-id updates and clear-output are not
implemented. The per-display limit is 16 MiB of encoded bundle data; larger
bundles fall back to text.

An existing terminal application whose client declares `editor_actions` can
open a link with:

```python
from jusi.editor_client import open_url
open_url('https://example.com/report')
```

This blocks for delivery acknowledgment; call it off an application's UI thread
when needed. A missing web-buffer command fails delivery with
`EditorDeliveryError`. The new web buffer survives source-client closure, like
an exported text snapshot. For worker `editor_action` results, return
`jusi.plugin_api.open_url(url)` instead.

URLs must be absolute HTTP(S) and reachable from the editor host. A target-side
`localhost` web server is not automatically reachable from a remote editor.
There is no implicit tunnel or reverse proxy. These helpers provide generic
navigation for plugins; a durable web-only client is a separate future surface
contract. See [ADR 0052](../adr/0052-web-buffer-output-and-url-actions.md).

An application may opt into a client-scoped navigation slot:

```python
from jusi.editor_client import open_url, close_url
open_url('https://example.com/first', slot='page')
open_url('https://example.com/second', slot='page')  # delete first, create second
close_url(slot='page')  # also closes hidden pages; repeated close is harmless
```

Slots accept 1-64 ASCII letters, digits, underscores or hyphens and never target
another client's page. A slot open replaces its prior buffer, rather than
navigating it in place. Managed pages use the source application's window,
retaining the terminal buffer underneath. Placement searches all tabs; if both
the terminal and page are hidden, core reveals the source terminal in a new
window before opening its replacement. Replacement always deletes the old
buffer, wherever it is displayed or hidden. `close_url` restores that terminal
in windows still displaying the page; a window reused for unrelated content
is left alone. Native window close hides the page; use `JusiToggleFocus` from
the source cell to reveal the retained terminal again.
Closing a page leaves the terminal application alive.
Closing the source client leaves its last delivered page available and forgets
the slot. Helpers block for editor acknowledgment; serialize navigation calls
and keep them off the application UI thread. Worker editor-action results can
use the equivalent `jusi.plugin_api` helpers. See
[ADR 0053](../adr/0053-client-scoped-url-slots.md).
