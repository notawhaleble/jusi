# ADR 0013: Kernel Adapters Attest Before The Kernel Is On

- Status: accepted
- Date: 2026-08-31

## Context

The target-side plugin catalog names opaque kernel-extension modules, but the
service must not import them and the frontend must not infer an exact provider
from a magic header. A kernel can become authoritative only after its installed
adapter code is known to match the runtime catalog generation.

## Decision

After Jupyter readiness but before publishing kernel state `on`, the supervisor
asks the fresh kernel to import every catalog-declared adapter module. A module
exports `jusi_kernel_adapter_v1()` returning its exact `plugin_id`,
`plugin_version`, and family/magic claims. It may expose
`load_ipython_extension(ipython)` to register its magics.

The kernel emits one aggregate
`application/vnd.jusi.adapters-ready.v1+json` attestation. Core validates its
closed envelope and requires an exact match with the immutable catalog. Missing,
duplicate, malformed, or mismatched modules fail kernel startup; the kernel
remains `off` and is cleaned up with bounded process diagnostics.

During execution, an adapter may emit at most one
`application/vnd.jusi.handoff.v1+json` record naming exact plugin version,
family, magic, and an opaque object payload. The Jupyter adapter removes this
control record from presentation output. The supervisor validates it against
the current runtime catalog before any worker can be selected. The kernel does
not know or invent backend execution, client, or worker identities. Adapter
attestations and handoffs are limited to 1 MiB of encoded JSON each.

Kernel adapters and handoffs are reliability contracts, not a security
sandbox. User code already executes with the kernel's authority.

## Consequences

- Full restart imports adapter code in a new kernel with no surviving module cache.
- Kernel state `on` means catalog-declared adapter identity was observed, not assumed.
- Ordinary kernels with an empty adapter set require no bootstrap execution.
- Plugin worker activation remains deferred until supervisor locking permits
  worker I/O without holding the global state lock.
