# Python Service Instructions

## Ownership

The Python package owns the HTTP control plane, authoritative resource state, kernel supervision, execution routing, event sequencing, failure capture, and plugin-worker supervision.

It does not own Neovim buffers, extmarks, presentation layout, or frontend-local cell coordinates.

## Boundaries

- Domain and application code must not import the web framework, Jupyter client, or Neovim concepts.
- Adapters report observations; the supervisor decides authoritative resource state.
- Kernel death always yields kernel state `off`.
- A plugin or execution failure must not poison the supervisor or unrelated clients.
- Child process stderr, exit code, signal, operation, layer, reason, and trace ID must survive normalization.
- Cleanup and stop are idempotent and report which resources were actually stopped.
- Terminal output bytes remain opaque to the service; do not parse ANSI escape sequences.

## Verification

Python changes require the relevant unit tests, protocol conformance tests, and—when process or Jupyter behavior changes—integration tests with explicit timeouts.
