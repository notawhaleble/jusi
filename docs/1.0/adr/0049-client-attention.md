# ADR 0049: Client-owned attention

Status: accepted

## Decision

A plugin terminal application declaring the `attention` capability can publish
`action_required` or `notice` items through its private, client-bound application
socket. This extends the existing application channel without requiring the
`editor_actions` capability. Attention messages carry short summaries, not
approval controls, answers, credentials, or arbitrary presentation content.
The plugin keeps all question/permission UI and decision semantics.

`jusi.editor_client.request_attention(kind=..., message=...)` returns an opaque
attention ID after target-side acceptance. `update_attention` changes an existing
item; `clear_attention` resolves or withdraws it. These calls have a bounded
five-second transport wait and never wait for the ordinary worker operation,
editor focus, or human response. They work with a disconnected editor. Uncertain
requests are not automatically retried. An update cannot recreate a cleared ID.

The service owns pending attention within the exact client/runtime/notebook/cell.
It binds the recipient to the client's exclusive terminal editor and retains
pending state across transport reconnects. Recipient replacement changes the
recipient and revision; client/runtime cleanup clears its items. Attention is
not kernel state, execution outcome, or a failure. No backend-global plugin
catalog or frontend-local service is introduced.

State is bounded to 32 pending items per client and 256 per runtime, with messages
of 1–240 Unicode characters excluding control characters. Each change has a
monotonically increasing item revision. Ordered `client.attention_changed` events
carry closed records; health includes the pending records. Health's event cursor
and application-channel snapshot share the channel lock. Old peers default a
missing health field to an empty list; attention-capable peers require the new
catalog capability/event contracts. Frontend and backend must be upgraded together.

A notice can be dismissed through an exact-recipient, exact-revision HTTP
command. Action-required items cannot be dismissed by the frontend: visiting,
receiving, or ringing is never consent. Only the originating plugin can resolve
or withdraw such an item. Repeated clear/dismiss after removal is harmless.

## Frontend behavior

Jusi statuslines show an attention count on the notebook and owning client.
New items produce one short message without changing windows/tabs or input mode.
Updates/replays do not repeat notifications. Bursts are grouped for 100 ms and
bells are limited to one per two seconds across the editor. Pending items remain
inspectable even when a toast or bell is missed. Closing a projection does not
clear attention; ending its backend client does.

While pending attention has a visible location, the native tabline is temporarily
replaced with clickable labels and shown even for one tab. Owning labels have
an amber background for action-required items, blue for notices, and a count;
action-required takes precedence. Exact visible client views determine tabs;
notebook views are fallback only for hidden clients. This persistent projection
survives status-message traffic and clears only with its underlying attention.
Native tabline/visibility settings are restored afterward. Custom tablines are
never replaced; a cached summary API and `User JusiAttentionChanged` event allow
integration. `attention.tabline = false` disables the native enhancement.

`:JusiAttention` visits the sole pending client or offers a client picker. It
prefers a client view in the current tab, then another visible view, or explicitly
reopens a hidden projection in the current tab. Visiting a client dismisses its
notices only. `:JusiAttention!` dismisses notices without visiting; it cannot
clear requests for action. Merely being visible when a notice arrives does not
count as visiting it. A later explicit visit/focus gain does.

`FocusGained`/`FocusLost` select the external alert policy. Known focus suppresses
bells; unfocused or unknown focus requests a best-effort BEL on the editor's
controlling terminal. No synthetic keys, mode changes, or plugin-terminal escape
injection implement the bell. Headless sessions do not ring. Terminal/emulator
settings control whether BEL is audible or visible; reliable OS notifications
are not promised by the portable default. `attention.bell` accepts false, true,
or a local function, and `attention.notify` accepts an optional local callback
receiving the coalesced records and focus context. No external command is run by
default. These hooks belong to the Neovim host even for remote kernels.

## Verification

Shared Python/Lua fixtures and JSON Schema cover requests, records, events,
health ownership, revision bounds and notice dismissal. Manager/channel tests
cover disconnected acceptance, isolation, bounds, updates, cleanup and bounded
helper calls. Frontend tests cover focus/tab behavior, duplicate suppression,
updates, notices versus approval, stale replay, persistent tab highlights,
mirrored notebook attribution, hidden-client fallback, and custom tabline
ownership/restoration. A real terminal fixture
publishes attention during blocked worker work, survives reconnect, clears a
request from the application, and dismisses a notice by visiting the client.
