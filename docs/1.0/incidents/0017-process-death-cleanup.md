# Incident 0017: Process death left owned work behind

## Observed

Release probes found that even an idle `:qa!` could leave the frontend-started
service and kernel running. Explicit shutdown also stalled behind execution
that ignored SIGINT. Service SIGKILL left a plugin worker behind during a
followup. Kernel SIGKILL during that followup remained `on` because the monitor
could not obtain the operation lane. Finally, editor exit against an external
target left its unlimited-duration curl request waiting for a response.

## Causes

The service had no editor lifetime signal independent of exit callbacks. Service
close queued behind active work before interrupting it. Worker EOF diagnostics
could fail on broken stderr before cleanup started, and the main worker thread
could remain blocked. Plugin followups occupied the serialized operation lane
without kernel-channel observations. Plain curl had no independent owner signal.

## Resolution

[ADR 0043](../adr/0043-owned-process-death-and-transport-loss.md) defines explicit
owner pipes for the local service and HTTP helpers, bounded escalation during
teardown, worker owner-loss cleanup, and kernel observation during idle or plugin
work. SSE keepalive loss now also detects a silent blackhole without claiming
kernel death or limiting user execution time.

## Regression coverage

The [lifecycle matrix](../architecture/lifecycle-reliability.md) covers these
failures with real owned processes, both normal exit and SIGKILL, external-target
survival, fresh kernel execution after death, and reconnect without resubmission.
