---
title: API overview
description: A compact map of pylopdf Document, Page, Pixmap, Rect, permissions, warnings and exceptions.
---

# API overview

Full docstrings live in the package (`help(pylopdf.Document)`); this page is a
map. All page numbers are 0-based except `get_toc` / `set_toc` (1-based,
pymupdf-compatible). All coordinates are top-left-origin display space.
The [API stability policy](stability.md) defines the public boundary and
deprecation lifecycle.

## Document { #document }

`pylopdf.Document(filename=None, stream=None, password=None, max_decompressed_size=None, *, limits=None)` —
`pylopdf.open()` is an alias constructor. Context-manager support included.

| Member | Purpose |
|---|---|
| `doc[i]` / `load_page(pno)` / iteration | `Page` views (negative indices; re-fetch after structural changes) |
| `page_count` / `len(doc)` | number of pages |
| `limits` / `complexity` | immutable open-time resource policy / cheap structural facts without stream decoding |
| `needs_pass` / `is_encrypted` / `authenticate(pw)` | encryption state & unlock (pymupdf semantics) |
| `is_repaired` | whether opening repaired an incorrect final classic `startxref`; saving normalizes the xref data |
| `metadata` / `set_metadata(dict)` | Info dictionary (UTF-16BE aware) |
| `get_page_text(pno, option)` | `"text"` / `"words"` / `"blocks"` / `"dict"` |
| `to_markdown(pages=None, table_strategy="lines")` | Markdown conversion (headings, CJK joining, emphasis, lists, multicolumn and conservative vertical-CJK order; bordered tables by default, `"text"` adds borderless tables, `None` disables tables) |
| `render_page(...)` / `render_pages(..., workers=)` / `render_page_svg(...)` | PNG bytes, ordered parallel PNG batches, or SVG |
| `compress_images(dpi=150, quality=75)` | lossy, placement-aware downsampling and JPEG recompression of safe DCT or Flate raster XObjects; returns typed byte/count statistics |
| `set_fallback_font(font, kind=, index=)` | CJK fallback for non-embedded fonts |
| `select` / `delete_page(s)` / `insert_pdf` / `new_page` / `copy_page` | page management |
| `get_toc()` / `set_toc(toc)` | outlines (1-based pages) |
| `get_page_labels()` / `set_page_labels(labels)` | page label ranges; fixed caps: 4,096 entries/nodes, 32 levels, 1 MiB label text |
| `get_form_fields()` / `set_form_field(name, value, fontfile=, fontbuffer=, fontindex=)` | bounded AcroForm list & fill with native, bounded widget appearances |
| `embfile_add / embfile_names / embfile_get(name, max_size=64 MiB) / embfile_del` | file attachments with bounded decoding; `max_size=None` explicitly opts out |
| `get_pdfa_claim(max_size=1 MiB)` | bounded XMP PDF/A declaration read; `max_size=None` explicitly opts out, and this is not validation |
| `save(...)` / `tobytes(...)` | `garbage=` `deflate=` `object_streams=` `user_pw=` `owner_pw=` `permissions=` |
| `close()` | also via `with` |

`compress_images()` interprets every page to find each indirect raster object's
largest placement, then edits a lopdf clone atomically. `dpi=None` disables
downsampling but retains quality recompression. The conservative boundary is
direct, single-filter, 8-bit DeviceGray/DeviceRGB DCT or Flate streams without
masks or custom decode arrays. DCT decode parameters are excluded; Flate may
use no predictor or a consistent PNG predictor. Unsupported interpreted
indirect images and encodings that would not be smaller are skipped; inline
images are not considered. Repeating the same settings is idempotent.

## Page { #page }

