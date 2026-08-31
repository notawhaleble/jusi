# Protocol Conformance

Python and Lua must load the same fixtures from `protocol/fixtures/` and reach the same accept/reject result.

Initial conformance coverage will validate:

- identifiers and resource references
- operation and event envelopes
- ordered walking-skeleton events
- structured failures with process diagnostics
- additive unknown fields where permitted
