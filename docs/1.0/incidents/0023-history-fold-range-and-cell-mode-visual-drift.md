# Incident 0023: History Fold Range and Cell Mode Visual Drift

An expanded history could gain a line while Neovim's manual fold retained its
old end. The new history text then appeared between cells after collapse. The
notebook model classified the edit as local text, so no fold update ran. Fold
extmarks could still point at the expected boundaries while the native fold
covered a shorter range. In a related case, the history toggle attempted
`foldclose` after the native fold had disappeared, producing E490.

Local edits inside history now notify the history view without turning ordinary
body typing into structural work. Before reusing a fold, the view checks its
native start and end against the current cell boundary. Toggle repairs a
missing or stale fold before opening or closing it. The closer stays visible.

Cell mode could keep a stale Insert-enter flag after a missed InsertLeave event.
Normal-mode cell mappings then worked while the border overlays and statusline
badge remained hidden. Explicit mode changes now read the current editor mode
before setting the active visual flag.

Frontend regressions cover history-line insertion, collapse from inside
expanded history, a missing native fold, and mode toggling after a stale
Insert-enter event.
