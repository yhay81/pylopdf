`Document.save(..., incremental=True)` and `Document.to_bytes(...,
incremental=True)` write the bytes that a document was opened from unchanged
and append one update with the objects that differ from them, so signatures
over the original bytes stay valid; pyHanko validates a signature after an
incremental form fill or metadata edit. `Document.can_save_incrementally`
reports whether a document can be saved this way: it must have been opened
from unencrypted bytes that loaded without repair. Incremental saves refuse
`dedupe`, `renumber`, `object_streams`, and `encryption`, and compress only
the streams that the update writes. Such a document keeps its original bytes
in memory while it is open. The codemod rewrites pymupdf's `saveIncr()`,
`save(..., incremental=True)`, `can_save_incrementally()`, and `ez_save()`.
