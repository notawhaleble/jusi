# ADR 0038: Target aliases compose start and stop

Status: accepted

## Decision

Frontend `targets` configuration names local launch profiles or remote service
URLs. `JusiStart <alias>` composes service startup (local only), authoritative
inspection/event connection, and kernel startup for the current notebook.
Aliases have command completion. Repeated starts share a pending operation or
inspect an already running target; they do not create another kernel.

Aliases are frontend routing configuration, separate from target-side plugin
configuration. Local services obtain an ephemeral URL from their readiness
record. Remote profiles connect directly to an existing target-side service;
there is no frontend-local service proxy or implicit deployment. A live target
belonging to another notebook is rejected before adopting its state or surfaces.

The resolved profile is fixed for the connected session, including the kernel
name used on restart. Changing aliases or their configuration requires stop
first. This does not change target-side configuration reload on full restart.

`JusiStop` resolves the owning notebook when invoked from an output. It stops
the authoritative kernel and its resources, retires frontend projections, and
stops an owned local service. For an external service it only disconnects after
kernel cleanup. User-authored notebook text survives. An already stopped
notebook is a successful no-op.

Stop cancels a pending local service launch. Once a kernel-start request has
been sent, stop waits for its bounded lifecycle result and then tears down the
resulting runtime. Failed local starts clean up their owned service. Failed
remote kernel-start requests retain their connection for inspection and retry;
uncertain HTTP delivery does not grant authority to kill an external service.
Failed cleanup remains visible and retains the session for retry. Callback
identity checks fence abandoned operations from replacement sessions.

These are composed operations, not new kernel states or wire commands.
Existing explicit service, transport, and kernel commands remain available.

## Verification

`tests/e2e/target_lifecycle_spec.lua` covers cancellation and immediate retry,
duplicate start, target-specific restart, stop from a pending-input projection,
failed-start cleanup, stop during kernel startup, remote ownership conflict,
and disconnected remote stop. Its external service runs on loopback: this proves
ownership semantics, not remote deployment.

`scripts/nvim-dev.lua` enables per-launch manual testing with regular Neovim
configuration, fences the legacy plugin's loading guard, and removes its runtime
lookup paths after package loading. It does not modify user configuration.
