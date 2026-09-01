# ADR 0017: Web Clients Use Service-Relative Surfaces And A Renderer Adapter

- Status: proposed
- Date: 2026-09-01

## Context

ADR 0015 identifies web content as the second broad plugin presentation family.
An exact backend plugin may own a durable application whose useful presentation
requires HTML, JavaScript, forms, canvas, or other browser behavior. Neovim core
must place and focus that presentation without interpreting plugin application
data.

The 0.x frontend implemented `open_url` as a handler action. It created a plain
editor buffer and invoked an optional `:JusiWebBuffer {url}` command. That
proved useful placement behavior, including replacing a client view in its
window, opening in a tab, keeping editor-local buffer identity out of backend
state, and returning to the originating client when a temporary web view
closed. It also accumulated aliases, backend-driven layout hints, and no durable
web resource or failure identity.

The separate `jusi-neovim` Electron frontend provides stronger historical
evidence. A web buffer has distinct Neovim buffer, live browser-session, and
visible-window identities. Browser state survives hiding and moves between
windows without pretending that the window owns the session. URL/title changes,
browser-process death, and mirror-text convergence remain incomplete there and
must not be guessed by Jusi.

For a remote kernel target, a plugin-owned loopback URL is not reachable from
the user's browser. Publishing that URL would also leak backend topology and
make plugins invent SSH, Docker, and host-rewriting behavior again.

## Proposed Decision

A durable browser-backed plugin presentation is a Jusi `web` surface. It is not
an `open_url` action and is not selected from plugin identity. The public
surface descriptor contains only core identities, generic browser capabilities,
and a relative endpoint under the authoritative target service:

```text
/v1/surfaces/{surface_id}/web/
```

The frontend resolves that endpoint against the same configured service origin
used for HTTP/SSE. Local and remote targets therefore have identical surface
descriptors. Events never contain the plugin's target-local upstream address,
credentials, cookies, authorization headers, Electron identifiers, Neovim
buffer numbers, or window placement instructions.

### Target-Side Content Boundary

The exact worker owns the web application, application sessions, routes, HTML,
JavaScript, and application errors. Its private `web_surface.create` request
identifies one already-ready HTTP upstream bound to target loopback. Core
validates that the upstream is loopback-only, verifies reachability before
publishing the surface, and retains the upstream reference only in target-side
private state.

The Jusi service reverse-proxies the relative public endpoint to that upstream.
It strips the public surface prefix, filters hop-by-hop headers, preserves
streaming response bodies, provides an explicit forwarded prefix, and scopes or
rewrites cookies so one surface cannot accidentally share another surface's
path. Redirect, origin, CSP, WebSocket, upload, and response-size behavior must
be closed by conformance tests before each capability is advertised. The first
slice may support ordinary HTTP navigation only; it must reject undeclared
WebSockets rather than partially proxy them.

Core does not sanitize or parse application HTML. This is reliability and
routing isolation, not a security sandbox: installed plugins already execute as
the user. Binding the private upstream to loopback prevents accidentally
turning the public surface route into a configurable open proxy.

### Frontend Renderer Boundary

Lua owns a frontend-local projection record distinct from the backend surface:

- one Neovim buffer identity
- zero or one live browser-session identity owned by a renderer adapter
- zero or one currently visible Neovim window identity

Jusi calls a registered generic web-surface adapter with the prepared buffer,
resolved service URL, and closed surface descriptor. The initial compatibility
adapter may invoke `:JusiWebBuffer` when that command exists, but the shared
protocol never names Electron or that command. A future renderer can register
the same Lua adapter contract.

Hiding or moving the buffer does not close the backend client. Closing the web
projection through Jusi explicitly closes the owning client; backend
`surface.closed` destroys the browser projection. Renderer navigation, title,
and crash observations are frontend-local facts until a closed generic feedback
contract exists.

If no renderer is registered, Lua emits a typed
`frontend_presentation/open_web_surface/unsupported` failure and explicitly
closes the unusable client. It does not display raw HTML in a text buffer or
leave an invisible required client running.

### Authentication

The browser URL uses the configured service origin and never embeds a bearer
token or per-surface secret in query parameters or replayable events. A remote
deployment supports web surfaces only when its authentication can also be used
by the registered browser adapter, for example through same-origin cookies or
an explicit adapter-owned header mechanism. Jusi must fail capability setup
when browser authentication is unavailable; it must not introduce a mandatory
frontend-local proxy implicitly.

### External Links And Generic Actions

`open_url` remains a separate, one-shot generic frontend action for an external
link. It may open a system browser or a temporary web buffer according to user
policy, but it does not create a durable client surface, proxy a private
upstream, or transfer client ownership. Legacy aliases and backend-selected
layout strings are not protocol commitments.

### Failure Semantics

An upstream HTTP application response, including a 4xx or 5xx page, is plugin
content and does not by itself kill the client. Failure to connect to the
required upstream, upstream process loss, or proxy protocol corruption uses the
core failure channel. Browser renderer death initially produces a typed local
frontend failure followed by explicit client close; it must not be mislabeled
as backend death before a closed renderer-feedback command exists. Either fatal
path retires only that client and worker and leaves the kernel on.

## Rejected Alternatives

### Publish the plugin's URL directly

Target loopback is unreachable for remote clients, and rewriting it in Lua
recreates the legacy target-topology problem.

### Put HTML or a plugin payload on SSE

This couples potentially large application content and browser backpressure to
the ordered core event stream and asks frontend core to invent application
semantics.

### Make every link a durable web surface

External navigation is an action. Conflating it with client lifetime would
create backend resources for ordinary documentation links and browser tabs.

### Require Electron in the shared protocol

Electron is one renderer host. The protocol boundary is a web surface and its
generic capabilities, not a particular browser process or Neovim UI.

### Mandatory local reverse proxy

The authoritative service already runs at the target and is the natural remote
boundary. A local proxy may be an explicit authentication adapter later, but it
must not become a hidden second supervisor or default authority.

## Required Proof Before Acceptance

- The same relative descriptor works for a local service and a remotely
  forwarded service URL.
- No private upstream URL or credential appears in health, events, Lua state,
  or renderer-visible buffer metadata.
- Two web surfaces cannot cross-route paths, cookies, or lifecycle cleanup.
- Large/streaming content does not block health, SSE failures, or kernel stop.
- Renderer absence closes only the unusable client with a typed local failure.
- Upstream connection loss produces a typed core failure with the kernel still
  `on`; an application-rendered HTTP error does not.
- Browser buffer, browser session, backend surface, client, worker, and window
  identities remain distinct in tests.
- Authentication failure is explicit and does not fall back to URL tokens.

## Consequences If Accepted

- Protocol v1 gains a closed `web` surface descriptor and private
  `web_surface.create` worker request atomically across schema, Python, Lua,
  fixtures, and tests.
- The service gains a narrowly scoped target-loopback reverse proxy owned by
  surface lifecycle, not a general plugin or frontend proxy.
- Lua gains a generic renderer registration point and an optional
  `JusiWebBuffer` compatibility adapter.
- WebSocket applications, mirror text, generic browser feedback, and external
  `open_url` actions remain separate incremental decisions.
