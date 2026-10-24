`Layout.to_html()` and `Layout.to_xml()` serialize a page's layout as
positioned HTML and as XML of blocks, lines, fonts, and characters, as
pymupdf's `"html"` and `"xml"` text outputs do; `json.dumps(layout.to_dict())`
gives the JSON output.
