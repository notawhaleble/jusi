# ADR 0012: The Jusi Service Runs At The Kernel Target

- Status: accepted
- Date: 2026-08-31

## Context

HTTP and SSE make the service reachable over a network, but do not imply that a
local service should proxy every remote kernel. A frontend-local gateway would
split one notebook runtime across unrelated environments, obscure process
ownership, and tempt plugin discovery and workers to run on the wrong machine.

The implemented local launcher also caused an easy misunderstanding: it is a
convenience for a local target, not a mandatory frontend-side tier and not a
Neovim-wide singleton.

## Decision

The Jusi service and authoritative supervisor run at the kernel target.

```text
local target:  Neovim -> local Jusi service -> local kernel/workers
remote target: Neovim -> HTTP/SSE -> remote Jusi service -> remote kernel/workers
```

- Neovim talks directly to the target-side service over HTTP/SSE. SSH tunnels,
  container port forwarding, and TLS termination may carry that connection but
  do not create another Jusi supervisor layer.
- `:JusiServiceStart` is only the explicit convenience launcher for a local,
  frontend-owned target service. It automatically connects after readiness.
- `:JusiConnect` connects to an independently running service and never starts a
  local proxy. This is the normal primitive for remote or externally managed
  targets.
- The initial topology remains one service/supervisor per notebook runtime and
  at most one active kernel generation. Multiple notebook buffers or Neovim
  processes may own separate local services. There is no editor-wide or
  OS-session-wide singleton.
- A future multi-runtime supervisor must keep kernel, catalog, discovery,
  client, and worker ownership scoped by `runtime_id`; it cannot introduce one
  process-global plugin catalog.
- Plugin discovery, plugin workers, and interactive plugin processes run at the
  target side and belong to one notebook runtime generation. Kernel-only code
  remains in that runtime's kernel adapter.
- Service readiness is independent of plugin discovery. Discovery occurs while
  constructing a notebook runtime during kernel start or full restart.
- Transport disconnect does not stop an independently managed target-side
  service or its kernel. Remote reconnection inspects that supervisor's
  authoritative identity and resources.

Target placement and durability are independent. A remote service can be
temporary, and a local service can be independently managed and durable.
Cleanup authority follows explicit process ownership, not network location.

## Rejected Topology

Jusi 1.0 does not place a mandatory local service between Neovim and a remote
Jusi backend:

```text
Neovim -> local Jusi proxy -> remote Jusi service -> kernel
```

That topology duplicates identities and failure boundaries without product
value. A separate gateway would require its own future use case and ADR.

## Consequences

- Remote deployment naturally uses the selected HTTP/SSE transport.
- Plugin code cannot accidentally migrate from a remote target into a local
  frontend-side supervisor.
- Target launchers must eventually start or locate the service in the selected
  virtualenv, host, container, or other execution boundary.
- The current `service_command` is intentionally only a local-target command;
  it is not yet a general target launcher abstraction.

