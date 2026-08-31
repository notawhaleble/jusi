# Jusi 1.0 Performance Budgets

These are initial product budgets, not claims about the 0.x implementation. Measurements must record machine, Neovim, Python, kernel, notebook size, and warm/cold conditions.

## Reference Notebook

The baseline large notebook contains:

- 10,000 lines
- 1,000 cells
- mixed ordinary and magic-looking text
- representative execution history text

## Frontend Budgets

- ordinary non-structural edit callback: p95 <= 5 ms, p99 <= 10 ms
- localized structural reparse and reconciliation: p95 <= 16 ms
- localized projection/render update: p95 <= 8 ms
- initial full parse of the reference notebook: p95 <= 100 ms
- no network, backend, client, or whole-notebook work on ordinary typing

Any unavoidable full-buffer recovery must be observable in development metrics and must not become the normal edit path.

## Local Service Budgets

Excluding kernel computation and cold kernel startup:

- accepted HTTP command overhead: p95 <= 25 ms
- backend-observed output to SSE emission: p95 <= 25 ms
- SSE receipt to queued frontend render: p95 <= 25 ms
- stop acknowledgement: p95 <= 50 ms, with cleanup completion reported asynchronously when needed

## Walking-Skeleton Budgets

- warm service start to readiness: <= 1 s
- local Python kernel start to `on`: <= 5 s
- `1 + 1` submission to result event on a warm kernel: <= 500 ms
- kernel stop to authoritative `off`: <= 2 s

These generous initial limits detect regressions and hangs. Tightening them requires measured evidence.

## Required Benchmarks

- typing inside a cell at top, middle, and bottom of the reference notebook
- delimiter insertion/deletion and cell split/merge
- extmark identity preservation through structural edits
- burst output event handling
- 1, 10, and 100 sequential executions on a warm local kernel
- cleanup after normal stop, kernel death, client failure, and plugin-worker death

## Executable Frontend Benchmark

The headless frontend suite runs `tests/frontend/benchmark.lua`. It constructs the 10,000-line/1,000-cell reference notebook and records machine, Neovim, Lua, and warm-sample conditions in its JSON output.

The benchmark currently enforces:

- full parser p95 and initial model attachment against the 100 ms load budget
- top, middle, and bottom body-edit p95/p99 budgets
- split/merge structural-edit p95 budget
- no edit-triggered full parse
- at most 12 scanned lines and two reconciled cells for its localized split/merge workload

Service, event-burst, sequential-execution, and failure-cleanup benchmarks remain deferred beyond the notebook-model slice.
