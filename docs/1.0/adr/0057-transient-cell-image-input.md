# Single-use cell attachments

The frontend holds ordered files under the live cell identity and displays names
as virtual text. At submission it snapshots the queued entries. A successful
execute or followup operation consumes those entries. Failure, cancellation or
a missing acknowledgement leaves them available. An identical file attached
again is a new entry. For asynchronous providers, success means Jusi accepted
the handoff. Model generation can complete later.

File types use capability strings such as `input:image/png` or `input:text/csv`.
The service verifies the required capabilities for the execution family or live
client. Execute and followup allow an optional attachment_count of one to eight.
The multipart POST carries one JSON command and ordered attachment file parts
totaling at most 16 MiB. Each file part carries its media type and a percent-encoded
UTF-8 basename. The service assigns private staging paths and supplies workers
with ordered `attachments` records containing name, media_type and path. Files
remain readable for the request lifetime. Workers needing asynchronous access
retain their own copies. Text-only requests use JSON.
