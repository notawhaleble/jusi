# Incident 0029: ACP Session-Features Test Crashes Native Neovim

- Date: 2026-10-18
- Status: unresolved; bootstrap-specific validation passes

## Observation

While validating ADR 0051 against the sibling `jusi-acp` checkout, its broader
`test_session_features_through_real_notebook_and_terminal` scenario terminated
Neovim 0.11.4 with signal 11, before it wrote its result file. Repeating the
original scenario without the new bootstrap/completion steps reproduced the
crash. This does not establish its root cause or establish a bootstrap regression.

The macOS diagnostic report identified `EXC_BAD_ACCESS` at address `0x10` on the
main thread, through `vim_regexec_string`, `match_file_pat`, `aucmd_next`,
`apply_autocmds_group`, `nvim_exec_autocmds` and a scheduled Lua callback during
`vim.wait`. No Python failure or Lua traceback was available from that process.

## Scope and validation

- Core Python, headless frontend and Neovim end-to-end suites passed.
- The ACP questions UI scenario and GigaCode provider tests passed after aligning
  the core source path and GigaCode's static family capability declaration.
- Dedicated ACP `tests/fixtures/bootstrap.lua` passes with a real service,
  kernel, worker, terminal application and fake ACP agent. It proves draft
  preservation, completion before the first prompt, same-client reuse and no
  agent prompt in the recorded events.
- The broader session-features scenario remains unchanged. Native crash
  investigation is separate from the initialization contract.

The ACP UI harness now preserves an explicitly supplied `PYTHONPATH`, allowing
these tests to select the matching core checkout rather than silently loading an
older installed core. Run with both the current core and GigaCode source paths:

```sh
PYTHONPATH=/path/to/jusi/src:/path/to/jusi-gigacode/src .venv/bin/python -m pytest tests/test_followup_ui.py -q
```

Use absolute paths for `PYTHONPATH` when the subprocess changes working directory.
