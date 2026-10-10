# Changelog

## 1.0.5 — 2026-10-10

- Add client-owned attention requests and completion notices, with statusline
  counts, tab indicators, `:JusiAttention`, and optional notification hooks.
- Add `:JusiCloseTab` / `\Q` to clean up the current notebook’s visible
  artifacts in the current tab while preserving its last view and running kernel.
- Wake owned readers when transports close and reduce startup imports, improving
  plugin launch, close, and service shutdown latency.
- Keep application-driven editor opens anchored to the requesting client window
  throughout transfer and retries; fail delivery if that window is unavailable.

## 1.0.4 — 2026-10-05

- Restore the notebook statusline when switching notebooks in the same window.
- Report missing local terminal-bridge executables with actionable configuration
  guidance, and clean up failed terminal launches without leaving empty splits.
- Open application snapshots beside the requesting client in the current tab,
  including when a notebook is mirrored across tabs.
- Document local venv and Docker target setup, including explicit host-side
  terminal-bridge paths, and add the optional JShell command recipe.

## 1.0.3 — 2026-09-26

- Repair followup-history folds after local edits, including a stale range or
  missing native fold, while keeping ordinary body typing on its local path.
- Resync cell-mode visuals with the current editor mode when toggled, so the
  badge and borders agree with active Normal-mode mappings.
- Scan aliased or repeated Python library paths only once during plugin
  discovery, preventing false duplicate-plugin conflicts while preserving
  conflicts between distinct installations.

## 1.0.2 — 2026-09-17

- Run established plugin-client operations outside the serialized kernel lane,
  allowing Python execution and work on other clients to continue while a
  plugin is busy. Same-client overlap remains a recoverable conflict.
- Add Insert-mode `Ctrl-Y` submission that clears only the successfully
  submitted cell body while preserving magic headers, history and newer edits.
- Share complete VisiData startup and editor integration through the optional
  `jusi.visidata_support` helper, including normal user configuration loading.
- Open editor-action snapshots as disposable scratch buffers so local edits do
  not create false unsaved-file prompts or block editor exit.
- Keep interactive terminal views following new output unless the user scrolls
  away, and open palette-selected notebooks as full-height left columns.

## 1.0.1 — 2026-09-16

- Poll the owned kernel process directly when checking liveness. This prevents an
  inherited running asyncio loop in Jupyter's synchronous wrapper from falsely
  reporting a healthy kernel as dead and triggering runtime cleanup.
- Add the recorded Jusi 1.0 demo to the GitHub README.

## 1.0.0 — 2026-09-15

First stable release of the Neovim-native Jusi rewrite. Includes the features and
compatibility changes listed under rc1 and the corrected PyPI description from
rc2. Runtime behavior is unchanged from the verified candidates.

## 1.0.0rc2 — Release candidate

Prepared 2026-09-15 for TestPyPI review.

- Use a dedicated PyPI description with an absolute GitHub project link, avoiding
  relative documentation links that do not resolve on package-index pages.
- Include the PyPI description in the source archive. Runtime behavior is unchanged
  from the verified rc1 candidate.

## 1.0.0rc1 — Release candidate (TestPyPI)

Prepared 2026-09-15.

Jusi 1.0 introduces a Neovim-native frontend and a target-side Python service.

- Plain-text cells with stable identities, cell-local syntax and indentation,
  kernel/plugin completion, status marks and keyboard cell mode.
- Contextual execution, kernel input and plugin followups; foldable followup
  history and explicit output parking.
- Local and remote target aliases through `JusiStart` / `JusiStop`, with resource
  cleanup and diagnostics for process and transport failures.
- Terminal clients and bundled `%%vd`, including copy to a register and open in
  a buffer. Plugins can also request display-only diffs.
- Source-only Jupyter notebook import/export. Markdown and raw sources import
  as ordinary cells; outputs and original cell types are not retained.
- Separately installed Python and Neovim components, plus packaged plugin and
  plugin-family authoring skills with matching reference source.

### Compatibility

Requires Python 3.9+ and Neovim 0.11+. Vim is no longer supported. The `.vipynb`
extension remains, but 1.0 uses new delimiters and does not load legacy `##`
notebooks. Legacy notebook migration is not provided. Runtime plugins must use
the 1.0 contracts; legacy providers are not automatically compatible.

### Scope

Rich/web output presentation is deferred. Diff display has no accept/reject or
writeback workflow. Independent authoring-skill evaluation and real remote
transport-loss review remain deferred; local simulated transport-loss tests
are covered by the release checks.
