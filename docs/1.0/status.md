# Jusi 1.0 Status

Updated: 2026-09-01

## Current Facts

- The foundation review is accepted; ADRs 0001-0016 are active.
- The 0.x Python package, bundled `jusi_vd`, legacy tests, and stale smoke script have been removed from the active tree. Their exact provenance remains under `docs/legacy/0.x/` and Git history.
- The Python package is now `1.0.0.dev0` and contains framework-independent domain/application layers, a managed Jupyter adapter, and a thin Tornado HTTP/SSE service.
- The walking-skeleton protocol supports start, execute, inspect, stop, ordered replayable events, structured failures, and idempotent repeated stop.
- Python and Lua consume shared command and event fixtures, including unchanged ANSI-bearing text.
- The Neovim frontend has a pure Lua symbolic-format parser, model-owned cell identities, extmark anchors, locally spliced structural reconciliation, and an executable 10,000-line/1,000-cell performance harness.
- `.vipynb` remains the canonical extension with Neovim filetype `jusi`; legacy `##` notebooks are detected and rejected without mutation until explicit conversion exists.
- A replaceable curl/`vim.system` adapter and Lua controller now bind that model to HTTP commands and ordered SSE events.
- Textual output now projects unchanged through `nvim_open_term()` into a hidden, cell-attached terminal buffer. Renderer choice is media-driven.
- Repository-root Neovim runtime loading exposes explicit connect, kernel start/stop, execute, output-open, and disconnect commands. Output opens in a predictable bottom split.
- Frontend disconnect now closes only transport; the live buffer model, cell identities, kernel view, and output surfaces survive an inspect-before-replay reconnect.
- Explicit `JusiServiceStart`/`JusiServiceStop` now own one notebook-local service process, validate readiness, retain bounded stderr, distinguish process/supervisor/transport identities, and clean up on buffer destruction. Transport connect still never spawns implicitly.
- Event-stream connection now inspects authoritative supervisor/kernel state and the retained replay window first. Supervisor replacement and expired cursors replace stale frontend state; replayable cursors consume only missing ordered events.
- Every initial event kind now has a closed payload contract shared by JSON Schema, Python, Lua, and valid/invalid fixtures. Runtime supervisor events are validated in backend tests before frontend dispatch relies on them.
- Kernel death and cleanup failures preserve bounded stderr plus PID/exit/signal diagnostics, and execution failures record non-secret payload size. Kernel death emits a caused execution failure before the authoritative `off` transition.
- Plugin discovery is specified as a fresh short-lived process and exact plugin behavior as supervised workers; the service will not import third-party plugin code or reuse the incompatible 0.x handler entry-point group.
- The versioned data-only plugin catalog schema and shared Python/Lua fixtures now cover exact providers, shared family claims, capabilities, media/interaction requirements, kernel adapters, worker references, and duplicate identity rejection.
- A fresh-process discovery adapter now loads `jusi.plugins.v1` providers only in an owned child process, returns one fail-closed validated catalog through a private bounded result, and preserves timeout/exit/signal/stderr diagnostics without importing provider modules in the supervisor.
- Shared Python/Lua catalog conformance now rejects incompatible descriptors for a shared family or one magic claimed by incompatible families.
- Start now creates an authoritative notebook-runtime generation only after fresh catalog discovery and kernel readiness; health snapshots bind `runtime_id`, `notebook_id`, `discovery_id`, catalog, and `kernel_id`.
- Protocol v1 and the Neovim frontend now implement full `restart_notebook`/`:JusiRestart`: teardown fences the old kernel, discovery and configuration are reloaded, backend and frontend runtime identities are replaced, text/undo/buffer identity survive, and post-teardown failure leaves the kernel off without stale runtime restoration.
- Real Neovim end-to-end coverage sets a kernel global, restarts, verifies fresh runtime/discovery/kernel/notebook/cell identities and retired output, then proves the new kernel no longer contains the global.
- Exact-plugin workers now have a versioned generic control envelope, a fresh-process child host, private bounded length-prefixed control descriptors, immutable context, catalog-only entry-point selection, runtime/family/capability validation, and idempotent process-aware cleanup.
- Plugin stdout cannot corrupt worker control; it joins bounded stderr diagnostics. Worker handler failure, timeout, malformed/oversized data, exit, and signal fence only that worker. This is reliability isolation, not a security sandbox.
- Notebook-runtime stop/restart owns worker cleanup. Incomplete cleanup prevents full replacement before the old kernel is stopped; ordinary kernel stop still establishes authoritative `off` and reports any worker cleanup failure.
- Service placement is explicit: the authoritative service runs at the kernel target. `JusiServiceStart` is only a notebook-local convenience for local targets; remote targets connect directly to their remote service over HTTP/SSE without a mandatory local proxy or editor-wide singleton.
- Fresh kernels import catalog-declared adapter modules and require exact plugin-version/family attestation before becoming `on`. Execution captures one versioned exact-provider handoff MIME record, excludes it from presentation, and validates it against the current runtime catalog while leaving the kernel on for local mismatches.
- Lifecycle and execution use a serialized operation lane distinct from the short-lived authoritative-state lock, so health and inspection remain responsive during bounded discovery, kernel, cleanup, and future worker I/O.
- Exact-plugin clients are durable within their notebook runtime. Ordinary operation results never decide client lifetime; only explicit close, fatal client/worker loss, or owning-runtime cleanup ends one.
- A validated exact-plugin handoff now starts the catalog-selected worker, creates a protocol-visible client identity, delivers the private payload only to that worker, and leaves the client active after the initiating execution. Health and ordered events expose client lifecycle without exposing plugin application data.
- `close_client` is an explicit idempotent HTTP/controller operation. It stops only the exact worker, emits `client.closed`, reports `already_absent` on a repeated close, and leaves the kernel on. Fatal worker startup/control failures use typed core failures scoped to the client.
- The internal POSIX terminal broker now proves the ADR 0016 geometry gate before protocol exposure: it sizes and verifies the target PTY before spawning the application, establishes a controlling terminal in an isolated launcher, preserves raw terminal bytes, verifies later resize, and performs bounded idempotent process-group cleanup with kill escalation.
- Protocol v1 now has one closed terminal-surface resource and lifecycle shared by JSON Schema, Python, and Lua. A `terminal_interactive` worker must return exactly one typed private `terminal_surface.create` request before its client and surface publish atomically; launch argv, cwd, and environment never enter health, events, or Lua. Client cleanup retires its surface first.
- Backend-only plugins expose generic terminal or web surfaces. SQL/VisiData, shell, terminal text, and browser content remain plugin-owned; frontend core manages native surfaces, input/geometry, and versioned generic actions. Recoverable application errors stay in plugin presentation, while fatal worker/client/surface loss always uses the typed core failure channel.
- The 1.0 development environment is `.venv`; legacy `venv2` imports Jusi 0.1.1 from the detached `/Users/niku/Documents/dev/jusi-0.x` worktree.
- The headless-Neovim black-box scenario starts the real service and kernel, executes `1 + 1` from a model cell, receives the ordered `text/plain` result event, and stops the kernel without loading an interactive UI.
- The same walking skeleton has been exercised successfully in an interactive clean-config Neovim session.
- The archival tag `legacy/0.x-pre-1.0-2026-08-31` and detached sibling worktree `/Users/niku/Documents/dev/jusi-0.x` preserve the audited backend snapshot.

## Walking Skeleton Boundary

Implemented:

- service readiness
- local managed Python kernel adapter
- HTTP start/inspect/execute/stop
- supervisor-owned `off`/`on` kernel truth
- ordered SSE event IDs, sequence, replay cursor, and gap detection
- `text/plain` results and ANSI-preserving text output
- structured process-aware failures
- idempotent stop reporting `stopped` or `already_absent`
- black-box service/kernel scenario runnable outside the restricted sandbox

Deferred:

- remote supervisors and `checking`
- richer window/focus policy and interactive PTY clients
- input, interrupt, completion, rich media, PTY clients, and durable event storage

## Next Boundary

ADR 0016 establishes the target-side PTY/per-surface bridge boundary.
Incident 0003 preserves the legacy VisiData geometry failure that its handshake
and tests must address. Surface identity is implemented; WebSocket attachment,
stream cursors, and bridge integration have not begun.

1. bind the proven target PTY broker to the terminal surface at geometry-gated WebSocket attachment
2. specify byte framing, bounded cursor replay, resize acknowledgement, and continuity failure atomically
3. design the remote-safe web-surface contract after the terminal boundary is established
