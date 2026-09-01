# ADR 0018: Ordinary Text Output Uses Bounded Ordered Events

- Status: accepted
- Date: 2026-09-02

## Context

Ordinary Jupyter stdout, stderr, display text, results, and tracebacks travel in
the ordered SSE event plane. The event log already retains a bounded number of
events, but Jupyter can place an arbitrarily large string in one message. Event
count alone therefore does not bound supervisor memory or one SSE write.

Text and ANSI presentation must remain media-driven and byte-preserving. Jusi
must not parse terminal escape sequences merely to find chunk boundaries.
Interactive or sustained high-volume terminal clients already have a separate
backpressured byte stream under ADR 0016.

## Decision

- The Jupyter adapter emits ordinary textual presentation incrementally as the
  kernel messages arrive.
- It divides each textual message into ordered events whose UTF-8 `data` is at
  most 16 KiB. A boundary never splits a UTF-8 code point.
- Concatenating the `data` fields in event-sequence order reproduces the exact
  original text. ANSI sequences may cross event boundaries; Neovim's terminal
  channel receives the writes in order and remains the ANSI interpreter.
- No newline, truncation marker, or other presentation byte is inserted.
- The existing event-log count bound and the per-event data bound jointly bound
  retained ordinary output. Replay expiry continues to follow ADR 0008:
  transient output may be lost after a cursor gap, and the frontend must not
  invent it.
- This route is for render-only ordinary output. Workloads requiring input,
  terminal geometry, job control, or sustained high-volume streaming use a
  dedicated surface transport instead of expanding SSE into a terminal stream.

## Consequences

- Output becomes visible before a long-running execute HTTP request completes.
- Output observed before timeout, channel failure, or kernel death remains in
  the ordered event chronology.
- One large `print`, result representation, or traceback cannot become one
  unbounded retained event.
- Consumers treat consecutive execution-output records as ordered writes, not
  as independently newline-terminated documents.
- Durable complete output history remains out of scope until a separate output
  resource store is justified.
