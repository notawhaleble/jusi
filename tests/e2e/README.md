# End-to-End Tests

The first black-box test does not require Neovim:

1. start the service on an ephemeral loopback port
2. subscribe to SSE and retain the cursor
3. start a real Python kernel
4. execute `1 + 1`
5. observe the ordered result event containing `2`
6. stop the kernel
7. repeat stop and verify idempotent no-op reporting

The test must assert event order, trace correlation, distinct resource identities, terminal `on`/`off` kernel state, and cleanup of every process it created.

The real-kernel text reliability scenario additionally verifies that stdout is
observable over SSE before the execute HTTP request completes, ANSI stdout,
stderr, results, and tracebacks retain their media contract, an ordinary Python
exception does not kill the kernel, and a generated large markdown-like text
surrogate is accepted before a subsequent execution. The surrogate is not the
unrecovered Incident 0001 input and must not be described as its reproduction.

`terminal_surface_spec.lua` explicitly enables the test-only exact plugin under
`tests/fixtures/terminal_plugin`. It proves the complete native Neovim path,
including first-draw geometry, opaque input/output, explicit client close, and
kernel survival. The fixture is never discovered by an ordinary service unless
that directory is deliberately placed on `PYTHONPATH`.

The complementary headless-Neovim test exercises the Lua notebook model,
HTTP/SSE transport, and controller against that real service and kernel without
requiring the interactive UI:

```sh
nvim --headless -u tests/frontend/minimal_init.lua -l tests/e2e/run.lua
```
