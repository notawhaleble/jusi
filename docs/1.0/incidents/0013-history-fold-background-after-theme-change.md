# Incident 0013: History Fold Background After Theme Change

The muted history summary remained visually backed by a different background
when switching colorscheme or the background option. Its foreground-only text
chunk did not control the native Folded highlight beneath the summary and fill.

Notebook windows now map Folded to Normal through window-local winhighlight.
JusiHistoryFold supplies only the muted foreground. No theme background is
cached. The prior winhighlight value is restored with the other window options
when leaving the notebook. Global Folded styling and other buffers are unchanged.

The frontend history test switches light/dark backgrounds and colorschemes,
verifying Normal inheritance, foreground-only summary styling, unchanged text
and preservation of the closed fold.
