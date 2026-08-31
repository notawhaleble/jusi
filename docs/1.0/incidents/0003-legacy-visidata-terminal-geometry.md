# Incident 0003: Legacy VisiData Terminal Geometry Did Not Converge

- Status: preserved regression evidence; root cause not proven
- Observed in: Jusi/Jusivim 0.x on Linux with VisiData 3.3 and 3.4
- Source: `../jusivim/.local/cross-repo-sync.md` and the audited 0.x terminal path

## Symptom

After a Jusi-owned VisiData terminal was resized, its display continued drawing
against stale curses dimensions. The visible status line appeared at the wrong
row and subsequent drawing became inconsistent.

## Confirmed Observations

- Neovim resized its local terminal job.
- The target VisiData process could observe the new PTY dimensions through its
  terminal file descriptor.
- Sending an additional `SIGWINCH` did not repair the curses display.
- The frontend emitted a `terminal_resize` handler message.
- The 0.x backend had no end-to-end consumer that applied that message to the
  VisiData runtime. It could return apparent success without changing the
  application.
- The Jusi VisiData runner changed VisiData from its normal eventual blocking
  input behavior to permanent timed polling.
- Standalone `vd .` resized correctly on the same machine.

These observations disprove the broad explanations that Neovim never resized
the PTY or that those VisiData versions could not resize at all. They do not
identify the exact cause inside the Jusi-controlled runtime.

## Unproven Hypothesis

Permanent polling and the plugin-runtime control path may have prevented or
delayed the normal curses `KEY_RESIZE`/`resizeterm()` sequence. This remains a
hypothesis, not an accepted cause.

## 1.0 Requirements

- Initial geometry must be acknowledged at the target PTY/application boundary
  before a full-screen application performs its first draw.
- Runtime resize success must mean the target PTY accepted the dimensions and,
  when an application-specific resize action is required, the owning plugin
  applied it on the application's correct thread.
- A frontend send or HTTP/control acknowledgement alone is not success.
- Repeated identical geometry is idempotent.
- A disappearing surface/client during resize produces a typed scoped failure,
  not apparent success and not kernel failure.
- A Linux regression test should eventually assert convergence among requested
  geometry, target PTY geometry, application screen geometry, and visible
  status-line placement.

## Legacy Complexity Not To Copy

The 0.x frontend received backend-provided `attach_cmd` and `attach_env`, then
rewrote them separately for local, virtualenv, SSH, Docker, and SSH-plus-Docker
targets before launching a terminal job. This mixed frontend target policy,
backend process details, credentials/environment handling, and surface
attachment. It is evidence for a stable transport endpoint, not a design to
port.
