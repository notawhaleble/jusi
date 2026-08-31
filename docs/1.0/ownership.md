# Jusi 1.0 Component Ownership

## Core Service

Owns:

- HTTP/SSE contract implementation
- supervisor and resource identity
- kernel lifecycle and truth
- execution routing and event ordering
- structured failures and trace correlation
- idempotent cleanup
- generic plugin discovery and isolation contracts
- fail-closed publication of validated plugin catalog snapshots
- notebook-runtime generations that bind catalog, kernel, and frontend notebook identities

Does not own:

- notebook text coordinates
- Neovim buffers, windows, extmarks, signs, or renderer layout
- plugin-family semantics
- ANSI interpretation

## Neovim Frontend

Owns:

- notebook text model and stable cell identity
- localized parsing and reconciliation
- extmark anchoring
- mappings, commands, rendering, terminal surfaces, focus, and layout
- frontend transport connection and event-cursor persistence

Does not own:

- kernel liveness or ownership truth
- backend process state
- plugin-worker health
- server cleanup decisions

## Plugin Family

A family such as SQL, shell, or VisiData owns:

- family configuration schema and discovery
- shared presentation metadata
- handoff validation beyond core envelope validation
- family-level completion/follow-up semantics
- common worker adapter where applicable
- family claim/conflict rules and versioned catalog data; no authority over core resource state

## Exact Plugin

An exact provider/plugin owns:

- provider-specific kernel integration
- concrete runtime behavior
- provider-specific config and credentials
- exact presentation overrides
- minimized provider regression fixtures
- exact worker and kernel-adapter entry points, imported only in isolated worker/discovery or kernel processes

## Shared Protocol

Owns cross-language shapes only. It must not encode Neovim buffer identifiers, Python object topology, framework request objects, or plugin-specific opaque payload internals beyond their declared envelope.

## Verification Matrix

| Change | Required verification |
|---|---|
| notebook model | frontend unit + performance benchmark |
| Python domain | backend unit |
| Jupyter/process adapter | backend integration with timeout |
| protocol shape | schema + Python/Lua conformance + fixture |
| user workflow | end-to-end scenario |
| plugin worker | isolation + cleanup + exact plugin tests |
