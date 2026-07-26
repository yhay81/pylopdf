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
Password input stops at 127 UTF-8 bytes.

| Member | Purpose |
|---|---|
| `doc[i]` / `load_page(pno)` / iteration | `Page` views (negative indices; re-fetch after structural changes) |
| `page_count` / `len(doc)` | number of pages |
| `limits` / `complexity` | immutable open-time resource policy / cheap structural facts without stream decoding |
| `needs_pass` / `is_encrypted` / `authenticate(pw)` | encryption state & unlock with a 127-byte password boundary (pymupdf semantics) |
| `is_repaired` | whether opening repaired an incorrect final classic `startxref`; saving normalizes the xref data |
| `metadata` / `set_metadata(dict)` | eight standard Info fields (UTF-16BE aware); 1 MiB aggregate text boundary and atomic writes |
| `get_page_text(pno, option)` | `"text"` / `"words"` / `"blocks"` / `"dict"` |
| `to_markdown(pages=None, table_strategy="lines", max_size=64 MiB)` | page-at-a-time two-pass Markdown with a bounded linear entry builder; max 4,096 pages and cumulative UTF-8 output (`None` opts out), with headings, CJK, emphasis, lists, columns, vertical order, and table controls |
| `render_page(..., max_size=64 MiB)` / `render_pages(..., workers=, max_size=512 MiB)` / `render_page_svg(..., max_size=64 MiB)` | bounded PNG bytes, ordered parallel batches capped at 4,096 pages and cumulative encoded output, or bounded UTF-8 SVG (`None` opts out) |
| `compress_images(dpi=150, quality=75)` | lossy, placement-aware downsampling and JPEG recompression of safe DCT or Flate raster XObjects; returns typed byte/count statistics |
| `set_fallback_font(font, kind=, index=, max_font_size=64 MiB)` | bounded CJK fallback for non-embedded fonts; `None` opts trusted font input out |
| `select` / `delete_page(s)` / `insert_pdf` / `new_page` / `copy_page` | page management; select/delete/insert batches are capped at 4,096 entries |
| `get_toc()` / `set_toc(toc)` | cycle-aware bounded outlines (1-based pages; 4,096 entries/nodes, 8,192 edges, 64 levels, 1 MiB text) |
| `get_page_labels()` / `set_page_labels(labels)` | page label ranges; fixed caps: 4,096 entries/nodes, 32 levels, 1 MiB label text |
| `get_form_fields()` / `set_form_field(name, value, fontfile=, fontbuffer=, fontindex=, max_font_size=64 MiB)` | bounded AcroForm list & fill with native, bounded widget appearances and font input |
| `embfile_add(..., max_size=64 MiB) / embfile_names / embfile_get(name, max_size=64 MiB) / embfile_del` | attachments with symmetric input/decoded-output defaults plus bounded metadata and inline FileSpec clone shapes; `max_size=None` explicitly opts out |
| `get_pdfa_claim(max_size=1 MiB)` | bounded XMP PDF/A declaration read; `max_size=None` explicitly opts out, and this is not validation |
| `save(...)` / `tobytes(..., max_size=512 MiB)` | atomic replacement after a complete same-directory streamed write / bounded PDF bytes; `garbage=` `deflate=` `object_streams=` and 127-byte-bounded `user_pw=` / `owner_pw=`; `max_size=None` opts out |
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
| `get_text(option)` / `search_for(needle, max_hits=4096)` | extraction and bounded case-insensitive search; terms stop at 4,096 UTF-8 bytes and `None` opts trusted result sets out |
| `get_text_ocr(dpi=, engine=, tile_size=, overlap=, min_confidence=, rotation=, clip=)` | local PP-OCRv6 positioned words without editing; `rotation` corrects input clockwise and `clip` uses display coordinates |
| `apply_ocr(..., rotation=, clip=, skip_existing=True)` | recognize and insert an orientation-aware invisible searchable layer; skip existing text in the selected region by default |
| `find_tables(strategy="lines", clip=None)` | complete or conservatively refined vector-bordered grids and merged cells; `"text"` opts into borderless detection; `clip` is a display-coordinate region |
| `to_markdown(table_strategy="lines", max_size=64 MiB)` | single-page Markdown with the same table and UTF-8 output controls |
| `get_images()` | drawn images (`bbox`, JPEG passthrough / PNG); rejects partial output above 4,096 placements, 64,000,000 cumulative pixels, or 64 MiB of payloads |
| `get_drawings()` | interpreted vector fill/stroke paths with display-space line/cubic geometry and normalized paint/stroke properties |
| `get_pixmap(scale=, dpi=, background=, clip=)` / `render(max_size=64 MiB)` / `render_svg(max_size=64 MiB)` | bounded PNG / UTF-8 SVG rendering; `clip` uses display coordinates |
| `rotation` / `set_rotation(deg)` | display rotation |
| `mediabox` / `cropbox` / `rect` / `set_mediabox` / `set_cropbox` | page boxes |
| `insert_image(rect, filename= / stream= / pixmap=, rotate=, keep_proportion=, overlay=, max_size=64 MiB, max_pixels=64,000,000)` | draw bounded JPEG/PNG or reuse an already bounded RGBA `Pixmap`; `None` opts trusted encoded input or PNG pixels out; `rotate` turns clockwise in 90-degree steps |
| `show_pdf_page(rect, src, pno=, keep_proportion=, overlay=)` | overlay a PDF page as vectors; `src` may be the same document |
| `insert_text(point, text, fontsize=, fontname=, fontfile=, fontbuffer=, fontindex=, color=, overlay=, max_font_size=64 MiB, max_text_size=1 MiB)` | bounded UTF-8 and 4,096-line standard-14 WinAnsi or shaped subset text; `pylopdf[cjk]` auto-selects its JP font; `None` opts the corresponding trusted input out |
| `insert_textbox(rect, text, fontsize=, fontname=, fontfile=, fontbuffer=, fontindex=, color=, align=, expandtabs=, lineheight=, overlay=, max_font_size=64 MiB, max_text_size=1 MiB)` | bounded UAX #14 wrapping with Core 14, OpenType, or auto-selected JP metrics; physical and wrapped layout stop at 4,096 lines, tab expansion is preflighted, and overflow draws nothing |
| `insert_ocr_text_layer(words, rotation=)` | orientation-aware invisible OCR layer; fixed caps: 4,096 words and 1 MiB UTF-8 text per call |
| `replace_text(search, replacement, default_char=, max_size=64 MiB)` | atomic copy-on-write simple-encoded replacement with bounded input/output |
| `annots()` / `get_links()` / `add_highlight_annot(...)` / `add_link_annot(rect, uri)` | bounded annotation/link reads, one cycle-aware named-destination index per call, and creation |

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
| `peek_metadata(filename=None, stream=None, password=None, *, max_file_size=None)` | fast metadata/page-count probe with optional input-size rejection and a 127-byte password boundary; `repaired` reports bounded classic-`startxref` recovery |
| `Permissions` | encryption permission flags (IntFlag) |
| `Rect` | rectangle NamedTuple with `width` / `height` |
| `TextPage` / `TextBlock` / `TextLine` / `TextSpan` | `get_text("dict")` TypedDict hierarchy |
| `ImageInfo` / `ImageCompressionResult` / `DrawingInfo` / `AnnotationInfo` / `LinkInfo` / `FormFieldInfo` | TypedDict contracts for mapping-shaped page, document-operation, and form results |
| `PageLabelInfo` / `PageLabelSpec` | normalized page-label output / setter input contracts |
| `DocumentMetadata` / `MetadataUpdate` / `MetadataProbe` | metadata output / partial update / fast-probe contracts |
| `DocumentLimits` / `DocumentComplexity` | immutable untrusted-input budgets, including `max_interpretation_size` for the PDF snapshot and `max_text_glyphs` for positioned layout / cheap structural TypedDict |
| `OcrEngine` / `OcrWord` | reusable pure-Rust PP-OCR engine / positioned result contract |
| `OcrRotation` / `DrawingItem` / `WordEntry` / `BlockEntry` / `FormFieldType` | runtime-importable OCR-rotation, vector-command, tuple and literal type aliases |
| `TableFinder` / `Table` / `TableDiagnostics` | owned table geometry, cell text (`None` for merged continuations), strategy and confidence evidence; `Table.to_markdown(max_size=64 MiB)` preflights escaped UTF-8 output |
| `PdfError` / `LimitError` / `PasswordError` / `OcrError` / `DocumentClosedError` / `EncryptedDocumentError` / `StalePageError` | exception hierarchy; limit failures expose a stable `.code` (ValueError-compatible base) |
| `Pixmap` | Immutable RGBA8 pixels: `samples` / `width` / `height` / `stride` / `n` / `tobytes(max_size=64 MiB)` / streaming, failure-atomic PNG-only `save(path)`; cp314t also supports read-only zero-copy `memoryview()` |
| `PylopdfWarning` | recoverable interpretation warnings (xref repair, font resolution, image decode) |

The `TypedDict` contracts affect static typing only; values remain ordinary
pymupdf-style dictionaries. `LinkInfo` requires `kind` and `from`, with
destination-specific optional keys. `PageLabelSpec` requires `startpage`;
`style`, `prefix`, and `firstpagenum` retain their runtime defaults.
