# Kernel And Execution Observability

## Process Capture

The managed Jupyter adapter gives each kernel a private stderr capture file.
Kernel startup, death, and incomplete cleanup failures preserve, when available:

- kernel PID
- exit code or signal
- the final 16 KiB of stderr
- an explicit truncation flag

The capture file and Jupyter channels are frontend-independent resources owned
by the kernel adapter. They are removed and closed after normal stop and after a
failed graceful/forced cleanup attempt. Diagnostics are materialized before the
file is removed.

## Execution Context

Execution failures record UTF-8 payload byte count and line count, but never the
cell body. User exceptions retain error name/value and ANSI traceback output.
They remain `execution/execution_error` while the kernel stays `on`.

If the kernel dies during execution, the supervisor emits a primary
`kernel/kernel_died` failure with process diagnostics and a caused
`execution/cancelled` failure for the owning execution. The authoritative kernel
transition to `off` follows those failures in the ordered event chronology.

## Frontend Inspection

[ADR 0022](../adr/0022-session-local-failure-inspection.md) defines
`:JusiTrace [trace-id]`. With no argument it opens the latest received failure's
trace in a read-only scratch split. It remains usable after failed service
startup and after the owning notebook is closed. Notifications surface missing
configuration paths and local process exit/stderr causes directly.

History contains up to 50 bounded, selectively copied failure records for the
current Neovim session. Identical HTTP/SSE reports collapse; separate causal
failures remain inspectable. Request/config/environment data are not captured.
Failure producers must keep messages and stderr free of credentials.

## Limits

These mechanisms make a future reproduction of Incident 0001 actionable; they
do not establish its cause. Parent PID and operating-system resource limits are
not yet captured. Frontend failure history is neither a complete operation/event
timeline nor durable backend trace storage; records not received by this editor,
evicted records, and records from previous Neovim sessions cannot be inspected.