| Member | Purpose |
|---|---|
| `number` / `parent` / `get_label()` | identity & display label |
| `get_text(option)` / `search_for(needle)` | extraction & case-insensitive search |
| `get_text_ocr(dpi=, engine=, tile_size=, overlap=, min_confidence=, rotation=, clip=)` | local PP-OCRv6 positioned words without editing; `rotation` corrects input clockwise and `clip` uses display coordinates |
| `apply_ocr(..., rotation=, clip=, skip_existing=True)` | recognize and insert an orientation-aware invisible searchable layer; skip existing text in the selected region by default |
| `find_tables(strategy="lines", clip=None)` | complete or conservatively refined vector-bordered grids and merged cells; `"text"` opts into borderless detection; `clip` is a display-coordinate region |
| `to_markdown(table_strategy="lines")` | single-page Markdown with the same table controls |
| `get_images()` | drawn images (`bbox`, JPEG passthrough / PNG); rejects partial output above 4,096 placements, 64,000,000 cumulative pixels, or 64 MiB of payloads |
| `get_drawings()` | interpreted vector fill/stroke paths with display-space line/cubic geometry and normalized paint/stroke properties |
| `get_pixmap(scale=, dpi=, background=, clip=)` / `render(...)` / `render_svg()` | rendering; `clip` uses display coordinates |
| `rotation` / `set_rotation(deg)` | display rotation |
| `mediabox` / `cropbox` / `rect` / `set_mediabox` / `set_cropbox` | page boxes |
| `insert_image(rect, filename= / stream= / pixmap=, rotate=, keep_proportion=, overlay=)` | draw JPEG/PNG or reuse a rendered RGBA `Pixmap`; `rotate` turns it clockwise in 90-degree steps |
| `show_pdf_page(rect, src, pno=, keep_proportion=, overlay=)` | overlay a PDF page as vectors; `src` may be the same document |
| `insert_text(point, text, fontsize=, fontname=, fontfile=, fontbuffer=, fontindex=, color=, overlay=)` | standard-14 WinAnsi or shaped subset text; `pylopdf[cjk]` auto-selects its JP font for Japanese/Han |
| `insert_textbox(rect, text, fontsize=, fontname=, fontfile=, fontbuffer=, fontindex=, color=, align=, expandtabs=, lineheight=, overlay=)` | UAX #14 wrapping with Core 14, explicit OpenType, or auto-selected JP metrics; returns spare height and draws nothing on overflow |
| `insert_ocr_text_layer(words, rotation=)` | orientation-aware invisible OCR text layer (searchable PDFs) |
| `replace_text(search, replacement, default_char=)` | simple-encoded text replacement |
| `annots()` / `get_links()` / `add_highlight_annot(...)` / `add_link_annot(rect, uri)` | bounded annotation/link reads and creation |

`get_drawings()` returns `DrawingInfo` dictionaries with `type="f"`, `"s"`,
or `"fs"`, self-contained line/cubic `items`, `rect`, RGB/opacity, fill rule,
width, cap, join, and dashes. Pattern paints retain their geometry with
`None` color and opacity. Clipping paths, clip-resolved visibility, group and
soft-mask structure, optional-content layer names, text, images, and annotations
are not returned; optional-content visibility is still applied. The result is
rejected rather than truncated above 8,192 paths or 131,072 commands.

Embedded-font `insert_text` requires one font containing every glyph. If no
source is passed, `pylopdf[cjk]` auto-selects its JP-subset Noto Sans for
Japanese/Han, or Noto Serif for a Times `fontname`. This is one whole-run font,
not per-glyph fallback. Pass an explicit OpenType font for Hangul,
locale-specific Chinese glyph forms, other scripts, or another typeface. Each
line is shaped, but bidirectional paragraph layout and wrapping remain outside
this primitive. RTL shaping renders correctly; extraction currently follows
visual rather than logical order.

