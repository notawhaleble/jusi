# Incident 0001: Bulky Markdown-Like Cell Lost The Kernel

- Status: open; cause unknown
- Source: representative user report preceding the 1.0 rewrite

## Observation

Executing a bulky markdown-like cell body repeatedly caused the kernel to die, disappear, or otherwise become unavailable. The product did not identify which process failed or why.

## Known Facts

- The failure reproduced on each attempt with the reported body.
- The user did not receive actionable kernel, supervisor, plugin, or transport diagnostics.
- No minimized fixture or reliable process chronology has yet been captured.

## Unknowns

- which process exited first
- whether a signal, resource limit, protocol error, plugin path, transport failure, or kernel exception was involved
- whether payload content, payload size, rendering, or unrelated state was necessary

No cause may be assigned until evidence supports it.

## Required Capture For Reproduction

- exact but safely stored input and minimized variants
- supervisor, kernel, execution, client, plugin-worker, and transport IDs
- trace ID and ordered event chronology
- process PID/parent, exit code, signal, and bounded stderr
- kernel channel observations and last successful event
- payload byte size and relevant media metadata

## Regression Exit Criterion

A minimized fixture must either execute successfully or fail with a structured, correctly scoped explanation that identifies the originating layer and preserves process diagnostics.
