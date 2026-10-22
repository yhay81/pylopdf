`pylopdf.open(source, /, *, password=None, limits=None)` opens a PDF from a
path or from `bytes`, `bytearray`, or `memoryview`, and `Document(*,
limits=None)` creates an empty document; `open` is no longer the `Document`
class, and its `filename=`, `stream=`, and `max_decompressed_size=` arguments
are gone (set that budget with `DocumentLimits`). `pylopdf.probe(source, /,
*, password=None, max_file_size=None)` replaces `peek_metadata` and returns a
`Probe` record with `page_count`, `version`, `is_encrypted`, `is_repaired`,
and `metadata` keyed `title`, `author`, `subject`, `keywords`, `creator`,
`producer`, `created`, and `modified`. `Document.needs_password` replaces
`needs_pass`, and the new `Document.path` is the file the document came from.
