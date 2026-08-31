# ADR 0007: Curl Is The Initial Neovim HTTP/SSE Adapter

- Status: accepted
- Date: 2026-08-31

## Context

The first frontend slice must prove the shared HTTP/SSE contract from headless
Neovim without choosing a Lua dependency framework or making transport part of
the notebook model. Neovim provides safe subprocess lifecycle APIs but no
built-in HTTP/SSE client.

## Decision

Use `curl` through `vim.system` as the initial replaceable Neovim transport
adapter. Commands are passed as an argument vector, request bodies use stdin,
and SSE is consumed incrementally with buffering disabled. No shell is involved.

The adapter owns only HTTP encoding, SSE framing, timeouts, and
`frontend_transport` failures. The controller owns resource correlation and
ordered-event policy. The notebook model owns neither.

The SSE endpoint emits an immediate comment after its response headers. Receipt
of that comment or an event proves that the stream is open without adding a
domain event or sequence number. It does not establish supervisor identity or
kernel state; only validated events and resource responses do that.

## Consequences

- The walking skeleton runs with stock Neovim plus the service environment.
- ANSI-bearing output remains opaque data and is not parsed by the transport.
- Curl is an explicit runtime prerequisite for this adapter, not a permanent
  protocol commitment.
- A future native client can replace this adapter without changing notebook,
  controller, or wire semantics.
- Supervisor replacement and cursor-expiry recovery still require an explicit
  inspection/resynchronization design; stream openness alone is insufficient.
