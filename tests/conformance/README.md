# Protocol Conformance

Python and Lua must load the same fixtures from `protocol/fixtures/` and reach the same accept/reject result.

Initial conformance coverage validates:

- identifiers and resource references
- operation and event envelopes
- ordered walking-skeleton events
- structured failures with process diagnostics
- additive unknown fields where permitted

Event payload objects are closed even though additive top-level event fields remain permitted. Runtime events emitted by the supervisor are also passed through the Python validator, while the shared all-kind fixture is consumed by both Python and Lua.
