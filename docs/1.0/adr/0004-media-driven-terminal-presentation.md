# ADR 0004: Media-Driven Terminal Presentation

- Status: accepted
- Date: 2026-08-31

## Context

Text output from kernels and plugins may contain ANSI terminal sequences, including colored tracebacks. Implementing an ANSI parser in Jusi duplicates terminal behavior and makes presentation differ based on which kind of cell produced identical media.

The 0.x native-terminal path also coupled ordinary output to attach commands, environment payloads, supervisor PIDs, status files, and client-runtime processes.

## Decision

- Select presentation by media type and interaction requirements, not by ordinary/plugin cell classification.
- Feed textual and ANSI-bearing bytes unchanged to Neovim's terminal renderer.
- Use `nvim_open_term()` and its channel for non-interactive streamed terminal presentation unless implementation evidence requires another native surface.
- Use a real PTY or dedicated bidirectional stream only for clients needing input, terminal sizing, job control, or sustained high volume.
- Keep all terminal presentation and stream topology independent from HTTP kernel control and authoritative state.

## Consequences

- Code, traceback, shell-like, and plugin text share one presentation rule.
- Jusi does not parse ANSI escapes.
- Ordinary code output no longer needs the 0.x process-oriented terminal attachment machinery.
- Interactive clients can evolve without changing the basic kernel protocol.
