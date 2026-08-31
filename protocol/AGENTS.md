# Protocol Instructions

## Authority

Files under `protocol/schema/` and accepted fixtures under `protocol/fixtures/` are the language-neutral wire contract.

Prose explains the contract but cannot override schemas and fixtures.

## Change Rule

Every protocol change must update, in one change:

- JSON Schema
- valid and invalid fixtures
- Python conformance tests and implementation
- Lua conformance tests and implementation
- affected end-to-end scenario fixtures

Do not merge a schema-only or implementation-only protocol change.

## Compatibility

- Protocol versioning is independent from the Jusi product version.
- Never reinterpret an existing field or event kind incompatibly.
- Unknown additive fields must be tolerated unless a schema explicitly closes an internal object.
- Event sequence, resource identity, trace identity, layer, operation, and typed failure reason are contract data.
