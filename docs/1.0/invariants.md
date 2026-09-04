# Jusi 1.0 Product Invariants

These invariants constrain implementation and protocol design. A change that violates one requires an explicit replacement decision and ADR.

## Kernel And Supervisor

1. User-visible kernel state is `off` or `on`; `checking` is reserved for remote verification.
2. Starting, stopping, interrupting, executing, cleaning up, and connecting transport are operations, not kernel states.
3. A kernel is `on` only after the authoritative supervisor has observed readiness.
4. A dead, exited, killed, unreachable-owned, or cleaned-up kernel is `off`.
5. Kernel liveness is never guessed by the frontend.
6. A new kernel process receives a new `kernel_id`, even when started for the same notebook.
7. Frontend transport reconnection never claims to revive a kernel.

## Identity And Lifetime

8. Supervisor, plugin discovery, kernel, execution, client, plugin worker, cell, and frontend transport have distinct identities and lifetimes.
9. Cell identity is owned by the notebook model and is independent of line coordinates, extmark IDs, signs, buffers, and backend resources.
10. Backend resource identifiers are opaque to the frontend and are never reconstructed from local coordinates.
11. Events from an obsolete resource generation cannot mutate the current generation.

## Failure And Containment

12. Every surfaced failure identifies its originating layer, operation, typed reason, trace ID, and affected resource or scope.
13. Process failures preserve available PID, exit code, signal, and bounded stderr information.
14. An execution failure affects its execution and owning cell/client unless evidence requires a wider scope.
15. A plugin-worker failure affects that worker and its owning client/execution; it does not by itself turn the kernel or supervisor off.
16. Protocol or frontend-transport failure does not imply kernel failure.
17. Failure propagation widens scope only through an explicit causal relationship recorded in diagnostics.

## Cleanup

18. Stop and cleanup operations are idempotent.
19. Cleanup reports what was stopped, what was already absent, and what could not be stopped.
20. A stopped resource cannot remain authoritative through stale cached metadata or transport environment.
21. The supervisor cleans up only resources it owns or has an explicit lease to manage.

## Protocol And Events

22. Commands use HTTP and backend-to-frontend lifecycle events use an ordered event stream unless an ADR replaces this baseline.
23. Event sequence gaps are detected and recovered through authoritative inspection or replay; the frontend does not invent missing state.
24. A protocol change is atomic across schema, Python, Lua, fixtures, and conformance tests.
25. Product version and protocol version are independent.

## Presentation

26. Renderer selection depends on declared media type and interaction requirements, never on cell kind or plugin identity.
27. Textual and ANSI-bearing output is passed intact to Neovim's terminal renderer; Jusi does not implement an ANSI escape parser.
28. Terminal presentation does not imply terminal ownership of execution or kernel lifecycle.
29. A PTY or dedicated bidirectional stream is reserved for clients that actually need terminal input, resize, job control, or sustained high volume.

## Frontend And Performance

30. Persistent cells use the symmetric, exact-line, symbol-only boundaries defined by the notebook format.
31. Text is the durable, reconstructable authority for notebook structure; the in-memory model is the operational authority during editing.
32. Syntax, extmarks, signs, highlights, and client buffers are projections and do not define cell semantics.
33. Every cell opener is a structural recovery point, so one malformed cell does not consume or invalidate the rest of the notebook.
34. Ordinary typing performs no backend, supervisor, session, client, or whole-notebook work.
35. Non-structural edits reparse and rerender only a bounded affected region.
36. Deleted runtime bindings do not resurrect through undo, duplicate text, stale extmarks, or late backend events.
37. Backend unavailability cannot make plain-text notebook editing unusable.

## Full Restart

38. User-visible restart replaces the complete notebook runtime; it is never an in-place Jupyter kernel restart.
39. Restart preserves current user-authored buffer text and undo history, while discarding backend-derived and runtime-derived notebook state.
40. Restart reloads configuration from canonical sources and rediscovers plugins, capabilities, palettes, and kernel extensions without reusing import or discovery caches.
41. Restart creates new kernel, execution, client, plugin-worker, and runtime cell identities.
42. Events and callbacks from every replaced identity are ignored after restart.
43. If teardown succeeds but replacement startup fails, the kernel is `off`; stale runtime state is not restored as a fallback.

## Target Placement

44. The authoritative Jusi service and supervisor run at the kernel target; a remote kernel is not controlled through a mandatory frontend-local Jusi proxy.
45. `JusiServiceStart` is a local-target convenience operation, while transport connection to an existing local or remote service is a distinct operation.
46. Plugin catalogs, discovery attempts, workers, clients, and kernel adapters belong to one notebook runtime at that target; they are never editor-wide or OS-session-wide globals.
47. Network location does not determine durability or cleanup authority; explicit ownership does.
48. Service readiness does not depend on successful plugin discovery or kernel startup.
49. Any future supervisor that owns multiple runtimes must preserve per-runtime plugin and resource isolation.

## Plugin Clients

50. A successful exact-plugin handoff creates a durable client; ordinary execute, follow-up, completion, or action results never decide its lifetime.
51. A plugin client ends only through explicit close, demonstrated fatal client/worker loss, or cleanup of its owning notebook runtime.
52. An operation-level plugin error does not close an otherwise usable client or widen failure to the kernel.
53. Plugin application semantics and presentation content remain backend-owned; frontend core supports only versioned generic terminal/web surfaces, controls, and actions.
54. Recoverable plugin application errors use plugin-owned presentation, while fatal factory, worker, channel, or required-surface failures always produce a typed core failure event.

## Cell Artifact Interaction

55. Ordinary execution output and plugin clients share one cell-oriented interaction model without erasing their distinct internal identities or cleanup paths.
56. Execute reveals the current cell artifact without moving frontend focus; focus changes only through an explicit focus or editor action.
57. Native window close hides a projection and does not end its execution artifact or backend client.
58. `JusiClose` explicitly ends the current cell artifact and performs type-appropriate, idempotent cleanup while leaving an otherwise live kernel on.
59. The initial frontend retains at most one current execution artifact per model cell; a new cell execution explicitly replaces any prior artifact.
