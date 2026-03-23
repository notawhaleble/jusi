# Jusi Backend Architecture

## Goal

Jusi is the standalone backend process responsible for kernel lifecycle and notebook execution for Jusivim.

The backend must preserve the useful behavior of the MVP while replacing the old tightly coupled implementation with explicit boundaries and predictable runtime behavior.

## Design Principles

- backend domain logic must not depend on Vim-specific rendering details
- transport details must be isolated behind adapters
- state transitions must be explicit and observable
- process supervision and kernel management must be operational concerns, not spread across business logic
- the protocol must be stable enough to coordinate with `../jusivim` across separate development sessions

## Layering

### Domain

Owns backend concepts and invariants:

- notebook session
- kernel attachment mode
- prepared client
- cell execution state
- lifecycle policies

The domain does not know how messages travel, how Vim stores state, or how Jupyter is called.

### Application

Owns use cases:

- start managed session
- attach to existing kernel
- reconnect to external kernel after linkage loss
- execute cell
- interrupt execution
- stop session
- refresh prepared client

Application services coordinate domain state and infrastructure ports.

### Infrastructure

Implements:

- Jupyter kernel integration
- process supervision
- transport implementations
- logging
- persistence if introduced later

### Interfaces

Owns external contracts:

- request decoding
- event encoding
- protocol versioning
- CLI or daemon entrypoints

## Initial Vertical Slice

The first end-to-end slice should support:

1. frontend sends `start_session`
2. backend starts a managed Python kernel
3. backend emits `session_updated`
4. backend provisions a prepared client
5. backend emits `prepared_updated`
6. frontend sends `execute_cell`
7. backend marks cell busy, consumes prepared client, starts replacement preparation
8. backend emits cell and prepared updates until execution settles

This slice is the reference path for later interrupt, attach, and recovery behavior.
