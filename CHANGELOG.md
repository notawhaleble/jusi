# Changelog

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
