# Jusi 1.0 Status

Updated: 2026-09-16

## Current Facts

- **Jusi 1.0.1 is released.** [PyPI](https://pypi.org/project/jusi/1.0.1/) and the [GitHub release](https://github.com/notawhaleble/jusi/releases/tag/v1.0.1) are public. Tag `v1.0.1` identifies `84899ea`; `main` was fast-forwarded to that commit. GitHub assets include the wheel, sdist, checksums and recorded demo.

- Managed-kernel liveness now polls the owned process directly. A reproduced inherited-asyncio-loop error in Jupyter's synchronous wrapper previously reported a live kernel as dead and triggered runtime cleanup. The Codex-session report's original trigger remains unconfirmed. See [Incident 0019](incidents/0019-liveness-wrapper-falsely-reported-kernel-death.md).

- Release validation passed 254 Python tests with one opt-in skip, both Neovim suites, metadata and checksum checks, and isolated base/VisiData installation. Fresh downloads from production PyPI and GitHub match the verified artifacts. A clean PyPI installation verified the public frontend tag and default `install-skills` release-tag fetch.

- Release-facing documentation separates the README quick start, user guide, contributor setup and release procedure. The recorded GIF is embedded in the GitHub README; the source demo notebook and walkthrough are in `examples/`, and the recorded MP4 is attached to the GitHub release.

- Offline `jusi import-ipynb` / `jusi export-ipynb` convert cell source only (import includes Markdown/raw as ordinary executable cells; export creates code cells), with overwrite protection, source whitespace preservation, and strict native delimiter validation. Outputs and followup history are excluded. See [conversion](notebook-conversion.md).

- Plugin and family skills now ship in the Python distribution. `jusi install-skills` installs both with an exact-version Git reference, offline cache reuse, edit protection and rollback. External plugin work stays outside the managed core reference. Real-Git integration and isolated wheel installation cover the installer; independent `%%todo` skill evaluation is deferred. See [skills](skills.md) and [ADR 0045](adr/0045-versioned-authoring-skills.md).

- Distribution uses pip for the Python backend and a separate Git/plugin-manager installation for Neovim. The backend wheel excludes frontend assets; `jusi --version` identifies the installed Python distribution. An isolated gate verifies the wheel alongside a separately cloned native Neovim package, including base execution and optional VisiData copy/open. See [installation](installation.md) and [distribution verification](architecture/distribution-verification.md).

- Release lifecycle probes cover editor/service/kernel death across idle, execution, input, terminal client, followup and interrupt-ignoring work. Owned service and HTTP processes follow owner-pipe lifetime; explicit teardown can terminate an interrupt-ignoring kernel. Kernel death is observed during idle and plugin work. External runtime survival and loopback dropped/silent transport loss have dedicated coverage. Real remote-machine loss review remains deferred. See [ADR 0043](adr/0043-owned-process-death-and-transport-loss.md), [Incident 0017](incidents/0017-process-death-cleanup.md), and the [test matrix](architecture/lifecycle-reliability.md).

- Notebook statuslines show kernel truth separately from transport and label disconnected cached state as last-known. Only kernel badges have state backgrounds (gray unknown/off, green on, amber stale); the main statusline retains theme defaults. Off kernels hide their target alias. Successful stop retains off after session cleanup; start/stop operations are separate labels. mode:cell has a purple badge, and output labels omit .vipynb. Output statuslines expose monotonic editor-local IDs for counted G/Q and backslash g/q targeting; stale IDs cannot close replacements. Notebook-local backslash actions cover editing, submission, history, parking and lifecycle. See [ADR 0042](adr/0042-statuslines-and-output-addressing.md) and [Incident 0016](incidents/0016-statusline-lifecycle-color-flicker.md).

- Terminal projection windows clear inherited number/sign/fold/status gutters before PTY startup, so their first geometry uses the full split width. Real-terminal coverage enables notebook gutters to reproduce and prevent the mismatch. See [Incident 0015](incidents/0015-inherited-gutters-reduced-initial-terminal-width.md).

- J/J! now complete loaded notebooks, discovered magics and target configuration aliases; magic reuse preserves identity/history and bang uses contextual submission. The Ctrl-\ Ctrl-\ focus chord respects user mappings and finds visible notebooks from unrelated buffers, preferring the current tab. Palette notebooks open on the left; output layout stays in the notebook tab. Shared palette contracts and frontend/real-kernel tests cover the flow. See [ADR 0041](adr/0041-palette-and-focus-controls.md).

- Backend-driven show_diff now displays two read-only snapshots in native Neovim diff windows in a new tab. It reuses chunked editor delivery and acknowledgment, with no accept/reject or writeback behavior. Shared contracts, paired-transfer tests and the terminal end-to-end fixture cover display and source-close independence. See [ADR 0040](adr/0040-show-diff-is-display-only.md) and the [application guide](architecture/show-diff.md). Display-only diff is ready for manual use.
- Manual testing between two Macs confirmed remote plain-cell execution, VisiData interaction, zY copy, Ctrl-O open and JusiStop over an SSH-forwarded service connection. Public authentication/TLS deployment and connection-loss testing remain separate.

- Total copy/open and VisiData snapshot size ceilings are removed. Application exports use disk-backed chunked UTF-8 delivery with renewable inactivity leases; private worker editor results use data framing. VisiData uses kernel-owned target artifacts rather than embedding snapshots in control messages. Tests exercise multi-megabyte file delivery, large real VisiData copy/open, chunk validation and cleanup. Destination memory and structural/type validation still apply. See [ADR 0039](adr/0039-user-data-is-not-a-control-frame.md).

- Target aliases now compose local service/connect/kernel startup through `JusiStart <alias>` and complete owned cleanup through `JusiStop`. Remote aliases connect directly and leave their external service alive after kernel stop. Cancellation, failed startup, target-specific restart and foreign-notebook rejection have real-service coverage. Regular-config manual testing can use the per-launch legacy override in the [contributor guide](../../CONTRIBUTING.md). See [ADR 0038](adr/0038-target-alias-start-stop.md). Remote plain cells, VisiData copy/open and stop have passed manual review between two Macs.

- Established plugin operations now support concurrent exact `JusiInterrupt`, recoverable worker rejections, deadline-free followups, and full close of busy work. Health publishes active client operation identities; cancellation preserves the client/surface. See [ADR 0034](adr/0034-concurrent-plugin-operation-interruption.md).
- Backend-driven copy/open now uses a private target-side client channel, captured UTF-8 content over HTTP, exact editor recipients, replay-safe delivery and acknowledgments independent of ordinary plugin work. Python application helpers and `jusivim PATH` support selected text and target-read files; exported buffers survive source close. Public `JusiCopy`/`JusiOpen` remain withdrawn. See [ADR 0036](adr/0036-application-driven-editor-actions.md) and the [fixture guide](architecture/backend-driven-editor-actions.md). Application work leases and writeback remain deferred.
- Bundled `%%vd` now uses ordinary plugin discovery, kernel-expression snapshots, an isolated worker and VisiData terminal, with application-driven `zY` copy and Ctrl-O open. Python containers and pandas tables are supported through data-only snapshots; no-op legacy followups are not advertised. VisiData is available through the optional `vd` extra. See [ADR 0037](adr/0037-bundled-visidata-provider.md), the [usage guide](architecture/bundled-vd.md), and [Incident 0014](incidents/0014-embedded-visidata-startup-and-editor-actions.md). Local and remote VisiData use has passed manual review.
- The plugin and family development skills are packaged and structurally validated. Independent practical evaluation of the plugin skill is reserved for the planned new `%%todo` plugin, not inferred from bundled `%%vd`. External provider migration remains deferred.

- Accepted new executions now close final unparked outputs on the same kernel through full artifact cleanup, including Enter's execution branch. Busy work and followup clients survive; `JusiPark` or cell-mode `S` toggles explicit retention, shown by `~` beside the status symbol (ADR 0033). Folded history inherits Normal through a window-local mapping, preventing stale/destructive backgrounds after theme changes (Incident 0013).

- Folded history now displays plain `history: N entries` with a muted foreground and blank fill; expanded history retains its delimiter. The terminal development fixture visibly displays initial/followup bodies and supports readable line input. Followup success notifications are removed; failures remain visible.

- Offline navigation and buffer-local cell mode now provide legacy-style Normal-mode keys, double-line border overlays in the existing status colors, native Insert behavior, cell text operations and contextual `JusiSubmit` dispatch. Navigation follows linked cells and expanded history; input/followup identities and full cell retirement remain unchanged. See [ADR 0032](adr/0032-cell-mode-and-contextual-submission.md). Manual interaction review passed.

- Followup-capable plugins now capture durable cell history on initial handoff and followup delivery. Native per-window history folds, offline toggle/apply commands, undoable restoration and isolated entry syntax/indentation are implemented. Pending accepted text waits through structural damage and retires with its opener. See [ADR 0031](adr/0031-foldable-followup-history.md).

- Cell-local syntax and indentation now use installed Neovim runtime profiles in a notebook-owned local editing worker. Plain Python works before connection; discovery supplies family defaults and exact client handoff selects provider overrides. Visible-cell extmarks update in Insert mode without cross-cell syntax/indent leakage. See [ADR 0030](adr/0030-cell-local-syntax-and-indentation.md).

- Whole-cell deletion now retires model entries even when the opener extmark has vanished, including undo-created cells. Reconciliation begins from the linked left boundary so output cleanup receives every retired identity. See [Incident 0012](incidents/0012-whole-cell-deletion-skipped-retirement.md).

- Retiring output now closes windows still displaying its buffer before deleting it, for ordinary and interactive terminals. Reused windows survive; Neovim’s last window remains with replacement content. See [Incident 0011](incidents/0011-retired-output-left-a-split-open.md).

- Kernel execution and input waiting now have no automatic deadline. Explicit interrupt and cell/runtime cleanup remain authoritative; execution HTTP has no total timeout, while connection setup and control/resource operations stay bounded. See [ADR 0029](adr/0029-cell-execution-has-no-automatic-deadline.md).

- Cell status now uses opener-anchored extmarks with symbols after the opener and matching opener/closer colors, with purple busy *, green done ✓, red error ✗, orange interrupted !, blue followup >, and yellow never-executed delimiters. RGB and 256-color terminal palettes are provided. No sign or status columns are used. Execution/input, client lifecycle and exact followup operations drive the projection; ordinary typing only moves existing extmarks. See [ADR 0028](adr/0028-cell-status-is-an-inline-extmark-projection.md).

- The foundation review is accepted; ADRs 0001-0016 and 0018-0042 are active. ADR 0017 and all web-surface implementation are explicitly deferred while the product direction—including web-as-text—is still exploratory.
- `JusiTrace [trace-id]` inspects the latest or selected received failure, including bounded stderr, process status, configuration paths, and resource identities. ADR 0022 retains 50 selectively copied records independently of notebook/service lifetime; failed CLI startup is covered through the public command path. This is session-local history, not durable backend tracing.
- The 0.x Python package, bundled `jusi_vd`, legacy tests, and stale smoke script have been removed from the active tree. Their exact provenance remains under `docs/legacy/0.x/` and Git history.
- The Python package is now `1.0.0.dev0` and contains framework-independent domain/application layers, a managed Jupyter adapter, and a thin Tornado HTTP/SSE service.
- The walking-skeleton protocol supports start, execute, inspect, stop, ordered replayable events, structured failures, and idempotent repeated stop.
- Jupyter stdout, stderr, display, result, and traceback messages now enter the ordered event log as they arrive instead of being retained until execution completion. Output already observed remains available if the execution later times out or the kernel dies.
- ADR 0018 bounds each ordinary text-output event to 16 KiB of UTF-8 while preserving exact ordered concatenation. This makes retained output memory finite without parsing ANSI or turning SSE into the sustained interactive stream.
- Python and Lua consume shared command and event fixtures, including unchanged ANSI-bearing text.
- The Neovim frontend has a pure Lua symbolic-format parser, model-owned cell identities, extmark anchors, locally spliced structural reconciliation, and an executable 10,000-line/1,000-cell performance harness.
- `.vipynb` remains the canonical extension with Neovim filetype `jusi`; legacy `##` notebooks are detected and rejected without mutation until explicit conversion exists.
- A replaceable curl/`vim.system` adapter and Lua controller now bind that model to HTTP commands and ordered SSE events.
- Textual output now projects unchanged through `nvim_open_term()` into a cell-attached terminal buffer. Its first output reveals a bottom split without taking focus. Renderer choice is media-driven.
- Repository-root Neovim runtime loading exposes explicit connect, kernel start/stop, restart, execute, artifact close/focus-toggle, and disconnect commands. ADR 0020 gives ordinary output and plugin clients one cell-oriented interaction without merging their internal ownership.
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
- Terminal surface attachment is now implemented end to end: a dedicated WebSocket subprotocol carries closed attach/resize/failure controls, cursor-framed raw target output, and raw frontend input. The target process is spawned only after verified first-attachment geometry; bounded replay, single-writer ownership, slow-consumer fencing, and explicit cursor expiry do not use the supervisor operation lane.
- The repository `jusi terminal-bridge` resolves the relative surface endpoint against the configured local or remote service URL and relays opaque bytes and verified resize controls inside a native Neovim terminal job. Health reconciliation projects existing surfaces without duplicating jobs. The running bridge now survives WebSocket loss by reattaching with a fresh identity from its exact in-memory consumed cursor; expired continuity ends visibly without guessed replay. A dead bridge process is not silently recreated.
- Incident 0004 established that the bridge must own its local TTY mode as part of byte transport. It now selects raw input with guaranteed restoration, so single keys and Ctrl-C reach the target immediately instead of being echoed/buffered or signalling the bridge. Automatic surface creation still leaves focus in the notebook.
- Cell-oriented commands use stored model identity when invoked from a Jusi projection and notebook cursor coordinates only from notebook text. Incident 0005 prevents terminal screen rows from being mistaken for notebook coordinates during asynchronous client cleanup.
- `JusiExecute` reveals one current cell artifact without taking focus; it explicitly replaces prior cell-owned clients before a new execution. Native window close only hides an artifact, `JusiToggleFocus` reopens or navigates to/from it, and `JusiClose` performs the appropriate frontend-output or backend-client teardown while leaving the kernel on.
- `JusiInterrupt` targets the exact active execution through a control path that does not wait behind execute. Active execution identity survives authoritative health resynchronization; successful interruption completes it as `interrupted`, keeps the kernel on, and permits subsequent execution. Plugin-specific interrupt remains distinct from close and will preserve a usable durable session when the generic concurrent client-operation path lands.
- Unexpected target terminal-process death emits a typed `client/run_terminal_surface/channel_closed` core failure with process diagnostics, retires only the owning surface/client/worker, and leaves kernel truth unchanged.
- A test-only `terminal_fixture` exact plugin now proves the complete Neovim path through fresh discovery, kernel attestation/handoff, worker isolation, target PTY, WebSocket bridge, first-draw geometry, opaque input/output, explicit client close, and kernel survival. It is outside production packaging and is discovered only through an explicit test `PYTHONPATH`.
- Backend-only terminal plugins expose generic terminal surfaces. SQL/VisiData, shell, and terminal text remain plugin-owned; frontend core manages native surfaces and generic input/geometry. Web surfaces remain a conceptual later family only. Recoverable application errors stay in plugin presentation, while fatal worker/client/surface loss always uses the typed core failure channel.
- The 1.0 development environment is `.venv`; legacy `venv2` imports Jusi 0.1.1 from the detached `/Users/niku/Documents/dev/jusi-0.x` worktree.
- The headless-Neovim black-box scenario starts the real service and kernel, executes `1 + 1` from a model cell, receives the ordered `text/plain` result event, and stops the kernel without loading an interactive UI.
- A real-kernel text reliability scenario proves pre-response SSE delivery, ordered ANSI stdout/stderr/result media, ANSI traceback preservation, survival after an ordinary execution error, and successful execution of a generated roughly quarter-megabyte markdown-like body. It does not reproduce or explain Incident 0001.
- The same scenario proves a 40 KiB Unicode/ANSI output is split into byte-bounded SSE records and reconstructs exactly; frontend coverage deliberately splits an ANSI escape sequence across writes and lets Neovim consume it.
- The SQL/VisiData migration audit preserves family alias routing, provider-owned VisiData/database behavior, durable clients, and generic terminal presentation while retiring 0.x handler/runtime/frontend-config machinery. The clean `jusi-sql` and `jusi-sqlite` repositories remain untouched and still target the incompatible 0.x contract.
- ADR 0019 is accepted with the shared-host qualification: runtime configuration is target-side and should contain only capabilities/routing relevant to that target; providers should normally resolve credentials from their worker environment or another target-side secret mechanism.
- `jusi serve` now snapshots bounded data-only TOML from target-side `~/.jusi/jusi.toml` on every kernel start/restart, with `--config PATH` for an explicit target file. Missing defaults are empty; missing explicit files and malformed/oversized/unsupported data fail the runtime operation without stopping the service, discovery, or spawning a kernel. Configuration values remain absent from public state and diagnostics; the palette publishes only discovered alias names.
- Kernel adapters may receive that private frozen snapshot through `configure_jusi_runtime_v1` before their IPython extension loads and readiness attests. A real-kernel test verifies delivery; restart and redaction tests verify fresh generations without exposing values.
- A central test-only `sqlite` exact plugin now proves the first real SQL path without modifying or copying the external 0.x packages: target-side `[sql.main]` resolution, exact kernel handoff, isolated worker, private 0600 launch payload, read-only SQLite connection, real VisiData in the generic target PTY, native Neovim terminal projection, explicit client close, and kernel survival.
- The isolated VisiData fixture disables VisiData user-state loading and persistence. Its end-to-end check rejects the observed state-directory `FileExistsError`; production SQL/VisiData configuration remains plugin-owned.
- The SQLite development fixture now enforces distinct catalog, kernel-adapter, worker, and terminal-application imports. Removing accidental IPython initialization from fresh worker/application paths reduced the measured warm-kernel execute-to-visible median from about 1.54 s to 0.65 s on the reference development machine; CI blocks boundary regression without imposing a wall-clock threshold.
- VisiData 3 is an optional `vd` extra and a test dependency; core Jusi has no mandatory SQL, SQLite-plugin, or VisiData dependency. pandas is a test dependency for snapshot coverage and remains optional in user kernels.
- The same walking skeleton has been exercised successfully in an interactive clean-config Neovim session.
- The archival tag `legacy/0.x-pre-1.0-2026-08-31` and detached sibling worktree `/Users/niku/Documents/dev/jusi-0.x` preserve the audited backend snapshot.

## Completed Walking Skeleton

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
- dead-bridge replacement policy and multiple terminal observers
- plugin interrupt, rich media, and durable event storage

## Next Boundary

Collect feedback on 1.0 and handle concrete regressions in follow-up releases.
Real remote connection-loss review remains deferred until a remote target is
available. Independent `%%todo` skill evaluation is deferred at the user's
request. Legacy Jusi notebook migration is out of scope; external plugin and
SQL-family migrations remain separate work.
