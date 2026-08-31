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

## Limits

These mechanisms make a future reproduction of Incident 0001 actionable; they
do not establish its cause. Parent PID, operating-system resource limits, and
plugin-worker chronology are not yet captured because those resources do not
exist in the walking skeleton.
