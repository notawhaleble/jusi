# Incident 0036 — Blank output pane for a successful pip command

Executing a plain cell containing `pip show jusi` opened a terminal split.
With a five-line output pane, the first and second executions both showed five
empty lines. The terminal buffer contained the package metadata below the
visible rows.

Jusi initialized Neovim’s virtual terminal while the notebook window was
current. Opening the smaller output split afterward resized the terminal and
left blank rows in scrollback above the stream. The presenter now opens the
split first and initializes the terminal in that window’s context. Focus
returns to the notebook before Jusi sends the output.

The owned-service end-to-end regression executes the command twice in a real
kernel and reads the output pane’s visible rows to check the name and version.
