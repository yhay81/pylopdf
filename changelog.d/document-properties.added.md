`Document` gains pymupdf's XMP and catalog properties: `get_xml_metadata`,
`set_xml_metadata`, and `del_xml_metadata`; `pagemode`, `set_pagemode`,
`pagelayout`, and `set_pagelayout`; `markinfo` and `set_markinfo`, with the
new `MarkInfo` dictionary type; `has_links` and `has_annots`; and `is_closed`,
`is_pdf`, and `name`. XMP reads and writes default to a 1 MiB limit, and
written XMP stays unfiltered when saving with `deflate=True`.
