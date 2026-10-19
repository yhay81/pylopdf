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
| `is_repaired` | whether opening recovered damaged cross-reference data (a corrected offset or a rebuilt table); saving normalizes the xref data |
| `metadata` / `set_metadata(dict)` | eight standard Info fields (UTF-16BE aware); 1 MiB aggregate text boundary and atomic writes |
| `get_page_text(pno, option)` | `"text"` / `"words"` / `"blocks"` / `"dict"` / `"rawdict"` (since 0.14.0) |
| `get_text(pages=None)` | batched plain text for every page (`None`) or up to the larger of the page count and 4,096 explicit entries; 4,096-entry chunks each share one interpreter font cache |
| `to_markdown(pages=None, table_strategy="lines", max_size=64 MiB)` | page-at-a-time two-pass Markdown with a bounded linear entry builder; every page by default, explicit entries up to the larger of the page count and 4,096, and cumulative UTF-8 output (`None` opts out), with headings, CJK, emphasis, lists, columns, vertical order, and table controls |
| `render_page(..., max_size=64 MiB)` / `render_pages(..., workers=, max_size=512 MiB)` / `render_page_svg(..., max_size=64 MiB)` | bounded PNG bytes, ordered parallel batches of every page or up to the larger of the page count and 4,096 explicit entries under a cumulative encoded-output cap, or bounded UTF-8 SVG (`None` opts out) |
| `compress_images(dpi=150, quality=75)` | lossy, placement-aware downsampling and JPEG recompression of safe DCT or Flate raster XObjects; returns typed byte/count statistics |
| `set_fallback_font(font, kind=, index=, font_language="ja", max_font_size=64 MiB)` | bounded locale-aware CJK fallback for non-embedded Adobe Japan1, Korea1, GB1, or CNS1 fonts; `None` opts trusted font input out |
| `select` / `delete_page(s)` / `insert_pdf` / `new_page` / `copy_page` | page management; select/delete entries are bounded by the larger of the page count and 4,096, `insert_pdf` accepts any complete source range and registers the form fields of imported pages, and navigation cleanup and link import share the TOC and named-destination limits |
| `get_toc()` / `set_toc(toc)` | cycle-aware bounded outlines sized for large manuals (1-based pages; 65,536 entries/nodes, 131,072 edges, 64 levels, 16 MiB text; named destinations as in `get_links()`) |
| `get_page_labels()` / `set_page_labels(labels)` | page label ranges; fixed caps: 4,096 entries/nodes, 32 levels, 1 MiB label text |
| `get_form_fields()` / `set_form_field(name, value, font=, fontfile=, fontbuffer=, fontindex=, font_language=, max_font_size=64 MiB)` | bounded AcroForm list & fill with 1 MiB caller name/value input plus native, bounded widget appearances and font input; `font` draws with a loaded OpenType `Font` |
| `embfile_add(..., max_size=64 MiB) / embfile_names / embfile_get(name, max_size=64 MiB) / embfile_del` | attachments with symmetric input/decoded-output defaults plus 1 MiB caller text, bounded metadata, and inline FileSpec clone shapes; `max_size=None` explicitly opts out |
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
images are not considered. One call rejects more than 65,536 interpreted
indirect raster placements. Repeating the same settings is idempotent.

## Page { #page }

