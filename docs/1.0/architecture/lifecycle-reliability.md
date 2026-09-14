# Lifecycle reliability checks

`tests/e2e/test_process_lifecycle.py` starts isolated headless editors, services,
managed kernels and fixture clients. It captures exact process identities and
cleans up only those resources. All test waits are bounded. Run with:

```sh
.venv/bin/python -m pytest -q tests/e2e/test_process_lifecycle.py
```

Each lifecycle case runs idle, during a long execution, at `input()`, with a
terminal client, during a pending plugin followup, and while kernel code ignores
SIGINT. The long work deliberately has no application deadline.

| Case | Required observation |
| --- | --- |
| Neovim `:qa!`, owned target | Service, kernel, fixture workers, terminal and local transport processes exit. |
| Neovim SIGKILL, owned target | Owner-pipe EOF produces the same cleanup without editor exit callbacks. |
| Service SIGTERM | Normal bounded teardown; frontend disconnects. |
| Service SIGKILL | Managed kernel and fixture descendants retire after owner loss; frontend disconnects. No final authoritative event is assumed. |
| Kernel SIGKILL | Service survives; frontend receives typed kernel-death failure and `off`; clients/surfaces/active work retire. New kernel identity executes successfully. |
| Neovim `:qa!` or SIGKILL, external target | Local transport helpers exit. Target service, kernel, runtime and active work retain their identities. Explicit API stop still succeeds. |
| Loopback tunnel drops connections | Frontend marks transport disconnected, retaining kernel `on` as last-known. Target work can finish; reconnect restores truth and replays completion once. |
| Loopback tunnel silently discards bytes | SSE inactivity detection gives the same disconnected/reconnect behavior without imposing a code/input timeout. |

The external-target tests use a separately started local service, with no owner
pipe. The loss tests use a disposable TCP relay in front of that service. They
exercise remote transport semantics without requiring another machine or SSH.

## Later manual remote check

Start a remote target and a long-running cell, then break its tunnel/network
path. Notebook editing should remain responsive. The statusline must say that
the kernel state is last-known, rather than declaring it off. Restore the path
and use `JusiConnect` to reconnect the existing session. It must reconcile the
same target runtime and show any completed output without running the cell
again. Review interactive terminal reattachment separately as well.

This remote-machine review is deferred. Automatic tests do not claim fresh-editor
session restoration, durable events across service replacement, arbitrary
detached subprocess cleanup, or behavior during host suspension/power loss.
