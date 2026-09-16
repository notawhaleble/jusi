# Incident 0019: Liveness wrapper falsely reported kernel death

## Observed

A user reported that opening work with the migrated Codex plugin took down the
notebook runtime. The received failure was `kernel / inspect / kernel_died`,
message `Managed kernel process exited`, at `2026-09-16T01:03:03.748205Z`,
trace `trace_fe07b2050e4a4564acd36bc655e319fc`. It included PID 21465 but no
exit code, signal, or stderr. The original session's triggering sequence and
process exit status have not been recovered; plugin causation is unconfirmed.

## Reproduced defect

The managed adapter called Jupyter's synchronous `KernelManager.is_alive()`
and converted every exception to `False`. Jupyter caches its asyncio loop in a
context variable. If a running loop is inherited through `asyncio.to_thread`,
the synchronous wrapper attempts to run that loop again and raises. A real
live subprocess reproduces the reported failure without any plugin execution.

The supervisor's background monitor interpreted this false observation as
kernel death, published `off`, and invoked kernel and dependent-client cleanup.
Consequently an observation error could itself destroy a healthy runtime.
This is a demonstrated mechanism consistent with the report, not proof of the
original session's trigger.

## Resolution

The local managed adapter polls its owned provisioner's process directly, as
its process diagnostics and forced termination already do. Liveness no longer
passes through Jupyter's synchronous coroutine wrapper or converts its
exceptions into evidence of process exit. No protocol or resource model changes.

## Regression coverage

`test_liveness_check_uses_owned_process_with_inherited_running_event_loop` in
`tests/backend/integration/test_jupyter_adapter.py` uses a real subprocess,
Jupyter manager/provisioner, and a running loop copied into a background thread.
It verifies live inspection succeeds, then verifies actual termination still
produces `kernel_died` with SIGTERM diagnostics. It failed before the fix with
the reported message while the process was alive.