| Member | Purpose |
|---|---|
| `number` / `parent` / `get_label()` | identity & display label |
| `get_text(option)` / `search_for(needle, max_hits=4096)` | plain or positioned extraction, including glyph-level `"rawdict"` (since 0.14.0), and bounded case-insensitive search; terms stop at 4,096 UTF-8 bytes and `None` opts trusted result sets out |
| `get_text_ocr(dpi=, engine=, tile_size=, overlap=, min_confidence=, rotation=, clip=)` | local PP-OCRv6 positioned words without editing; `rotation` corrects input clockwise and `clip` uses display coordinates |
| `apply_ocr(..., rotation=, clip=, skip_existing=True)` | recognize and insert an orientation-aware invisible searchable layer; skip existing text in the selected region by default |
| `find_tables(strategy="lines", clip=None)` | complete or conservatively refined vector-bordered grids and merged cells; `"text"` opts into borderless detection; `clip` is a display-coordinate region |
| `to_markdown(table_strategy="lines", max_size=64 MiB)` | single-page Markdown with the same table and UTF-8 output controls |
| `get_images()` | drawn images (`bbox`, JPEG passthrough / PNG); rejects partial output above 4,096 placements, 64,000,000 cumulative pixels, or 64 MiB of payloads |
| `get_drawings()` | interpreted vector fill/stroke paths with display-space line/cubic geometry and normalized paint/stroke properties; rejects partial output above 8,192 paths, 131,072 commands, or 131,072 aggregate dash values |
| `get_pixmap(scale=, dpi=, background=, clip=)` / `render(max_size=64 MiB)` / `render_svg(max_size=64 MiB)` | bounded PNG / UTF-8 SVG rendering; `clip` uses display coordinates |
| `rotation` / `set_rotation(deg)` | display rotation |
| `mediabox` / `cropbox` / `rect` / `set_mediabox` / `set_cropbox` | page boxes |
| `insert_image(rect, filename= / stream= / pixmap=, rotate=, keep_proportion=, overlay=, max_size=64 MiB, max_pixels=64,000,000)` | draw bounded JPEG/PNG or reuse an already bounded RGBA `Pixmap`; `None` opts trusted encoded input or PNG pixels out; `rotate` turns clockwise in 90-degree steps; identical input placed again shares one image XObject |
| `show_pdf_page(rect, src, pno=, keep_proportion=, overlay=)` | overlay a PDF page as vectors; `src` may be the same document; placing a page of an unedited `src` again shares its Form XObject |
| `insert_text(point, text, fontsize=, font=, fontname=, fontfile=, fontbuffer=, fontindex=, font_language=, color=, overlay=, max_font_size=64 MiB, max_text_size=1 MiB)` | bounded UTF-8 and 4,096-line standard-14 WinAnsi or shaped subset text; `font` draws with a loaded `Font`, installed script/locale providers are selected automatically, and `font_language` resolves ambiguous Han; `None` opts the corresponding trusted input out |
| `insert_textbox(rect, text, fontsize=, font=, fontname=, fontfile=, fontbuffer=, fontindex=, font_language=, color=, align=, expandtabs=, lineheight=, overlay=, max_font_size=64 MiB, max_text_size=1 MiB)` | bounded UAX #14 wrapping with Core 14, OpenType, a loaded `Font`, or an installed script/locale provider; physical and wrapped layout stop at 4,096 lines, tab expansion is preflighted, and overflow draws nothing |
| `insert_ocr_text_layer(words, rotation=)` | orientation-aware invisible OCR layer; fixed caps: 4,096 words and 1 MiB UTF-8 text per call |
| `replace_text(search, replacement, default_char=, max_size=64 MiB)` | atomic copy-on-write simple-encoded replacement with bounded input/output |
| `annots()` / `get_links()` / `add_highlight_annot(...)` / `add_link_annot(rect, uri)` | bounded annotation/link reads and creation; named destinations resolve through name trees of up to 65,536 entries/nodes, 131,072 edges, 32 levels, and 16 MiB of keys, indexed once and reused by later reads until the next edit; rendering conservatively supplies missing appearances for bounded RGB Highlight, Underline, StrikeOut, and Squiggly dictionaries with valid `QuadPoints` without changing the source PDF |

### Positioned text and glyphs { #positioned-text }

`Page.get_text("rawdict")` and `Document.get_page_text(pno, "rawdict")` are
available since **0.14.0**.
The result keeps the `"dict"` hierarchy: page `width`/`height`, text blocks
(`number`, `type=0`, `bbox`, `lines`), lines (`bbox`, `wmode`, `dir`, `spans`),
and span metadata (`bbox`, `origin`, `size`, `font`, `flags`). Each span replaces
`text` with a `chars` list:

