`Document.metadata` is a live `Metadata` mapping of the Info entries
present, under the keys of `Probe.metadata`: `title`, `author`, `subject`,
`keywords`, `creator`, `producer`, `created`, `modified`, and `trapped`.
Assigning a key or calling `update()` writes in one edit, `del` removes an
entry, assigning a mapping replaces every entry, and `clear()` or an empty
mapping removes the Info dictionary; values are non-empty strings, and
`trapped` is `True`, `False`, or `Unknown`. `Document.version` and
`Document.encryption_method` replace the `format` and `encryption` entries,
and `Document.pdfa_claim` returns a `PdfaClaim` record or `None` in place of
`get_pdfa_claim()`'s tuple. `set_metadata`, `DocumentMetadata`, and
`MetadataUpdate` are removed; the codemod rewrites `set_metadata` calls with
a literal dictionary and reads of the old keys.
