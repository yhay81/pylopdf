`Document` gains the catalog and XMP properties, each read and assigned as
an attribute and removed with `del`: `xmp`, the XMP packet as text or
`None` (1 MiB limits; a written packet stays unfiltered, so compressing saves
keep it readable); `page_mode` and `page_layout`, with the new `PageMode` and
`PageLayout` enums; `mark_info`, with the new `MarkInfo` record; and
`language`, the catalog's complete BCP 47 `/Lang` tag, such as `zh-TW` or
`sr-Latn-RS`, where pymupdf keeps at most three letters so that `zh-TW` and
`zh-HK` both read as `zh-Hant`. `has_links()`, `has_annots()`, and
`is_closed` follow pymupdf. The codemod rewrites pymupdf's getters and
setters of these properties.