| `TextChar` key | Meaning |
|---|---|
| `c` | complete Unicode mapping of one interpreted PDF glyph; may contain multiple Unicode characters |
| `bbox` | approximate axis-aligned `(x0, y0, x1, y1)` bounds, using the existing glyph geometry |
| `origin` | glyph baseline start point `(x, y)` |

```python
import pylopdf

with pylopdf.open("report.pdf") as doc:
    layout = doc[0].get_text("rawdict")
    for block in layout["blocks"]:
        for line in block["lines"]:
            for span in line["spans"]:
                for char in span["chars"]:
                    print(char["c"], char["bbox"], char["origin"])
```

Positions use the same display points as rendering: a top-left origin,
rightward x and downward y, with page rotation and CropBox offsets applied.
With the default `/UserUnit=1`, one point is 1/72 inch; the current engine does
not apply physical scaling for non-default `/UserUnit` values.
Bounds approximate baseline/font-size geometry;
they are not exact ink outlines or quadrilaterals. The font name and style
flags retain the existing `"dict"` interpretation.

One entry represents one mapped glyph, not necessarily one Unicode code point
or grapheme. Ligatures and other mappings remain intact in `c`; no extra
entries are invented for geometry-inferred spaces or line breaks. Spaces
explicitly present in mapped glyphs remain. Joining `c` values therefore need
not reproduce the spacing of `get_text("text")`. Unmapped glyphs are skipped
with the same deduplicated `PylopdfWarning` as other extraction modes.

Reading order follows the existing line, column, and conservative RTL/CJK
heuristics. Vertical CJK mode is inferred from glyph placement; ruby,
warichu, general mixed-direction paragraphs, and mixed-orientation typography
remain approximate. Arbitrary-angle baseline reading order may differ from
source or semantic order; use glyph origins and line `dir` when an application
needs its own ordering. `rawdict` reports extracted PDF text, including any
existing invisible text layer; it does not run OCR.

