---
name: jusi-plugin-family-development
description: Design, implement, review, or migrate a Jusi 1.0 plugin family shared by multiple exact providers, including magic registration, target alias resolution, compatible catalog claims, editing defaults, and provider conformance.
---

# Jusi 1.0 plugin family development

Read [references/source.md](references/source.md) first. It identifies the
version-matched Jusi reference checkout installed with this skill. All core paths
below are relative to that checkout. Treat it as read-only reference material;
implement the requested plugin in the user's project. Do not change core or
other repositories unless the user includes that work in scope.

Read the relevant 1.0 contracts and examples as needed. Core `AGENTS.md` rules
apply when editing core, not as a requirement to modify core status files or run
its entire suite for an external plugin. Legacy code is behavior evidence, not
the 1.0 API. Keep the user's plugin/family packaging choice. ADRs live in `docs/1.0/adr/`.

Inspect `protocol/schema/v1/plugin-catalog.schema.json`, the cross-provider
checks in `src/jusi/protocol.py`, and `docs/1.0/architecture/plugins.md` when
designing registration or shared claims.

## Define what the family owns

A family represents one user-facing magic and shared semantics, not a shared
worker or session. Establish the family ID, magic name, alias/configuration
shape, operation payloads/results, error vocabulary, and baseline capabilities.
Keep family and provider independently installable when that is the product's
packaging model. A standalone exact plugin does not need a new family framework
just to satisfy a template.

Write down which behavior all providers promise and which is provider-specific.
Do not make core recognize SQL, database aliases, VisiData sheets, or a universal
`provider` configuration key. Alias interpretation belongs to the family at the
target; different families can use different configuration layouts.

## Discovery and resolution

Exact providers advertise family claims in their `jusi.plugins.v1` catalog
entries. Claims sharing a `family_id` must agree on magic name, capability set,
and family `presentation`. One magic cannot identify incompatible families.
Core rejects an inconsistent catalog as a whole. Do not weaken validation to
silently select whichever distribution was discovered first.

Family `presentation` supplies shared syntax/indent runtime profiles after
kernel startup. `provider_presentation` refines them only after exact-provider
handoff; it is deliberately excluded from shared-family equality. Keep this
fallback/refinement model unless a clean, explicit resolution contract can
supply exact alias metadata earlier. Frontend code must not guess the provider
from arbitrary TOML keys or execute a plugin merely to choose highlighting.

Configuration is loaded at the target for a notebook-runtime generation and
passed into kernel adapters by `configure_jusi_runtime_v1`. Resolve aliases
against that snapshot, reject missing/ambiguous/unavailable providers, and emit
an exact plugin/version handoff. A full restart reloads configuration and
catalogs; ordinary editing does not discover providers or contact the backend.
Avoid leaking connection secrets into public catalog metadata, events or errors.

Plan magic registration when several exact providers coexist. The single-provider
SQLite fixture does not prove that multiple adapters can safely register the
same magic. Verify current adapter attestation rules before designing a shared
registration module: each catalog module must attest the expected provider and
families. A shared dispatcher must have deterministic ownership, not rely on
last-import-wins behavior. If that requires a new generic adapter contract,
record it and update all protocol consumers atomically.

## Provider-facing contract

Define the minimum session operations and ownership in the family package, with
provider adapters implementing their own backend calls. Do not reintroduce the
0.x handler inheritance tree or a process-global registry of live sessions.
Each client and worker belongs to one exact provider in one notebook runtime.

For concrete worker implementation use the `jusi-plugin-development` skill when
available; its source is the sibling `../jusi-plugin-development/SKILL.md`.
The current authoritative Python surface is `src/jusi/plugin_api.py`:
`WorkerContext`, `WorkerResult`, terminal surfaces, recoverable rejection,
interruption, and copy/open content helpers. ADRs 0034 and 0035 define the
concurrent-control and HTTP export boundaries; ADR 0036 adds application-driven
delivery through `src/jusi/editor_client.py`.

Specify completion scopes precisely, including whether the family handles
aliases and the provider handles application identifiers. Current core routes
completion to the kernel before a client exists and to the exact active client
after handoff. It has no separate pre-execution family-completion RPC. Return
Unicode code-point replacement ranges bounded by the original cursor; permit
empty-prefix candidate lists when useful and preserve all suffix text.

For data presentation/export, let the family choose common formats and selection
meaning where that improves consistency. The provider resolves its data; core
receives generic content. Copy/open delivery must work unchanged over HTTP for
local and remote kernels. Applications capture the selection and use the
client-scoped `jusi.editor_client` channel; common UI keybinding hooks can live in
the family while data selection remains provider/application-owned. Use the
application helper for immediate acknowledged delivery, and `plugin_api` helpers
only to construct worker results. Preserve the injected channel environment in
subprocesses. Export snapshots do not establish writeback or publish arbitrary
application work for core interruption.

## Prove coexistence, not just one provider

Use at least two fixture providers when changing family discovery, resolution,
or registration. Cover compatible claims and rejected disagreements, distinct
exact worker routing, missing/unknown aliases, unavailable configured providers,
provider-specific editing refinement, and full restart after config changes.
Check that failure or cleanup of one provider client preserves unrelated clients
and the kernel. Exercise advertised shared operations against each adapter,
including usable sessions after recoverable error and cancellation.

Keep provider dependencies optional to core. Modify external repositories only
when the user has included those migrations in scope. Run the family and provider projects' tests plus targeted real-runtime integration.
Core changes require explicit task scope and core repository instructions. Keep
fixtures explicit about what they prove; do not infer compatibility from one
provider or treat legacy details as invariants.
