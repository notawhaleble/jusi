# Plugin cell timing profile — 2026-10-09

Profiling only: no runtime, protocol, lifecycle or ownership behavior changed.
The development harness and timing wrappers live under `scripts/` and are not
loaded during normal operation.

## Conditions

Local loopback service on macOS 14.5 arm64, Python 3.12.1, Neovim 0.11.4,
Jusi 1.0.4, VisiData 3.4, ipykernel 6.31.0, jupyter_client 8.10.0 and Tornado
6.5.10. Small notebook: one cell, with `1 + 1` for the ordinary baseline and
`%%vd` over one small Python list for the bundled plugin. Each run starts a
fresh service and kernel; all execution samples use that live kernel. Each
plugin execution creates a fresh client, worker, bridge and application;
filesystem caches are warm. HOME and VisiData configuration are isolated from
user configuration. These are diagnostic local measurements, not cold-disk,
remote, large-data or external-provider benchmarks.

The primary VisiData run has 12 executions, alternating six explicit
`JusiClose` calls and six application `q` quits. A separate 12-execution run
uses the normal service without timing wrappers. Additional eight-execution
terminal and SQLite fixtures were run concurrently with each other, so their
numbers are comparative screening evidence rather than a controlled baseline.
A final two-execution diagnostic run isolates whole-service shutdown; other
tests were running during that probe.

## User-observed timings

All values are milliseconds. The ordinary baseline ends at the execute HTTP
response; plugin startup ends when the expected application value appears in
the Neovim terminal buffer. Close/quit ends when the controller client and its
local surface projection have both retired. These endpoints include local
transport and rendering work. Predicates are polled at 2 ms intervals.

| Operation | Instrumented median (range), 12 executions | Normal-service median (range), 12 executions |
| --- | ---: | ---: |
| Ordinary `1 + 1` response | 11.9 (11.4–19.0) | 11.7 (11.3–17.9) |
| Plugin surface projected | 95.5 (72.4–100.4) | 93.8 (72.2–102.2) |
| VisiData value visible | 311.3 (288.7–327.6) | 308.7 (292.8–319.5) |
| Explicit close, six samples | 186.4 (184.6–186.9) | 185.3 (177.9–188.8) |
| Application `q` to retirement, six samples | 185.8 (152.3–187.5) | 185.5 (182.2–187.5) |

Fresh service/connect took 163–174 ms and fresh discovery/kernel startup took
563–749 ms in these two runs. Stopping the already client-free owned service
and kernel took 1.18 s. These are single startup/stop samples per run.

The terminal fixture took 304 ms to its geometry text, 199 ms to explicit
close and 198 ms to retirement after Ctrl-C. The SQLite/VisiData fixture took
291 ms to its value, 21 ms to explicit close and 28 ms to retirement after
`q`. SQLite currently does not request the application editor-action socket,
which explains why it avoids the cleanup delay below. Its terminal application
and worker remain fresh processes.

## Measured attribution

Synchronous wrappers use `time.monotonic_ns()` around the actual service
methods and record only stage, time and outcome, never payloads. Nested spans
overlap and must not be added together. The normal-service comparison shows
less than 3 ms difference in the primary startup median.

- Kernel execution is about 3–5 ms, including the plugin handoff. Worker
  readiness is 69.5 ms median (55.9–72.3 ms); the worker's initial request is
  0.86 ms. Snapshot staging for this tiny value is not a bottleneck.
- The execute method returning to the first target surface attachment takes
  about 87 ms. This includes event delivery, frontend job launch, the fresh
  bridge interpreter and WebSocket attach. PTY spawn/geometry itself takes
  about 1.7 ms. The application starts only after that attachment.
- The remaining startup interval includes the fresh application interpreter,
  imports, VisiData initialization and first draw. Separate eight-process
  probes using captured pipes measured 16.8 ms for a bare Python process,
  58.9 ms for worker-host import, 91.7 ms for bridge-module import, 48.5 ms for
  VisiData application-module import without initializing VisiData, and
  105.2 ms for standalone VisiData import. These process totals overlap with
  the live-path stages; they are not extra costs to add. An isolated cProfile
  probe attributed most application startup work to imports, especially
  VisiData's feature/submodule loading; loading empty user configuration and
  installing editor hooks took about 10 ms under profiling. cProfile and
  import-time tracing affect absolute timings.
- Explicit close stops the PTY in about 1.3 ms and the worker in about 9.5 ms.
  Worker reader joins take about 0.01 ms. The editor-action socket endpoint's
  `close()` takes 159.8 ms median (120.4–163.2 ms), accounting for most of the
  observed close/quit latency. Its listener uses a 200 ms accept timeout;
  cleanup closes the socket and joins that listener before publishing client
  retirement. The same endpoint wait occurs after an application exits.
- The whole-service diagnostic shows an additional 1.00 s in
  `BaseEventLoop.shutdown_default_executor`. `Supervisor.close()` and
  `HTTPServer.close_all_connections()` each take less than 1 ms after kernel
  teardown. A final SSE `EventLog.wait_after(timeout=1.0)` remains active in
  an executor thread after transport closure; executor shutdown waits for
  it. Kernel stop itself is about 114 ms in the primary run. This is a
  separate whole-service delay, rather than worker shutdown cost.

## Planning inputs

The most clearly avoidable waits are the socket listener join on client
retirement and the outstanding SSE wait on service exit. Startup is cumulative
fresh-process/import cost: worker readiness, bridge attachment, then VisiData
loading. Any startup proposal should preserve target-side ownership, exact
client/worker identities, the first-attachment geometry gate, isolation and
fresh restart discovery. No optimization or architecture choice is made here.

The profile does not establish timings for companion `jusi-codex`, external
SQL providers, JShell, real remote targets or user-specific VisiData plugins.
Those can add their own imports, connections and initialization.

## Reproduction

From the repository root, use a new output directory for each run:

```sh
mkdir -p /tmp/jusi-plugin-profile
JUSI_PROFILE_OUTPUT=/tmp/jusi-plugin-profile JUSI_PROFILE_KIND=vd \
  JUSI_PROFILE_SAMPLES=12 \
  nvim --headless -u tests/frontend/minimal_init.lua \
  -l scripts/profile-plugin-cells.lua
.venv/bin/python scripts/profile-plugin-summary.py /tmp/jusi-plugin-profile
```

Set `JUSI_PROFILE_INSTRUMENT=0` for the normal service control run. Supported
kinds are `vd`, `sqlite` and `terminal`; the latter two enable repository test
fixtures explicitly through PYTHONPATH. The output directory contains
`frontend.json` and, for instrumented runs, `service.jsonl` with nested spans.
The harness cleans up its owned service, kernel, clients and temporary home.

[Recorded stage summaries and individual timing samples](plugin-cells-2026-10-09.json)
include the primary, control, fixture and shutdown diagnostic runs.
