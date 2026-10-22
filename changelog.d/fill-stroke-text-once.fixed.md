Text painted with both fill and stroke (text render modes 2 and 6) is now
extracted once instead of twice by `get_text`, `search_for`, `find_tables`,
and `to_markdown`. Text extraction reads the paths and glyphs of the same
device as `Page.content()`.
