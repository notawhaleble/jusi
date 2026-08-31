# ADR 0006: Tornado Is The Initial HTTP/SSE Adapter

- Status: accepted
- Date: 2026-08-31

## Context

The walking skeleton needs a small HTTP command plane and replayable SSE endpoint. Tornado is mature, directly supports long-lived streaming responses, is already part of the installed Jupyter environment, and is available without adding an unrelated framework stack.

## Decision

Use Tornado 6 as the initial HTTP server and SSE adapter.

Tornado is confined to `jusi.interfaces` and service startup. Domain models, the supervisor, event log, protocol validation, and Jupyter adapter do not import Tornado.

## Consequences

- The walking skeleton can be exercised as a real process with ordinary HTTP clients.
- SSE does not require a WebSocket abstraction.
- Tornado becomes a declared direct dependency rather than an accidental Jupyter transitive dependency.
- Replacing the web adapter later does not require changing domain or application semantics.
