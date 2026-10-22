Adopt pymupdf's annotation surface: `Page.annots()` returns
read-only `Annot` handles with `type` (a `(PDF_ANNOT_*, subtype)` tuple),
`rect`, `info`, `colors`, `flags`, `xref`, and `parent`, and takes
`types=` to filter, instead of dictionaries; the `AnnotationInfo`
TypedDict is removed. New `AnnotType` and `AnnotFlags` enums carry
pymupdf's `PDF_ANNOT_*` and `PDF_ANNOT_IS_*` constants.
`add_highlight_annot` takes `quads` (rectangles or quads) and returns the
new `Annot`, and `Page.insert_link(link)` replaces `add_link_annot(rect,
uri)` for `LINK_URI` links.
