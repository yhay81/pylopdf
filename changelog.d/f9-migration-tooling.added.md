Add migration tooling for pymupdf programs (roadmap F9): `python -m
pylopdf.migrate PATH...`, from the new `migrate` extra, renames the `fitz` and
`pymupdf` imports, rewrites `get_toc`/`set_toc` with their page shift,
`embfile_*`, the `xref_*` stream readers, `copy_page`, and pymupdf's in-place
`Rect`, `Point`, and `Matrix` methods, and marks every remaining manual step
with a `# TODO(pylopdf): <hint>` comment that it also lists. Looking up a
pymupdf name that pylopdf does not provide now raises an `AttributeError`
naming its replacement or the milestone that plans it, and `llms.txt` carries
the pymupdf-to-pylopdf name map.
