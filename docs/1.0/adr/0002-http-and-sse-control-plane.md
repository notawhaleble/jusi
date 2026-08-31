# ADR 0002: HTTP Commands And SSE Events

- Status: accepted
- Date: 2026-08-31

## Context

The 0.x line-delimited stdio protocol combined process lifetime, command transport, event delivery, liveness rituals, and rendering inspection. Jusi 1.0 needs explicit resource inspection and ordered backend-to-frontend events locally and remotely.

## Decision

- Use HTTP for commands and authoritative resource inspection.
- Use SSE for the ordered backend-to-frontend event stream.
- Give every event an ID, supervisor-relative sequence, trace, layer, operation, and resource correlation.
- Support stream resumption or authoritative gap recovery.
- Add WebSockets or dedicated streams only when a concrete bidirectional/high-volume requirement demonstrates need.

## Consequences

- Commands have normal status, timeout, idempotency, and inspection semantics.
- SSE matches the dominant event direction and is easy to test without Neovim.
- Interactive terminal requirements remain separate from kernel control.
- A bounded replay/cursor policy must be defined during implementation.
