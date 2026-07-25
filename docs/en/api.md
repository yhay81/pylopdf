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
| `to_markdown(pages=None)` | Markdown conversion (headings, CJK joining, emphasis, lists, multicolumn and conservative vertical-CJK order; no automatic tables) |
| `render_page(...)` / `render_pages(..., workers=)` / `render_page_svg(...)` | PNG bytes, ordered parallel PNG batches, or SVG |
| `set_fallback_font(font, kind=, index=)` | CJK fallback for non-embedded fonts |
| `select` / `delete_page(s)` / `insert_pdf` / `new_page` / `copy_page` | page management |
| `get_toc()` / `set_toc(toc)` | outlines (1-based pages) |
| `get_page_labels()` / `set_page_labels(labels)` | page label ranges |
| `get_form_fields()` / `set_form_field(name, value, fontfile=, fontbuffer=, fontindex=)` | AcroForm list & fill with native widget appearances |
| `embfile_add / embfile_names / embfile_get / embfile_del` | file attachments |
| `get_pdfa_claim()` | XMP PDF/A declaration (a read, not validation) |
| `save(...)` / `tobytes(...)` | `garbage=` `deflate=` `object_streams=` `user_pw=` `owner_pw=` `permissions=` |
| `close()` | also via `with` |

## Page { #page }

| Member | Purpose |
|---|---|
| `number` / `parent` / `get_label()` | identity & display label |
| `get_text(option)` / `search_for(needle)` | extraction & case-insensitive search |
| `get_text_ocr(dpi=, engine=, tile_size=, overlap=, min_confidence=, clip=)` | local PP-OCRv6 positioned words without editing; `clip` uses display coordinates |
| `apply_ocr(..., clip=, skip_existing=True)` | recognize and insert an invisible searchable layer; skip existing text in the selected region by default |
| `find_tables(strategy="lines", clip=None)` | complete vector-bordered grids and merged cells; `"text"` opts into borderless detection; `clip` is a display-coordinate region |
| `to_markdown()` | single-page Markdown |
| `get_images()` | drawn images (`bbox`, JPEG passthrough / PNG) |
| `get_pixmap(scale=, dpi=, background=, clip=)` / `render(...)` / `render_svg()` | rendering; `clip` uses display coordinates |
| `rotation` / `set_rotation(deg)` | display rotation |
| `mediabox` / `cropbox` / `rect` / `set_mediabox` / `set_cropbox` | page boxes |
| `insert_image(rect, filename= / stream=, keep_proportion=, overlay=)` | draw JPEG/PNG |
| `show_pdf_page(rect, src, pno=, keep_proportion=, overlay=)` | overlay another PDF page as vectors |
| `insert_text(point, text, fontsize=, fontname=, fontfile=, fontbuffer=, fontindex=, color=, overlay=)` | standard-14 WinAnsi text, or subset-embedded OpenType Unicode text |
| `insert_textbox(rect, text, fontsize=, fontname=, fontfile=, fontbuffer=, fontindex=, color=, align=, expandtabs=, lineheight=, overlay=)` | UAX #14 paragraph wrapping with Core 14 or embedded OpenType metrics; returns spare height and draws nothing on overflow |
| `insert_ocr_text_layer(words)` | invisible OCR text layer (searchable PDFs) |
| `replace_text(search, replacement, default_char=)` | simple-encoded text replacement |
| `annots()` / `add_highlight_annot(...)` / `add_link_annot(rect, uri)` | annotations |

Embedded-font `insert_text` requires one font containing every glyph. It shapes
each line but does not provide font fallback, bidirectional paragraph layout,
or wrapping. RTL shaping renders correctly; extraction currently follows visual
rather than logical order.

`insert_textbox` adds wrapping without becoming a rich-text engine. It preserves
explicit newlines, expands tabs, breaks CJK at Unicode opportunities, and uses
grapheme-safe emergency breaks for overlong words. Alignment constants are
`TEXT_ALIGN_LEFT`, `TEXT_ALIGN_CENTER`, `TEXT_ALIGN_RIGHT`, and
`TEXT_ALIGN_JUSTIFY`. A negative return value is the vertical deficit; no page
content or font resource is added in that case.

`set_form_field` generates appearances for text, combo/list choice, checkbox,
and radio widgets. WinAnsi text auto-fits in Helvetica; pass an OpenType
`fontfile` or `fontbuffer` for subset-embedded Unicode. With `pylopdf[cjk]`
installed, non-WinAnsi values automatically use its sans font. Existing
non-empty checkbox/radio appearances are preserved and missing states receive
vector marks. Missing appearances on other WinAnsi fields are completed at the
same time; `NeedAppearances` is cleared only when every fillable widget is
self-contained. Rich text, comb layout, pushbutton actions, and signatures are
not generated.

`Table.confidence` is a deterministic 0–1 ranking heuristic, not a calibrated
probability. `Table.diagnostics` is a `TableDiagnostics` tuple containing the
strategy and, for borderless text tables, em-normalized alignment error,
minimum gutter and row-gap variation. Complete vector grids score 1.0 and have
`None` for those text-only metrics. `TableFinder.strategy` and
`TableFinder.clip` preserve the settings used.

## Module level { #module-level }

| Name | Purpose |
|---|---|
| `peek_metadata(path_or_stream, password=)` | fast metadata/page-count probe without full parsing |
| `Permissions` | encryption permission flags (IntFlag) |
| `Rect` | rectangle NamedTuple with `width` / `height` |
| `TextPage` / `TextBlock` / `TextLine` / `TextSpan` | `get_text("dict")` TypedDict hierarchy |
| `ImageInfo` / `AnnotationInfo` / `LinkInfo` / `FormFieldInfo` | TypedDict contracts for mapping-shaped page and form results |
| `PageLabelInfo` / `PageLabelSpec` | normalized page-label output / setter input contracts |
| `DocumentMetadata` / `MetadataUpdate` / `MetadataProbe` | metadata output / partial update / fast-probe contracts |
| `OcrEngine` / `OcrWord` | reusable pure-Rust PP-OCR engine / positioned result contract |
| `WordEntry` / `BlockEntry` / `FormFieldType` | runtime-importable tuple and literal type aliases |
| `TableFinder` / `Table` / `TableDiagnostics` | owned table geometry, cell text (`None` for merged continuations), strategy and confidence evidence |
| `PdfError` / `PasswordError` / `OcrError` / `DocumentClosedError` / `EncryptedDocumentError` / `StalePageError` | exception hierarchy (ValueError-compatible base) |
| `Pixmap` | Immutable RGBA8 pixels: `samples` / `width` / `height` / `stride` / `n` / `tobytes()`; cp314t also supports read-only zero-copy `memoryview()` |
| `PylopdfWarning` | interpreter warnings (font resolution, image decode) |

The `TypedDict` contracts affect static typing only; values remain ordinary
pymupdf-style dictionaries. `LinkInfo` requires `kind` and `from`, with
destination-specific optional keys. `PageLabelSpec` requires `startpage`;
`style`, `prefix`, and `firstpagenum` retain their runtime defaults.
