Add migration tooling for pymupdf programs and pylopdf 0.15 programs:
`python -m pylopdf.migrate PATH...`, from the new `migrate` extra, renames the
`fitz` and `pymupdf` imports, rewrites opening documents, the page operations
that `doc.pages` replaces (indexing, measuring, and iterating a variable bound
to a document through `.pages`), `peek_metadata`, `get_toc`/`set_toc` with
their page shift, `embfile_*`, the `xref_*` stream readers, and pymupdf's
in-place `Rect`, `Point`, and `Matrix` methods, and marks every remaining
manual step with a `# TODO(pylopdf): <hint>` comment that it also lists.
Looking up a pymupdf name, or a pylopdf 0.15 name, that pylopdf does not
provide now raises an `AttributeError` naming its replacement or the roadmap
milestone that adds the capability, and `llms.txt` carries both name maps.
