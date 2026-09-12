# ADR 0035: Editor Actions Transfer Content Over HTTP

Total data-size limits described below are superseded by
[ADR 0039](0039-user-data-is-not-a-control-frame.md).

- Status: accepted
- Date: 2026-09-09
- Amended: user-facing editor-invoked commands withdrawn; application-driven delivery is the intended workflow.

## Decision

An existing client declaring `editor_actions` accepts
`POST /v1/clients/{client_id}/editor-actions`. The command carries `action`
(`copy` or `open`) and an opaque object `selection`. The plugin owns selection
meaning and serialization; core does not interpret rows, cells, queries, or
VisiData objects. Busy runtimes reject the request instead of queueing a selection
behind unrelated work.

The worker handles `editor_action` and returns one of:

```json
{"action":"copy","text":"literal text","regtype":"v"}
```

```json
{"action":"open","text":"a,b\n1,2\n","name":"selection.csv","filetype":"csv"}
```

`copy_text()` and `open_text()` construct validated `WorkerResult` values.
The HTTP response's `editor_action` field contains the complete content. Neither
health nor ordered events contain that content. This uses the same HTTP path for
local kernels and kernels inside remote hosts/containers; no frontend-local
service, shared filesystem, SCP, or host-side staging is required.

Text is UTF-8, excludes NUL, and is bounded to 512 KiB of UTF-8 bytes. The private
worker frame also has its independent 1 MiB encoded limit; JSON escaping can
reach that limit sooner and is a recoverable export rejection. Streaming large
artifacts and binary exports are future extensions. Copy supports characterwise
`v` and linewise `V`. Open's name is a basename hint, never a target path;
filetype is a validated Neovim runtime profile identifier.

Neovim validates content and exact source identity before changing a destination.
The retained Lua copy helper defaults to the unnamed and yank registers; an
explicit register affects only that destination. The open helper focuses a new listed,
modifiable, unsaved local buffer with a unique temporary filename derived from
the hint. No file is written during delivery; `:write /chosen/path` is ordinary
local editing. Final newline and empty content survive, and modeline processing
is disabled for the export. Closing its source cell/client, disconnecting, or
restarting the kernel does not own or delete this independent buffer.

HTTP failures, malformed exports, and late replies to retired/replaced source
identities leave the destination untouched. A healthy worker can report missing
selection or serialization failure with `OperationRejected` and stay usable.
The frontend destination register and window remain frontend-owned.

## Boundary

`JusiCopy` and `JusiOpen` are removed from the public command surface at the
user's request. The request API and local delivery helpers remain available for
reuse and fixture coverage. Lua callers can pass plugin-owned `selection` options through
`require('jusi').editor_action(action, opts)`. This is an export snapshot, without
writeback. Actions initiated solely by keys inside a target application still
need an explicit frontend recipient/delivery contract; terminal escape sequences
or remote Neovim commands are not substitutes. That extension, large artifacts,
and editing data back into a plugin are not claimed by this slice.

## Verification

Shared Python/Lua fixtures cover literal Unicode, paths, NUL, action identity,
and malformed commands. Frontend tests cover empty content, whitespace, CR/LF,
final newlines, register types, and mutation-free rejection. The terminal fixture
proves HTTP copy/open, recoverable selection failure, and exported-buffer survival
after source close. Tests use loopback HTTP with a separate kernel/worker; a
remote-machine deployment test remains part of remote workflow verification.

Application-driven delivery is now implemented by
[ADR 0036](0036-application-driven-editor-actions.md); it extends this content
contract with recipient binding, an application channel and acknowledgments.
