Text painted with both fill and stroke (text render modes 2 and 6) is now
extracted once instead of twice by text extraction, search, table detection,
and Markdown conversion. Text extraction reads the paths and glyphs of the
same device as `Page.content()`.
