# ADR 0023: Kernel Input Is An Execution Reply

- Status: accepted
- Date: 2026-09-07

## Context

A user runs `input('lalala: ')`, sees the prompt in the cell's output split,
edits that same cell body to `ololo`, and submits the body as the reply. This
resembles submitting a followup to a durable plugin client at the interaction
level, but has a different owner and lifetime. Defining kernel input as a
plugin followup would couple two independent protocols prematurely.

## Decision

Kernel input is a request owned by an already-running execution. It does not
create another execution or a durable plugin client, and does not change kernel
state. Each prompt has an opaque `input_request_id`, correlated with its exact
execution, kernel generation, notebook, and cell. Repeated prompts within one
execution receive distinct identities.

The adapter publishes `execution.input_requested`; health exposes the current
request as `pending_input` (null when absent). The prompt and Jupyter password
flag are request metadata. The frontend projects the prompt into the ordinary
cell output split without moving focus. Inspection can recover a pending
request when event replay is unavailable. Replaying or inspecting the same
prompt does not append it twice to an existing projection.

`submit_input` names `kernel_id`, `execution_id`, and `input_request_id`, and
carries a literal string `value`. Its HTTP route is
`POST /v1/kernels/{kernel_id}/executions/{execution_id}/input`. The short control
path validates all identities and accepts at most one reply for that request.
Stale, duplicate, interrupted, and mismatched requests fail with a typed
request-scoped conflict; they cannot answer a subsequent prompt. No automatic
retry retargets input. A lost HTTP response can be resolved through ordered
events or authoritative inspection.

Acceptance queues the reply for the execution thread and publishes
`execution.input_replied` with identities only. It acknowledges acceptance,
not kernel consumption. The execution thread alone reads and writes the
Jupyter channels, including sending the queued `input_reply`; HTTP threads do
not touch the stdin socket. Replies never enter core events, health, or
diagnostics. User code may independently print or return its received value.

Kernel execution and human input waiting have no automatic deadline (ADR 0029).
Interrupt stays concurrent, retires the request when the execution completes,
and leaves an otherwise live kernel
on. Stop/restart fence input waits for their exact owning execution before
joining the serialized teardown lane, including prompts arriving after
teardown queues. Wrong runtime identities cannot cancel a valid input wait.

The explicit frontend command is `JusiInput`: resolve the model cell from
notebook text or projection identity, read its current body, join lines with
newlines, and submit it unchanged. Empty input and leading/trailing whitespace
are valid; no quotes, evaluation, trimming, or final newline are added. The
cell text and current execution artifact remain in place. This plaintext-cell
command is not a hidden password entry surface, even when the kernel's request
carries `password=true`.

The submitting frontend keeps the reply transiently until the ordered
`execution.input_replied` event confirms its exact submission trace. It then
echoes the literal value and a newline on the prompt line, without quotes.
HTTP completion does not insert presentation out of event order. A password
request, or an acceptance from another frontend whose value is unknown, shows
`[input accepted]` instead. Values remain absent from public protocol events,
health, and diagnostics. Genuine kernel expression results remain unchanged
on the following line; matching strings are not treated as duplicate output.

`JusiClose` on a cell with pending input interrupts that exact execution through
the concurrent control path. After acknowledgement it retires the execution's
projection; late output, prompts, or input acknowledgements cannot reopen it,
and a delayed close cannot remove a newer execution's artifact. ADR 0025 extends full close to all active executions and retired openers:
presentation is fenced, cleanup failures are surfaced, and outstanding cleanup
can be retried without rebinding resources. Native window close continues to
hide only.

Input acceptance ends that request's wait; it does not complete its execution.
Future busy/done marks must use authoritative execution completion, so further
code or another prompt keeps the cell busy until successful completion.

There is no generic submission dispatcher or mapping in this slice.
`JusiExecute` still executes, `JusiInput` only replies to pending kernel input,
and future plugin followup commands will target their durable client. A later
context-based mapping may select among those commands without merging their
ownership or protocol semantics.

## Verification

- Shared Python/Lua fixtures cover the command, requested/replied events,
  pending health snapshot, and malformed identities, metadata, and redaction.
- Supervisor tests cover exact identity fencing, consecutive prompts, duplicate
  rejection, one execution lifetime, and reply exclusion from diagnostics.
- Adapter integration advances the adapter clock beyond the former execution
  and input limits, then proves successful reply delivery, explicit interruption,
  and subsequent execution.
- The real Neovim scenario proves the `lalala`/`ololo` workflow, focus, literal
  empty/Unicode/multiline replies, projection-based cell selection, reconnect,
  stale reply rejection, interrupt, stop, restart, and subsequent execution.
