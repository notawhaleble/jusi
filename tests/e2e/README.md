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