`insert_textbox` adds wrapping without becoming a rich-text engine. It preserves
explicit newlines, expands tabs, breaks CJK at Unicode opportunities, and uses
grapheme-safe emergency breaks for overlong words. Alignment constants are
`TEXT_ALIGN_LEFT`, `TEXT_ALIGN_CENTER`, `TEXT_ALIGN_RIGHT`, and
`TEXT_ALIGN_JUSTIFY`. A negative return value is the vertical deficit; no page
content or font resource is added in that case.

`set_form_field` generates appearances for text, combo/list choice, checkbox,
and radio widgets. WinAnsi text auto-fits in Helvetica; pass an OpenType
`fontfile` or `fontbuffer` for subset-embedded Unicode. With `pylopdf[cjk]`
installed, non-WinAnsi values try its JP-subset sans font; pass a matching font
for Hangul or locale-specific Chinese typography. Existing
non-empty checkbox/radio appearances are preserved and missing states receive
vector marks. Missing appearances on other WinAnsi fields are completed at the
same time; `NeedAppearances` is cleared only when every fillable widget is
self-contained. Comb text fields honor inherited `MaxLen` and alignment, center
each Unicode grapheme in its position, and reject overlength values atomically.
Rich text, pushbutton actions, and signatures are not generated.

`Table.confidence` is a deterministic 0–1 ranking heuristic, not a calibrated
probability. `Table.diagnostics` is a `TableDiagnostics` tuple containing the
strategy and, for borderless text tables, em-normalized alignment error,
minimum gutter and row-gap variation. Complete vector grids score 1.0,
sparse-rule hybrid grids score 0.95, and both have `None` for those text-only
metrics. `TableFinder.strategy` and
`TableFinder.clip` preserve the settings used.

## Module level { #module-level }

| Name | Purpose |
|---|---|
| `peek_metadata(path_or_stream, password=)` | fast metadata/page-count probe; `repaired` reports bounded classic-`startxref` recovery |
| `Permissions` | encryption permission flags (IntFlag) |
| `Rect` | rectangle NamedTuple with `width` / `height` |
| `TextPage` / `TextBlock` / `TextLine` / `TextSpan` | `get_text("dict")` TypedDict hierarchy |
| `ImageInfo` / `ImageCompressionResult` / `DrawingInfo` / `AnnotationInfo` / `LinkInfo` / `FormFieldInfo` | TypedDict contracts for mapping-shaped page, document-operation, and form results |
| `PageLabelInfo` / `PageLabelSpec` | normalized page-label output / setter input contracts |
| `DocumentMetadata` / `MetadataUpdate` / `MetadataProbe` | metadata output / partial update / fast-probe contracts |
| `DocumentLimits` / `DocumentComplexity` | immutable untrusted-input budgets / cheap structural TypedDict |
| `OcrEngine` / `OcrWord` | reusable pure-Rust PP-OCR engine / positioned result contract |
| `OcrRotation` / `DrawingItem` / `WordEntry` / `BlockEntry` / `FormFieldType` | runtime-importable OCR-rotation, vector-command, tuple and literal type aliases |
| `TableFinder` / `Table` / `TableDiagnostics` | owned table geometry, cell text (`None` for merged continuations), strategy and confidence evidence |
| `PdfError` / `LimitError` / `PasswordError` / `OcrError` / `DocumentClosedError` / `EncryptedDocumentError` / `StalePageError` | exception hierarchy; limit failures expose a stable `.code` (ValueError-compatible base) |
| `Pixmap` | Immutable RGBA8 pixels: `samples` / `width` / `height` / `stride` / `n` / `tobytes()` / PNG-only `save(path)`; cp314t also supports read-only zero-copy `memoryview()` |
| `PylopdfWarning` | recoverable interpretation warnings (xref repair, font resolution, image decode) |

The `TypedDict` contracts affect static typing only; values remain ordinary
pymupdf-style dictionaries. `LinkInfo` requires `kind` and `from`, with
destination-specific optional keys. `PageLabelSpec` requires `startpage`;
`style`, `prefix`, and `firstpagenum` retain their runtime defaults.