The hierarchy resembles
[PyMuPDF rawdict](https://pymupdf.readthedocs.io/en/latest/textpage.html#structure-of-dictionary-outputs),
but is not a complete compatibility contract. pylopdf returns text blocks
only, preserves multi-character glyph mappings in one entry, and exposes its
existing span metadata rather than the additional image/vector, color,
opacity, font-metric, or synthetic-character fields of PyMuPDF. Use
`get_images()` or `get_drawings()` for image or vector extraction.
The [overlay example](https://github.com/yhay81/pylopdf/blob/main/examples/rawdict-overlay.py)
builds a standalone HTML preview of glyph bounds and baseline origins.

`get_drawings()` returns `DrawingInfo` dictionaries with `type="f"`, `"s"`,
or `"fs"`, self-contained line/cubic `items`, `rect`, RGB/opacity, fill rule,
width, cap, join, and dashes. Pattern paints retain their geometry with
`None` color and opacity. Clipping paths, clip-resolved visibility, group and
soft-mask structure, optional-content layer names, text, images, and annotations
are not returned; optional-content visibility is still applied. The result is
rejected rather than truncated above 8,192 paths or 131,072 commands.

Embedded-font `insert_text` requires one font containing every glyph.
`pylopdf[ar]`, `[he]`, `[hi]`, `[jp]`, `[ko]`, `[th]`, `[zh-cn]`, and
`[zh-tw]` provide Noto Sans/Serif generation fonts. Kana, Hangul, Bopomofo,
Arabic, Hebrew, Devanagari, and Thai select an installed provider automatically.
Pure Han keeps the JP-first compatibility default; pass
`font_language="zh-CN"`, `"zh-TW"`, `"ko"`, or `"ja"` when the locale matters.
This is one whole-run font, not per-glyph fallback. Each line is shaped, but
bidirectional paragraph layout and wrapping remain outside this primitive.
RTL shaping renders correctly. Pure RTL lines and lines with one Latin or
numeric token bracketed by RTL text extract in logical Unicode order. Edge
tokens, multiple tokens, and general mixed-direction paragraphs remain in
producer visual order.

Complex shaping can require PDF `/ActualText` to preserve a logical string that
does not map one-to-one to glyphs. Generated PDFs include that data for external
viewers, but pylopdf's current extractor does not yet consume `/ActualText`;
some reordered or context-dependent clusters, especially in longer
Devanagari runs, can therefore extract approximately after reopening.

`insert_textbox` adds wrapping without becoming a rich-text engine. It preserves
explicit newlines, expands tabs, breaks CJK at Unicode opportunities, and uses
grapheme-safe emergency breaks for overlong words. Alignment constants are
`TEXT_ALIGN_LEFT`, `TEXT_ALIGN_CENTER`, `TEXT_ALIGN_RIGHT`, and
`TEXT_ALIGN_JUSTIFY`. A negative return value is the vertical deficit; no page
content or font resource is added in that case.

`Font` follows pymupdf's `Font(fontname=None, fontfile=None, fontbuffer=None)`
and is passed as `insert_text(font=)` and `insert_textbox(font=)`, and an
OpenType face also as `set_form_field(font=)`.
`Font("helv")` or `Font("Times-Bold")` names a Standard 14 font, which is drawn
without embedding and measured with Adobe's AFM widths; pymupdf's other built-in
fonts are not available. `Font(fontfile=)` and `Font(fontbuffer=)` load an
OpenType face as given, a variable font at its default instance, which need not
be regular: the variable Noto Sans CJK JP defaults to Thin. `weight=` selects
another instance on a variable font's weight axis:
`Font(fontfile="NotoSansCJKjp-VF.otf", weight=700)` measures with the bold
instance's advances and embeds it as `NotoSansCJKjp-Bold`, and `weight=650`
works as well, so `weight=400` differs from an omitted weight on such a font. A
weight outside the axis raises `PdfError`, and so does a weight that a static
face does not declare. The `fontfile` and `fontbuffer` arguments of
`insert_text`, `insert_textbox`, and `set_form_field` draw the default
instance; pass the `Font` as `font=` to draw the instance it selects.
`Font(language="ja")` or `Font(family="Noto Sans JP")` selects the
face of an installed font extra, with `is_serif=True` for the serif face.
`font.text_length(text, fontsize)` measures with the shaping that drawing uses,
including kerning and ligatures, so layout code can wrap, align, and paginate
with the font it draws. `ascender` and `descender` are the em fractions that
`insert_textbox` places lines with, and `has_glyph(chr)` returns the glyph
number of a code point, or 0. The CJK extras install one static face per
style, so `is_bold`, `is_italic`, and an exact OS/2 `weight` that the face does
not match raise `PdfError`, and other weights, such as Noto Sans JP Bold, are
loaded from their files. The `pylopdf[ar]`, `[he]`, `[hi]`, and `[th]` extras
install variable faces instead: `Font(language="he", is_bold=True)` selects
the bold instance on the face's weight axis without another file, measures
with that instance's advances, and embeds it under its own PostScript name,
`NotoSansHebrew-Bold`. Their weights run from 100 through 900, or from 400
through 700 for Noto Naskh Arabic, the serif face of `pylopdf[ar]`, and a
weight outside the axis or an italic raises `PdfError`. Installed variable
faces are drawn at the regular weight, also when `insert_text`,
`insert_textbox`, and `set_form_field` select them automatically, because the
default instance of Noto Sans Hebrew is Thin. pylopdf keeps no font registry
and never searches system font directories. It never imitates a weight by stroking or overprinting glyphs;
`allow_regular_fallback=True` explicitly accepts the extra's face with a
`PylopdfWarning`. `is_bold`, `is_italic`, and `is_serif` raise with `fontname`
and font files, where pymupdf would ignore them. Each drawing call still embeds a subset of its own
glyphs.

`set_form_field` generates appearances for text, combo/list choice, checkbox,
and radio widgets. WinAnsi text auto-fits in Helvetica; pass an OpenType
`fontfile` or `fontbuffer` for subset-embedded Unicode, or a loaded OpenType
`Font` as `font`, such as the bold instance of a variable font. A `Font` fits,
aligns, and centers comb positions with the advances that `text_length`
measures, and cannot be combined with the other font arguments; a Standard 14
`Font` raises `PdfError`. Installed locale/script
providers are selected with the same rules as `insert_text`, and
`font_language` resolves ambiguous Han. Existing
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
| `peek_metadata(filename=None, stream=None, password=None, *, max_file_size=None)` | fast metadata/page-count probe with optional input-size rejection and a 127-byte password boundary; `repaired` reports cross-reference recovery |
| `Permissions` | encryption permission flags (IntFlag) |
| `Rect` | rectangle NamedTuple with `width` / `height` |
| `TextPage` / `TextBlock` / `TextLine` / `TextSpan` | `get_text("dict")` TypedDict hierarchy |
| `RawTextPage` / `RawTextBlock` / `RawTextLine` / `RawTextSpan` / `TextChar` | `get_text("rawdict")` TypedDict hierarchy with glyph mappings, bounds, and baseline origins (since 0.14.0) |
| `ImageInfo` / `ImageCompressionResult` / `DrawingInfo` / `AnnotationInfo` / `LinkInfo` / `FormFieldInfo` | TypedDict contracts for mapping-shaped page, document-operation, and form results |
| `PageLabelInfo` / `PageLabelSpec` | normalized page-label output / setter input contracts |
| `DocumentMetadata` / `MetadataUpdate` / `MetadataProbe` | metadata output / partial update / fast-probe contracts |
| `DocumentLimits` / `DocumentComplexity` | immutable untrusted-input budgets, including `max_interpretation_size` for the PDF snapshot and `max_text_glyphs` for positioned layout / cheap structural TypedDict |
| `OcrEngine` / `OcrWord` | reusable pure-Rust PP-OCR engine / positioned result contract |
| `Font(fontname=None, fontfile=None, fontbuffer=None, *, language=None, is_bold=False, is_italic=False, is_serif=False, weight=None, fontindex=0, family=None, allow_regular_fallback=False, max_font_size=64 MiB)` | reusable Standard 14 or OpenType font with `text_length`, `ascender` / `descender`, `has_glyph`, `name`, `family`, `weight`, `is_bold`, `is_italic`, `is_serif`, and `units_per_em`; `weight=` also selects an instance of a variable font file |
| `OcrRotation` / `DrawingItem` / `WordEntry` / `BlockEntry` / `FormFieldType` | runtime-importable OCR-rotation, vector-command, tuple and literal type aliases |
| `TableFinder` / `Table` / `TableDiagnostics` | owned table geometry, cell text (`None` for merged continuations), strategy and confidence evidence; `Table.to_markdown(max_size=64 MiB)` preflights escaped UTF-8 output |
| `PdfError` / `LimitError` / `PasswordError` / `OcrError` / `DocumentClosedError` / `EncryptedDocumentError` / `StalePageError` | exception hierarchy; limit failures expose a stable `.code` (ValueError-compatible base) |
| `Pixmap` | Immutable RGBA8 pixels: `samples` / `width` / `height` / `stride` / `n` / `tobytes(max_size=64 MiB)` / streaming, failure-atomic PNG-only `save(path)`; cp314t and cp315t also support read-only zero-copy `memoryview()` |
| `PylopdfWarning` | recoverable interpretation warnings (xref repair, font resolution, image decode) |

The `TypedDict` contracts affect static typing only; values remain ordinary
pymupdf-style dictionaries. `LinkInfo` requires `kind` and `from`, with
destination-specific optional keys. `PageLabelSpec` requires `startpage`;
`style`, `prefix`, and `firstpagenum` retain their runtime defaults.
