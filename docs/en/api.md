---
title: API overview
description: A compact map of pylopdf Document, Page, Pixmap, Rect, permissions, warnings and exceptions.
---

# API overview

Full docstrings live in the package (`help(pylopdf.Document)`); this page is a
map. All page numbers are 0-based except `get_toc` / `set_toc` (1-based,
pymupdf-compatible). All coordinates are top-left-origin display space.

## Document { #document }

`pylopdf.Document(filename=None, stream=None, password=None, max_decompressed_size=None)` —
`pylopdf.open()` is an alias constructor. Context-manager support included.

| Member | Purpose |
|---|---|
| `doc[i]` / `load_page(pno)` / iteration | `Page` views (negative indices; re-fetch after structural changes) |
| `page_count` / `len(doc)` | number of pages |
| `needs_pass` / `is_encrypted` / `authenticate(pw)` | encryption state & unlock (pymupdf semantics) |
| `metadata` / `set_metadata(dict)` | Info dictionary (UTF-16BE aware) |
| `get_page_text(pno, option)` | `"text"` / `"words"` / `"blocks"` / `"dict"` |
| `to_markdown(pages=None)` | Markdown conversion (headings, CJK joining, emphasis, lists) |
| `render_page(...)` / `render_pages(..., workers=)` / `render_page_svg(...)` | PNG bytes, ordered parallel PNG batches, or SVG |
| `set_fallback_font(font, kind=, index=)` | CJK fallback for non-embedded fonts |
| `select` / `delete_page(s)` / `insert_pdf` / `new_page` / `copy_page` | page management |
| `get_toc()` / `set_toc(toc)` | outlines (1-based pages) |
| `get_page_labels()` / `set_page_labels(labels)` | page label ranges |
| `get_form_fields()` / `set_form_field(name, value)` | AcroForm list & fill (NeedAppearances) |
| `embfile_add / embfile_names / embfile_get / embfile_del` | file attachments |
| `get_pdfa_claim()` | XMP PDF/A declaration (a read, not validation) |
| `save(...)` / `tobytes(...)` | `garbage=` `deflate=` `object_streams=` `user_pw=` `owner_pw=` `permissions=` |
| `close()` | also via `with` |

## Page { #page }

| Member | Purpose |
|---|---|
| `number` / `parent` / `get_label()` | identity & display label |
| `get_text(option)` / `search_for(needle)` | extraction & case-insensitive search |
| `find_tables(strategy="lines")` | vector-bordered grids and merged cells; `"text"` opts into borderless detection |
| `to_markdown()` | single-page Markdown |
| `get_images()` | drawn images (`bbox`, JPEG passthrough / PNG) |
| `get_pixmap(scale=, dpi=, background=, clip=)` / `render(...)` / `render_svg()` | rendering; `clip` uses display coordinates |
| `rotation` / `set_rotation(deg)` | display rotation |
| `mediabox` / `cropbox` / `rect` / `set_mediabox` / `set_cropbox` | page boxes |
| `insert_image(rect, filename= / stream=, keep_proportion=, overlay=)` | draw JPEG/PNG |
| `show_pdf_page(rect, src, pno=, keep_proportion=, overlay=)` | overlay another PDF page as vectors |
| `insert_text(point, text, fontsize=, fontname=, color=)` | standard-14 text (WinAnsi) |
| `insert_ocr_text_layer(words)` | invisible OCR text layer (searchable PDFs) |
| `replace_text(search, replacement, default_char=)` | simple-encoded text replacement |
| `annots()` / `add_highlight_annot(...)` / `add_link_annot(rect, uri)` | annotations |

## Module level { #module-level }

| Name | Purpose |
|---|---|
| `peek_metadata(path_or_stream, password=)` | fast metadata/page-count probe without full parsing |
| `Permissions` | encryption permission flags (IntFlag) |
| `Rect` | rectangle NamedTuple with `width` / `height` |
| `TableFinder` / `Table` | owned bordered-table geometry and cell text (`None` for merged continuations) |
| `PdfError` / `PasswordError` / `DocumentClosedError` / `EncryptedDocumentError` / `StalePageError` | exception hierarchy (ValueError-compatible base) |
| `Pixmap` | Immutable RGBA8 pixels: `samples` / `width` / `height` / `stride` / `n` / `tobytes()`; cp314t also supports read-only zero-copy `memoryview()` |
| `PylopdfWarning` | interpreter warnings (font resolution, image decode) |
