# Incident 0030: Web output splits and browser statusline errors

- Date: 2026-10-19

A first matplotlib execution could emit font-cache text before its plot. That
text created a terminal projection; the later rich MIME bundle unconditionally
opened another split. Later executions without the font-cache message appeared
correct because they created only a web projection.

Web output now selects a visible projection belonging to the same cell and tab
and opens the host buffer in that window. Earlier text and rich buffers remain
owned by the cell until cleanup. Later stream output is retained without opening
an additional split. Reused-window failure restores the preceding buffer; a
window displaying unrelated content is never treated as an owned projection.

Focusing a managed web buffer could raise `E121: Undefined variable` containing
`[Profile: default | Text ready]`. Core's `%!` whole-line statusline expression
was incompatible with the host's appended `%{...}` browser indicator: Neovim
parsed the combined option as Vimscript. Core now uses a re-evaluated format
item (`%{%...%}`) and preserves suffixes when refreshing its installed format.

Regression coverage includes text-to-web window reuse, consecutive rich output,
retained stream text, focus changes, composed statusline evaluation, host-open
rollback, and service/kernel/SSE execution. A read-only integration check against
`jusi-neovim`'s actual browser-status module passed with either module loading
first. A real matplotlib run with a fresh font-cache directory in the configured
Python 3.13 environment confirmed that its initial font-cache text window is
reused for the PNG plot. The generated JavaScript document was also exercised in an isolated,
hidden Electron window.
