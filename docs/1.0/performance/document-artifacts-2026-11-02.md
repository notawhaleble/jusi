# Document open measurements — 2026-11-02

macOS arm64, Neovim 0.11.4, Node 24. The supplied PDF contains 119,646,590 bytes
(114.1 MiB). The benchmark reads it without modifying the project and uses an
isolated headless Neovim, real Jusi HTTP delivery, and the real PDF provider worker.
Provider cache/state persistence, OCR and lookahead are disabled for this test.
Each transport starts with a fresh reader process and frontend; each is then
reopened once. These are single-run measurements, not a latency distribution.

| Operation | HTTP fallback | Negotiated shared staging |
| --- | ---: | ---: |
| First open, through provider readiness | 29.634 s | 0.666 s |
| Reopen existing reader | 0.107 s | 0.103 s |
| First-open action content | 160,128,431 bytes | 400 bytes |
| Reopen action content | 200 bytes | 200 bytes |

Action content excludes HTTP framing, acknowledgments and offer replies.
Shared staging also performs a local snapshot copy; it does not eliminate disk
I/O, hashing or reader parsing. An isolated preliminary pass measured 0.063 s for
SHA-256 and 0.577 s for artifact encoding without output I/O or HTTP. Most of the
old transfer path's cost was elsewhere: repeated fetch subprocesses, staging and
decoding. A local open now avoids that transfer, and both transports avoid all
book transfer on reuse.

The benchmark ends at provider readiness. It does not measure Electron's first
visible paint, normal application/VisiData startup, or authenticated assistant
activity. The user's observed desktop delay was approximately ten seconds or
more; the isolated HTTP measurement is not asserted to reproduce that exact
number. No physical remote host was used.

Reproduce from the sibling jusi-read checkout:

```sh
PATH=/opt/homebrew/opt/node@24/bin:$PATH \
JUSI_READ_BENCHMARK_BOOK=/absolute/path/to/book.pdf \
.venv/bin/python -m pytest tests/test_neovim.py -k document_open_benchmark -s -q
```

The benchmark is opt-in and otherwise skipped. Raw session log:
`/tmp/jusi-local-book-benchmark.log` (local, not a release artifact).
