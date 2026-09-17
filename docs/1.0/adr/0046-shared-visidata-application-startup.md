# ADR 0046: Shared VisiData Application Startup

- Status: accepted
- Date: 2026-09-16

## Decision

Optional `jusi.visidata_support.initialize_visidata()` centralizes embedded
VisiData startup: UTF-8 locale selection, idempotent data-directory creation,
normal VisiData configuration/plugin loading, then Jusi clipboard/open hooks.
Call it once in a target-side terminal application before constructing sheets.
It returns `vd`; the application constructs sheets and calls `vd.run(*sheets)`.
Applications can specify `open_name` and `open_filetype` for exported snapshots.

Importing the helper does not import VisiData. Core service, discovery, worker
and kernel paths do not invoke it, and VisiData remains an optional dependency.
The helper has no supervisor, transport or resource-lifetime responsibilities.
Data acquisition, sheet models, commands, ongoing work and domain errors remain
plugin-owned. It is not a universal plugin runner.

User configuration uses VisiData's own path selection, including `VD_CONFIG`
and `VD_DIR` in the target service environment. Preferences such as `disp_menu`
are preserved; Jusi installs its copy/open integration after config loading.
Existing applications must adopt the helper explicitly; launching a generic
terminal surface cannot implicitly initialize its application library.

Bundled `%%vd` and the companion `jusi-codex` checkout use this helper. The
SQLite test fixture intentionally retains its isolated startup policy.
The new API first ships in Jusi 1.0.2; companion plugin publication must require
that version or newer. Development uses both source checkouts together.

## Verification

Fresh-process tests cover ordinary rc loading, `VD_CONFIG`, options at UI
startup, import isolation, and editor bindings on derived sheets. The companion
Codex test exercises real runtime startup without invoking Codex and checks its
export filename. The real Neovim VisiData scenario verifies configured status,
hidden menu, Unicode and editor copy/open.
