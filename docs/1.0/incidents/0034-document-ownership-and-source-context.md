# Incident 0034 — Document ownership and source capture

Review of the unpublished document actions reproduced four failures. Independent
reuse could bind to a client-owned view and disappear when that client closed.
URL replacement could delete a borrowed reader with the same slot name. A second
palette selection from a plain buffer retained the previous reader's capture.
Changing a file between the identity offer and HTTP upload delivered new bytes
under the earlier digest.

Review also found that capture expiry discarded a waiting callback while its
delivery lease kept renewing, and that an initial execute merged a core field
into the provider's private handoff dictionary.

[ADR 0054](../adr/0054-document-artifacts-and-source-context.md) records the
ownership and worker API decisions. Frontend regression coverage lives in
`runtime_spec.lua`, `managed_urls_spec.lua` and `source_context_spec.lua`.
`test_document_offers.py` exercises content changes during delivery.
`test_supervisor.py` and `test_plugin_worker.py` cover the worker envelope and
compatibility through a fresh worker process.
