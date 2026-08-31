# ADR 0015: Backend-Owned Plugin Clients Use Generic Frontend Surfaces

- Status: accepted
- Date: 2026-09-01

## Context

Plugin behavior must remain backend-only while still using Neovim capabilities
well. A SQL plugin, for example, owns its database session, transactions,
datasets, VisiData process, completion behavior, and application errors.
Neovim should not receive SQL rows or provider-specific result objects and
reimplement that presentation.

The useful frontend distinction is not ordinary versus plugin output. It is
which generic renderer and control transport a durable client requires.

## Decision

A successful exact-plugin handoff creates a durable backend-owned client. The
client may expose one or more generic frontend surfaces. Jusi 1.0 recognizes two
broad presentation families:

1. a terminal surface, whose bytes are rendered by Neovim's native terminal
2. a web surface, whose backend-owned content is rendered by a supported
   WebBuffer/browser integration

Plugin identity never selects a renderer directly. A versioned surface
descriptor names the surface kind, client identity, transport, and generic
capabilities. The plugin backend computes the content; frontend core creates,
places, resizes, focuses, and closes the corresponding native surface.

### Terminal Surfaces

A render-only terminal surface carries text/ANSI bytes without input, resize,
or PTY ownership. An interactive terminal surface uses a dedicated target-side
PTY/byte transport and may declare input, resize, and signal capabilities. SQL
through VisiData, shell passthrough, and combinations of terminal programs all
use this same frontend renderer; their application semantics remain backend
owned.

### Web Surfaces

A web surface exposes backend-owned web content through a remote-safe endpoint
or stream. Frontend core knows how to open and manage the supported browser
surface but does not interpret the page's application data or JavaScript state.
The endpoint/authentication/proxy contract requires its own transport decision.

### Generic Frontend Actions

Plugins may request only versioned frontend actions that Jusi core supports,
such as opening a buffer/location or presenting a diff. Action arguments and
results are shared protocol data. A plugin cannot invent an implicit frontend
API inside an opaque payload.

### Opaque Content And Control

Opaque data always has a concrete non-core interpreter:

- exact kernel-adapter handoff payload: interpreted by the exact backend worker
- terminal bytes: interpreted by Neovim's terminal emulator
- web content: interpreted by the browser
- plugin runtime state: retained and interpreted inside the durable worker

Core validates identity, bounds, routing, and transport framing without
interpreting plugin application semantics. Frontend-facing resource
descriptors, lifecycle, geometry, input, and generic actions are not opaque;
they are closed shared contracts.

The existing bounded worker control channel remains private backend machinery.
Plugin-specific worker result objects are never forwarded to generic frontend
code. Long-lived terminal bytes, PTY input, resize, and web traffic do not pass
through that request/response control channel.

## Client And Failure Lifetime

Ordinary operation results never decide client lifetime. A client ends only
through explicit close, demonstrated fatal client/worker loss, or teardown of
its owning notebook runtime.

Recoverable application errors belong to the plugin's chosen presentation. A
SQL error may be shown inside VisiData; a shell command may render a non-zero
status in its terminal. If the worker/client remains usable, core does not
reinterpret that content as infrastructure failure.

Fatal factory, worker, control-channel, or required-surface failures always use
the typed core failure channel. Core preserves the originating layer,
operation, trace, resource identity, and available PID/exit/signal/bounded
stderr. Frontend observability therefore survives loss of the plugin's own
presentation. Fatal plugin failure closes only its client/worker unless a wider
causal failure is independently observed; it does not turn the kernel off.

## Consequences

- The proposed per-operation result `disposition` and frontend-facing opaque
  plugin payload are rejected.
- SQL/VisiData, shell, terminal text, and browser-rich plugins remain entirely
  backend implemented.
- The frontend implements a small generic surface/action vocabulary rather
  than exact-plugin adapters.
- Static kernel output may continue using execution media records; durable
  plugin clients are modeled primarily through surfaces and transports, not a
  sequence of result objects.
- Surface resources and their terminal/web transports must be specified before
  a plugin client is exposed end to end.
