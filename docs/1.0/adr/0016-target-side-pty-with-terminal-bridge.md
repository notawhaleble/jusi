# ADR 0016: Interactive Terminal Surfaces Use A Target-Side PTY And A Jusi Bridge

- Status: proposed
- Date: 2026-09-01

## Context

Backend-only plugins such as SQL/VisiData and shells need a real terminal while
the same repository must support services running locally or beside a remote
kernel. Neovim must remain the terminal emulator; Jusi must not parse ANSI or
reimplement a terminal screen.

The 0.x design exposed backend-generated attach commands and environments to
the frontend, which rewrote and launched them differently for local, venv, SSH,
Docker, and combined targets. It also allowed a resize notification to appear
successful without reaching the target application. Incident 0003 preserves
that failure evidence.

HTTP/SSE remains appropriate for control and ordered resource events, but an
interactive terminal is genuinely bidirectional and potentially high-volume.
Routing its bytes through plugin request/response calls or ordinary SSE events
would couple terminal backpressure to kernel control and event observability.

## Proposed Decision

The target-side Jusi service owns a generic terminal-surface broker. For an
interactive surface it owns the target PTY master, child-process diagnostics,
stream sequencing, and attachment lifecycle. The exact backend plugin supplies
a typed terminal launch request; it remains responsible for the program,
application state, and application-specific control. Core owns only generic
process/PTY mechanics.

The frontend launches a repository-supplied, per-surface `jusi terminal-bridge`
inside a native Neovim terminal job. The bridge connects to the target service
using a dedicated WebSocket subprotocol and relays bytes without interpreting
them:

- Neovim terminal input -> bridge stdin -> binary WebSocket frame -> target PTY
- target PTY -> binary WebSocket frame -> bridge stdout -> Neovim terminal
- local terminal `SIGWINCH`/size -> typed WebSocket geometry frame -> target PTY
- typed signal/attach acknowledgements use control frames on the same ordered
  connection

This bridge is not a frontend-local Jusi service, kernel gateway, or plugin
runtime. It is one replaceable transport adapter for one surface. The
authoritative service, plugin worker, application, and PTY remain at the kernel
target.

The frontend never executes an attach command or environment supplied by a
plugin or remote service. A surface descriptor contains only closed protocol
data such as `surface_id`, `client_id`, kind, capabilities, relative endpoint,
and WebSocket subprotocol. Service credentials come from the configured target,
not replayable surface events or plugin payloads.

### Geometry Gate

An interactive full-screen application must not draw against guessed geometry.
The initial sequence is:

1. the worker requests a generic terminal surface with a typed launch contract
2. core publishes the client and unattached surface identities
3. Neovim creates the terminal window and starts the bridge
4. the bridge attaches with the actual rows and columns
5. the target broker applies and verifies PTY geometry
6. only then does it release/spawn the terminal application and acknowledge the
   surface attachment

Later resize acknowledgements mean the target PTY accepted that exact geometry.
If a plugin runtime also needs an application-thread resize action, the plugin
must acknowledge that separately before reporting application convergence.
Incident 0003 therefore cannot be hidden by a successful frontend send.

### Stream And Reattachment Semantics

Terminal attachment is distinct from client and kernel lifetime. Losing the
WebSocket does not turn the kernel off and does not by itself destroy the
client, worker, application, PTY, or Neovim buffer.

Every output chunk has an ordered byte-stream cursor and the broker retains a
bounded replay window. Reattachment states the last consumed cursor. Exact
replay is allowed only while that cursor remains available. If continuity is
unavailable, core reports it explicitly; the frontend never silently presents
an incomplete terminal as recovered. A plugin may then perform a full redraw,
replace the surface, or ask the user to restart that client. The final cursor
and authentication fields require protocol design before implementation.

The first slice permits one active input attachment per terminal surface.
Additional observers and attachment takeover are deferred rather than inferred.

### Render-Only Text

All textual/ANSI bytes still use Neovim's native terminal renderer regardless
of whether they came from ordinary code or a plugin. Static and modest
render-only execution output may continue through ordered execution events into
`nvim_open_term()`. The bridge/WebSocket path is selected only by interaction
and sustained-stream requirements, never by cell kind or plugin identity.

## Rejected Alternatives

### Backend-provided attach command and environment

This repeats the 0.x target-rewriting, credential, executable-selection, and
environment ownership problems in the frontend.

### One frontend-local proxy service

This adds a mandatory extra authority and failure boundary for remote kernels.
The per-surface bridge is a byte adapter, not a supervisor or integration point.

### Terminal bytes over the plugin control channel or SSE

This lets high-volume/backpressured presentation delay core operations and
failure events, and makes bidirectional ordering awkward.

### Server-side terminal emulator

This recreates ANSI/screen semantics Jusi deliberately delegates to Neovim and
still requires a separate frontend projection protocol.

### SSH/Docker target logic in the Lua frontend

The service already runs at the kernel target. The frontend connects to its
configured URL; deployment-specific tunneling belongs outside surface identity.

## Required Proof Before Acceptance

- A fixture terminal application must not emit its first draw until the target
  PTY has the Neovim-reported geometry.
- Binary input/output and ANSI bytes must survive unchanged through the bridge.
- Resize must be idempotent and verified at the target PTY.
- A stalled terminal consumer must not delay health, core failures, or kernel
  stop.
- Bridge/WebSocket loss must leave kernel state unchanged and produce explicit
  surface/transport observations.
- Local and remote-loopback tests must use the same surface descriptor and
  bridge behavior; only the configured service URL differs.
- Expired stream continuity must be detected, never guessed around.

## Consequences If Accepted

- The repository gains a small Python bridge executable and a target-side PTY
  broker with a dedicated WebSocket endpoint.
- Surface lifecycle, attach, geometry, stream cursor, and failure fixtures must
  be atomic across schema, Python, Lua, and conformance tests.
- Plugin workers need a typed generic way to request a terminal launch without
  exposing plugin-specific presentation data to frontend core.
- Web surfaces remain a separate later decision; terminal requirements do not
  dictate their transport.
