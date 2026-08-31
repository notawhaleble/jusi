# Test Instructions

## Suite Boundaries

- `backend/unit/`: hermetic domain and application behavior; no subprocesses or network.
- `backend/integration/`: Jupyter, process, HTTP, and SSE adapters with explicit timeouts.
- `frontend/`: headless Neovim Lua model, parsing, extmark, and rendering behavior.
- `conformance/`: identical shared protocol fixtures consumed by Python and Lua.
- `e2e/`: black-box service and frontend workflows.

Automatic discovery is required. A test that is not discovered is not coverage.

## Reliability

- Every process, socket, event wait, and kernel operation has a bounded timeout.
- Tests own and clean up only resources they create.
- No test may depend on a user's editor, credentials, home configuration, or an implicit `EDITOR`.
- Assert event order and identities, not incidental timing sleeps.
- Real-kernel tests are marked and runnable separately from the fast suite.

## Intended Commands

These become mandatory as their suites land:

```sh
.venv/bin/python -m pytest tests/backend tests/conformance tests/e2e
nvim --headless -u tests/frontend/minimal_init.lua -l tests/frontend/run.lua
nvim --headless -u tests/frontend/minimal_init.lua -l tests/e2e/run.lua
```
