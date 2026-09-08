# ADR 0022: Session-local failure inspection

Status: accepted

## Context

Local service startup can fail before there is a frontend session or an HTTP
endpoint. The launcher already captured stderr and exit status, but the command
discarded access to that object after displaying an abbreviated notification.
A trace ID therefore gave the user no usable inspection path. Missing explicit
configuration paths were similarly omitted from frontend notifications.

## Decision

Frontend core retains the last 50 received structured failures in Neovim-session
memory, independently of notebook, transport, and service lifetimes.
`:JusiTrace [trace-id]` opens a read-only scratch split; omitted ID selects the
latest failure's trace. It shows retained failures for that trace in received
order, preserving distinct causal failures and collapsing identical HTTP/SSE
reports. Completion offers retained trace IDs. Explicit inspection takes focus;
failure arrival only notifies.

Notifications include the available configuration path, exit status/signal, and
last stderr line for local service failures, plus the inspection command.
Inspection preserves origin, operation, reason, scope, resource, causal identity,
and available bounded process diagnostics.

Only failure-envelope fields and an explicit allowlist of diagnostic detail
fields are copied. Request bodies, argv, config contents, and environment
mappings are not captured. Strings are capped at 16 KiB and each record has a
32 KiB copying budget with bounded nesting/entry counts. Producers remain
responsible for non-secret messages and stderr; arbitrary text cannot be reliably
classified or scrubbed by the viewer. Scratch buffers disable swap and undo
files. Nothing is automatically persisted or fetched from the service.

## Consequences

Failed startup is inspectable without a running backend. Diagnostics survive
notebook close, restart, and transport disconnect, but expire through bounded
eviction or Neovim exit. This is a received-failure history, not a complete
distributed trace or durable backend log. Future trace storage can extend the
same identity contract without making local startup inspection depend on it.

Verification: `tests/frontend/diagnostics_spec.lua` covers retention, bounds,
causal grouping, duplicate reports, payload exclusion, and the real CLI parser's
failed-startup path through public frontend commands.
