`Document.save` and `Document.to_bytes` take `compression_level` (zlib's 0
through 9 for the streams that `compress` and `object_streams` compress),
`cross_reference="table"` or `"stream"` to choose the form of the
cross-reference data, `decompress=True` to remove every filter that pylopdf
can decode, and `keep_id=True` to write the file identifier as read. The
codemod rewrites pymupdf's `no_new_id`, `expand=255`, and
`compression_effort`.
