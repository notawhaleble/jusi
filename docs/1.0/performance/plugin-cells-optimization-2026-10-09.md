# Plugin Lifecycle Optimization — 2026-10-09

The [original profile](plugin-cells-2026-10-09.md) is preserved as the baseline.
This pass removes avoidable idle waits and unnecessary fresh-process imports.

## Results

Same local macOS arm64 machine, Python 3.12.1, Neovim 0.11.4 and VisiData 3.4;
one small notebook cell, warm kernel and fresh plugin processes. Both columns
below use the normal, uninstrumented service. Baseline: 12 executions, six
closes and six application quits. After: 20 executions, ten closes and ten
application quits. The measurements use a temporary home and empty user
configuration apart from hiding the VisiData menu. No benchmark process ran
concurrently with the final measurements.

| User-observed endpoint | Before median | After median (range) | Reduction |
| --- | ---: | ---: | ---: |
| Execute to visible VisiData value | 308.7 ms | 263.5 ms (260.7–288.5) | 15% |
| Execute to projected terminal surface | 93.8 ms | 58.0 ms (56.8–59.9) | 38% |
| Explicit close to retired client/surface | 185.3 ms | 21.2 ms (20.4–21.9) | 89% |
| Application `q` to retired client/surface | 185.5 ms | 28.1 ms (27.5–29.5) | 85% |
| Stop owned service and kernel, already client-free | 1177.4 ms | 152.8 ms | 87% |
| Ordinary `1 + 1` execute response | 11.7 ms | 11.8 ms (11.6–19.4) | effectively unchanged |

Whole-service stop is one sample per run, not a percentile claim. Three
additional final runs measured 153.1–154.3 ms for that endpoint. Startup and
close medians include the first sample; no slow samples were discarded.

A separate 12-execution instrumented VisiData run measured 261.1 ms to a visible
value, 22.0 ms explicit close and 28.7 ms application quit. Its explicit-close
range was 21.5–54.4 ms, including one scheduling/process-exit outlier. This
confirms the dominant improvements without treating instrumentation as free.

Final, separately run diagnostic fixtures measured:

| Fixture, 12 executions | Execute to visible | Explicit close | Application exit |
| --- | ---: | ---: | ---: |
| SQLite/VisiData | 252.0 ms | 21.3 ms | 26.2 ms (`q`) |
| Terminal fixture | 255.0 ms | 31.9 ms | 31.3 ms (Ctrl-C) |

These are fixture medians, not claims about external providers. SQLite's first
execution took 472.9 ms and the terminal fixture's first took 319.7 ms. The
original fixture runs overlapped each other, so they are not used to calculate
controlled before/after percentages.

## Changes and Attribution

1. **Editor-action listener wakeup.** The client-owned listener now waits on its
   accept socket and a private wake socket. Close wakes the listener immediately,
   joins it, then closes peers and removes the private directory. The measured
   endpoint close span fell from about 160 ms to about 0.2 ms.
2. **SSE cancellation.** Each HTTP event stream owns a cancellation event.
   Disconnect or task cancellation wakes its condition waiter, including when
   cancellation wins the race with entry into the executor thread. Other live
   event readers retain their own waits and replay cursors.
3. **Terminal reader cancellation.** A closing WebSocket wakes its own attachment
   queue, so cancelling the asyncio pump also releases a pending executor read.
   This removed the remaining variable 500 ms queue-wait tail. Final executor
   shutdown is sub-millisecond; kernel shutdown itself remains about 114 ms.
4. **Lean process imports.** Package version metadata is resolved on explicit
   access, CLI `--version`, provider discovery or kernel attestation. The bundled
   provider's package initializer no longer imports it into each worker and
   terminal application. Worker port annotations no longer initialize the whole
   domain model. Installed version reporting remains accurate. Worker readiness
   fell from roughly 70 ms to roughly 40 ms in instrumented runs.
5. **Terminal launch and delivery.** The stdlib-only controlling-terminal launcher
   uses `-S`, avoiding a redundant site initialization before exec. The actual
   application retains normal site packages, environment and VisiData user
   configuration. Interactive WebSocket output enables TCP_NODELAY; no separate
   wall-clock speedup is attributed to that socket option.

[ADR 0048](../adr/0048-owned-waits-wake-on-transport-close.md) records the cleanup
ownership rules; [Incident 0027](../incidents/0027-idle-waits-delayed-plugin-cleanup.md)
records the blocking-wait failure. Resource identities, worker isolation,
first-attachment geometry, full cleanup and existing wire contracts are retained.

## Remaining Cost

The critical startup path still creates a worker, a frontend bridge and a
target application. The final instrumented execute-return-to-first-attachment
interval is about 81 ms and includes bridge interpreter/import startup. VisiData
loads its normal features, user configuration and editor integration before
first draw. Removing those imports or sharing live interpreters would change
application behavior or process ownership, rather than simply remove idle work.

These results apply to a small local value. Large snapshots, user VisiData
plugins, external provider initialization and real remote links can add cost.

## Verification and Reproduction

The profiling harness and commands are documented in the original profile.
For a normal-service comparison, set `JUSI_PROFILE_INSTRUMENT=0`; use a new output
directory for every run and keep benchmark runs sequential. The
[recorded optimized samples](plugin-cells-optimized-2026-10-09.json) contain all
final per-stage samples and environment metadata.

Regression coverage tests cancellation before and during waits, independent
live event readers, idle peer/server SSE disconnect, idle WebSocket disconnect,
incomplete editor-action requests, repeated close, fresh-process import
boundaries, CLI/distribution version agreement, and application site loading.
Existing suites exercise real geometry, raw bytes, reconnect, copy/open,
interruption, worker/kernel cleanup and frontend behavior.

Validation completed: `python -m pytest -q` passed 274 tests with one opt-in
skip; both the headless frontend suite and real-service Neovim end-to-end
suite passed. `git diff --check` passed.
