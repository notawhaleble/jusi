# ADR 0003: Kernel State Is Off Or On

- Status: accepted
- Date: 2026-08-31

## Context

The 0.x session model exposed `idle`, `starting`, `connected`, `disconnected`, `stopping`, `stopped`, and `failed`. Real use showed that these mixed kernel truth, transport state, in-flight work, and accumulated error state. Reconnect behavior was unclear and often ineffective.

## Decision

- Kernel resource state is `off` or `on`.
- `checking` is a remote frontend view used only while verifying supervisor reachability and kernel ownership.
- Start and stop are separately identified operations.
- Kernel process death resolves to `off`.
- Frontend transport reconnection reconnects only to a surviving supervisor/event stream.
- The protocol does not offer kernel reconnection or a durable kernel `failed` state.

## Consequences

- Failed operations carry structured failures without poisoning durable resource state.
- UI can show operation progress while kernel truth remains simple.
- Remote ownership verification needs an explicit inspection operation.
- Old reconnect, expiry, healthcheck, and sidecar behavior is not migrated.
