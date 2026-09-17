# Incident 0020: Codex Sheets Skipped VisiData Configuration

- Date: 2026-09-16
- Status: resolved in 1.0.2

`options.disp_menu = False` in the user's valid `~/.visidatarc` affected `%%vd`
but not `%%codex`, including after restarting Neovim. Initial diagnosis checked
only the bundled provider and missed the different application startup path.

Importing VisiData and calling `vd.run()` does not perform CLI configuration
loading. Codex duplicated editor integration but omitted `loadConfigAndPlugins`.

[ADR 0046](../adr/0046-shared-visidata-application-startup.md) extracts shared
startup from bundled VisiData and adopts it in the companion Codex source.
Regression coverage verifies configuration before sheet construction and at UI
startup. Existing running applications must be recreated to load configuration.
