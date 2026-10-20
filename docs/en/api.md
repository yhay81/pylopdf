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
| `objects` / `trailer` / `catalog` / `page_xref(pno)` | typed low-level access to the object graph, replacing pymupdf's `xref_*` methods ([low-level objects](#low-level-objects)) |
| `metadata` / `set_metadata(dict)` | eight standard Info fields (UTF-16BE aware); 1 MiB aggregate text boundary and atomic writes |
| `get_page_text(pno, option)` | `"text"` / `"words"` / `"blocks"` / `"dict"` / `"rawdict"` (since 0.14.0) |
| `get_text(pages=None)` | batched plain text for every page (`None`) or up to the larger of the page count and 4,096 explicit entries; 4,096-entry chunks each share one interpreter font cache |
| `to_markdown(pages=None, table_strategy="lines", max_size=64 MiB)` | page-at-a-time two-pass Markdown with a bounded linear entry builder; every page by default, explicit entries up to the larger of the page count and 4,096, and cumulative UTF-8 output (`None` opts out), with headings, CJK, emphasis, lists, columns, vertical order, and table controls |
| `render_page(..., max_size=64 MiB)` / `render_pages(..., workers=, max_size=512 MiB)` / `render_page_svg(..., max_size=64 MiB)` | bounded PNG bytes, ordered parallel batches of every page or up to the larger of the page count and 4,096 explicit entries under a cumulative encoded-output cap, or bounded UTF-8 SVG (`None` opts out) |
| `compress_images(dpi=150, quality=75)` | lossy, placement-aware downsampling and JPEG recompression of safe DCT or Flate raster XObjects; returns typed byte/count statistics |
| `set_fallback_font(font, kind=, index=, font_language="ja", max_font_size=64 MiB)` | bounded locale-aware CJK fallback for non-embedded Adobe Japan1, Korea1, GB1, or CNS1 fonts; `None` opts trusted font input out |
| `select` / `delete_page(s)` / `insert_pdf` / `new_page` / `copy_page` | page management; select/delete entries are bounded by the larger of the page count and 4,096, `insert_pdf` accepts any complete source range and registers the form fields of imported pages, select/delete remove the widgets, fields, and tagged structure content of dropped pages, and navigation cleanup and link import share the TOC and named-destination limits |
| `get_toc()` / `set_toc(toc)` | cycle-aware bounded outlines sized for large manuals (1-based pages; 65,536 entries/nodes, 131,072 edges, 64 levels, 16 MiB text; named destinations as in `get_links()`) |
| `get_page_labels()` / `set_page_labels(labels)` | page label ranges; fixed caps: 4,096 entries/nodes, 32 levels, 1 MiB label text |
| `get_form_fields()` / `set_form_field(name, value, font=, fontfile=, fontbuffer=, fontindex=, font_language=, max_font_size=64 MiB)` | bounded AcroForm list & fill with 1 MiB caller name/value input plus native, bounded widget appearances and font input; `font` draws with a loaded OpenType `Font` |
| `embfile_add(..., max_size=64 MiB) / embfile_names / embfile_get(name, max_size=64 MiB) / embfile_del` | attachments with symmetric input/decoded-output defaults plus 1 MiB caller text, bounded metadata, and inline FileSpec clone shapes; `max_size=None` explicitly opts out |
| `get_pdfa_claim(max_size=1 MiB)` | bounded XMP PDF/A declaration read; `max_size=None` explicitly opts out, and this is not validation |
| `save(...)` / `tobytes(..., max_size=512 MiB)` | atomic replacement after a complete same-directory streamed write / bounded PDF bytes; `garbage=0`–`4` (pymupdf's levels; 3 and 4 merge identical objects and streams) `deflate=` `use_objstms=` and 127-byte-bounded `user_pw=` / `owner_pw=`; `max_size=None` opts out |
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
| `xref` / `obj` | the page dictionary's object number / live low-level handle; inherited attributes stay on its ancestors |
| `get_text(option)` / `search_for(text, max_hits=4096)` | plain or positioned extraction, including glyph-level `"rawdict"` (since 0.14.0), and bounded case-insensitive search; terms stop at 4,096 UTF-8 bytes and `None` opts trusted result sets out |
| `get_text_ocr(dpi=, engine=, tile_size=, overlap=, min_confidence=, rotation=, clip=)` | local PP-OCRv6 positioned words without editing; `rotation` corrects input clockwise and `clip` uses display coordinates |
| `apply_ocr(..., rotation=, clip=, skip_existing=True)` | recognize and insert an orientation-aware invisible searchable layer; skip existing text in the selected region by default |
| `find_tables(clip=None, strategy=None)` | complete or conservatively refined vector-bordered grids and merged cells; `"text"` opts into borderless detection; `clip` is a display-coordinate region |
| `to_markdown(table_strategy="lines", max_size=64 MiB)` | single-page Markdown with the same table and UTF-8 output controls |
| `get_images()` | drawn images (`bbox`, JPEG passthrough / PNG); rejects partial output above 4,096 placements, 64,000,000 cumulative pixels, or 64 MiB of payloads |
| `get_drawings()` | interpreted vector fill/stroke paths with display-space line/cubic geometry and normalized paint/stroke properties; rejects partial output above 8,192 paths, 131,072 commands, or 131,072 aggregate dash values |
| `get_pixmap(scale=, dpi=, background=, clip=)` / `render(max_size=64 MiB)` / `render_svg(max_size=64 MiB)` | bounded PNG / UTF-8 SVG rendering; `clip` uses display coordinates |
| `rotation` / `set_rotation(deg)` | display rotation |
| `mediabox` / `cropbox` / `rect` / `set_mediabox` / `set_cropbox` | page boxes |
| `insert_image(rect, filename= / stream= / pixmap=, rotate=, keep_proportion=, overlay=, max_size=64 MiB, max_pixels=64,000,000)` | draw bounded JPEG/PNG or reuse an already bounded RGBA `Pixmap`; `None` opts trusted encoded input or PNG pixels out; `rotate` turns clockwise in 90-degree steps; identical input placed again shares one image XObject |
| `show_pdf_page(rect, docsrc, pno=, keep_proportion=, overlay=)` | overlay a PDF page as vectors; `docsrc` may be the same document; placing a page of an unedited `docsrc` again shares its Form XObject |
| `insert_text(point, text, fontsize=, lineheight=, font=, fontname=, fontfile=, fontbuffer=, fontindex=, font_language=, color=, overlay=, max_font_size=64 MiB, max_text_size=1 MiB)` | bounded UTF-8 and 4,096-line standard-14 WinAnsi or shaped subset text; as in pymupdf, a string is split with `str.splitlines()` and a list holds one line per item, lines are `lineheight` times `fontsize` apart, by default the font's `ascender` minus `descender`, lines below the page are left out, and the number of lines drawn is returned; `font` draws with a loaded `Font`, installed script/locale providers are selected automatically, and `font_language` resolves ambiguous Han; `None` opts the corresponding trusted input out |
| `insert_textbox(rect, text, fontsize=, font=, fontname=, fontfile=, fontbuffer=, fontindex=, font_language=, color=, align=, expandtabs=, lineheight=, overlay=, max_font_size=64 MiB, max_text_size=1 MiB)` | bounded UAX #14 wrapping with Core 14, OpenType, a loaded `Font`, or an installed script/locale provider; lines are placed as pymupdf places them; physical and wrapped layout stop at 4,096 lines, tab expansion is preflighted, and overflow draws nothing |
| `new_shape()` | pymupdf's `Shape` for paths and text drawn in one step; see [Shapes](#shapes) |
| `draw_line` / `draw_polyline` / `draw_rect` / `draw_quad` / `draw_circle` / `draw_oval` / `draw_sector` / `draw_curve` / `draw_bezier` / `draw_squiggle` / `draw_zigzag`(geometry, *, color=(0,), fill=None, width=1, dashes=None, lineCap=0, lineJoin=0, closePath=, overlay=True, stroke_opacity=1, fill_opacity=1) | draw one finished shape and return pymupdf's end `Point`; `fill` and `closePath` where pymupdf offers them, `radius=` on `draw_rect`, `fullSector=` on `draw_sector`, and `breadth` on the wavy lines, which also follows the points positionally as in pymupdf |
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

Both methods place lines as pymupdf does. `insert_text` starts each line
`lineheight` times `fontsize` below the previous one; without `lineheight`, the
multiplier is the font's `ascender` minus its `descender`, or 1.2 when that
difference is at most 1, so Helvetica lines at size 10 lie 13.74 points apart.
As in pymupdf, the difference is compared in the single-precision metrics that
MuPDF reports, in which an ascender of 0.8 and a descender of -0.2 span just
over 1 em and keep their lines 1 em apart.
`insert_textbox` takes the same default, sets the first baseline one ascender
below the top of `rect` and each later one `lineheight` times the ascender
lower, and counts `lineheight` times `fontsize` per line plus one descender to
decide whether the text fits and to report the spare height.

`insert_text` also decides which lines to draw as pymupdf does. A string is
split with `str.splitlines()`, so a trailing line break adds no line, and `\v`,
`\f`, `\x1c` to `\x1e`, `\x85`, `\u2028`, and `\u2029` break lines as `\n`,
`\r`, and `\r\n` do; a list or tuple holds one line per item, used as given.
The first line is always drawn, and each later line only while its baseline
does not fall below the bottom of `page.rect`. The return value counts the
lines drawn, and text with nothing to draw, such as `""` or `"\n"`, inserts
nothing and returns 0. pymupdf measures the room in unrotated space down to
user-space y = 0, and pylopdf in display coordinates down to the bottom of
`page.rect`; the two agree on unrotated pages whose visible area ends at
y = 0. The font still checks every line, also those below the page.

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
pymupdf reports, which `insert_text` and `insert_textbox` place lines with: for
an OpenType face, the OS/2 typographic metrics when the face sets
`USE_TYPO_METRICS` and the `hhea` metrics otherwise, as FreeType reads them for
MuPDF, and for a Standard 14 font, the bounding box of the URW font that MuPDF
draws it with, such as 1.075 and -0.299 for Helvetica. `has_glyph(chr)`
returns the glyph number of a code point, or 0. The CJK extras install one static face per
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
glyphs; draw through a [shape](#shapes) to embed each font once per commit.

### Shapes { #shapes }

`page.new_shape()` returns pymupdf's `Shape`. `draw_line`, `draw_polyline`,
`draw_rect` (`radius=` rounds the corners), `draw_quad`, `draw_circle`,
`draw_oval`, `draw_sector`, `draw_curve`, `draw_bezier`, `draw_squiggle`, and
`draw_zigzag` add path segments in display coordinates and return pymupdf's
end point; a segment that starts where the previous one ended continues its
subpath. `finish(*, width=1, color=(0,), fill=None, lineCap=0, lineJoin=0,
dashes=None, even_odd=False, closePath=True, fill_opacity=1, stroke_opacity=1)`
strokes and fills everything drawn since the previous `finish`, with gray,
RGB, or CMYK colours from 0 to 1 and a PDF dash pattern such as `"[3 4] 0"`.
`insert_text` and `insert_textbox` take the font options of `Page.insert_text`
and `Page.insert_textbox`, `font=` and `lineheight=` included, and place lines
as those methods do. `insert_text` also splits text and leaves out the lines
below the page as `Page.insert_text` does, and returns the number of lines it
added; `insert_textbox` returns the unused height, negative when the text does
not fit and nothing is added. `commit(overlay=True)` draws the shape: the finished
paths in order and all text above them, as pymupdf does.

```python
with page.new_shape() as shape:
    shape.draw_rect((50, 50, 545, 792))
    shape.finish(color=(0.72, 0.78, 0.82), fill=(0.95, 0.96, 0.97), width=0.5)
    shape.draw_circle((80, 140), 7)
    shape.finish(color=(1, 1, 1), fill=(0.83, 0.26, 0.05), width=1.2)
    shape.insert_text((56, 80), "Inspection report", font=bold, fontsize=18)
    shape.insert_textbox((56, 160, 540, 400), body, font=regular, fontsize=10.5)
```

A commit places one Form XObject. The paths and the text in embedded fonts
are generated together, so each font is embedded once per commit however many
lines use it; draw a page's text through one shape to embed each face once per
page. The `with` block commits when it ends normally and draws nothing after
an exception, and a shape discarded with drawing that was never committed
emits a `ResourceWarning`. Where pymupdf silently drops or blackens drawing,
pylopdf raises `PdfError`: `commit()` refuses drawing that `finish()` did not
style, and `finish()` refuses a style that draws nothing (no fill, and
`color=None` or `width=0`). `morph=` and `oc=` are not available. A shape holds
at most 1,048,576 path points until it is committed; more raise `LimitError`
with code `path_point_count`. The `Page.draw_*` methods take the same options
as keyword arguments and draw one finished shape each.

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

## Low-level objects { #low-level-objects }

`doc.objects` maps object numbers to the indirect objects of the document;
`doc.trailer`, `doc.catalog`, and `page.obj` are dictionary handles. This
typed model replaces pymupdf's string-based `xref_*` methods: objects are
Python values instead of PDF syntax.

```python
from pylopdf import Name

catalog = doc.catalog
catalog["PageLayout"] = Name("TwoColumnLeft")   # a name; a plain str is a text string
font = doc.objects.add({"Type": Name("Font"), "Subtype": Name("Type1"), "BaseFont": Name("Helvetica")})
content = doc.objects.add_stream(b"BT /F9 24 Tf 72 72 Td (Hi) Tj ET")
page.obj["Resources"] = {"Font": {"F9": doc.objects[font]}}   # an indirect handle is stored as a reference
page.obj["Contents"] = doc.objects[content]
doc.objects[content].read()                    # decoded, bounded to 64 MiB by default
```

| Value | Python |
|---|---|
| null, boolean, integer, real | `None`, `bool`, `int`, `float` |
| name | `Name`, a `str` subclass without the slash |
| string | `String`, a `bytes` subclass whose `.text` decodes a PDF text string |
| array / dictionary / stream | live `Array` (a mutable sequence) / `Dictionary` (a mutable mapping) / `Stream` (a dictionary with `read(*, decode=True, max_size=64 MiB)` and `write(data, *, filter="FlateDecode")`) |

References resolve on read, and a reference to a missing object reads as
`None`. Writes take effect at once and drop rendering and extraction caches;
a write that changes the page order also invalidates existing `Page` views.
Lists and mappings are written as direct objects, an indirect handle stored in
a container becomes a reference, and assigning a handle to `doc.objects[xref]`
or passing it to `add()` copies it. Handles raise `StaleObjectError` once their
object is removed or `save(garbage=2)` and higher renumber objects. Keeping
raw edits semantically valid is the caller's responsibility.

## Module level { #module-level }

| Name | Purpose |
|---|---|
| `peek_metadata(filename=None, stream=None, password=None, *, max_file_size=None)` | fast metadata/page-count probe with optional input-size rejection and a 127-byte password boundary; `repaired` reports cross-reference recovery |
| `Permissions` | encryption permission flags (IntFlag) |
| `Rect` / `IRect` | immutable `(x0, y0, x1, y1)` NamedTuples with containment, union, intersection, transformation, and rounding |
| `Quad` | immutable `(ul, ur, ll, lr)` NamedTuple of four `Point`s with transformation, containment, and shape tests |
| `Point` | immutable `(x, y)` NamedTuple with vector arithmetic, affine transforms, distance, and direction |
| `Matrix` / `IdentityMatrix` / `Identity` | immutable affine `(a, b, c, d, e, f)` NamedTuple with composition and inversion, and the identity |
| `EMPTY_RECT()` / `INFINITE_RECT()` and their `IRECT` / `QUAD` forms, `FZ_MIN_INF_RECT` / `FZ_MAX_INF_RECT`, `planish_line(p1, p2)` | pymupdf's geometry constants and line-flattening matrix |
| `TextPage` / `TextBlock` / `TextLine` / `TextSpan` | `get_text("dict")` TypedDict hierarchy |
| `RawTextPage` / `RawTextBlock` / `RawTextLine` / `RawTextSpan` / `TextChar` | `get_text("rawdict")` TypedDict hierarchy with glyph mappings, bounds, and baseline origins (since 0.14.0) |
| `ImageInfo` / `ImageCompressionResult` / `DrawingInfo` / `AnnotationInfo` / `LinkInfo` / `FormFieldInfo` | TypedDict contracts for mapping-shaped page, document-operation, and form results |
| `PageLabelInfo` / `PageLabelSpec` | normalized page-label output / setter input contracts |
| `DocumentMetadata` / `MetadataUpdate` / `MetadataProbe` | metadata output / partial update / fast-probe contracts |
| `DocumentLimits` / `DocumentComplexity` | immutable untrusted-input budgets, including `max_interpretation_size` for the PDF snapshot and `max_text_glyphs` for positioned layout / cheap structural TypedDict |
| `OcrEngine` / `OcrWord` | reusable pure-Rust PP-OCR engine / positioned result contract |
| `Font(fontname=None, fontfile=None, fontbuffer=None, *, language=None, is_bold=False, is_italic=False, is_serif=False, weight=None, fontindex=0, family=None, allow_regular_fallback=False, max_font_size=64 MiB)` | reusable Standard 14 or OpenType font with `text_length`, `ascender` / `descender`, `has_glyph`, `name`, `family`, `weight`, `is_bold`, `is_italic`, `is_serif`, and `units_per_em`; `weight=` also selects an instance of a variable font file |
| `Shape(page)` | pymupdf's builder for paths and text drawn in one step; `page.new_shape()` creates one |
| `Name` / `String` / `Array` / `Dictionary` / `Stream` / `Objects` | low-level PDF values and live object handles ([low-level objects](#low-level-objects)) |
| `OcrRotation` / `DrawingItem` / `WordEntry` / `BlockEntry` / `FormFieldType` | runtime-importable OCR-rotation, vector-command, tuple and literal type aliases |
| `TableFinder` / `Table` / `TableDiagnostics` | owned table geometry, cell text (`None` for merged continuations), strategy and confidence evidence; `Table.to_markdown(max_size=64 MiB)` preflights escaped UTF-8 output |
| `PdfError` / `LimitError` / `PasswordError` / `OcrError` / `DocumentClosedError` / `EncryptedDocumentError` / `StalePageError` / `StaleObjectError` | exception hierarchy; limit failures expose a stable `.code` (ValueError-compatible base) |
| `Pixmap` | Immutable RGBA8 pixels: `samples` / `width` / `height` / `stride` / `n` / `tobytes(max_size=64 MiB)` / streaming, failure-atomic PNG-only `save(path)`; every wheel also supports read-only zero-copy `memoryview()` |
| `PylopdfWarning` | recoverable interpretation warnings (xref repair, font resolution, image decode) |

The `TypedDict` contracts affect static typing only; values remain ordinary
pymupdf-style dictionaries. `LinkInfo` requires `kind` and `from`, with
destination-specific optional keys. `PageLabelSpec` requires `startpage`;
`style`, `prefix`, and `firstpagenum` retain their runtime defaults.

## Point and matrix geometry { #geometry }

`Point()` creates the origin; `Point(x, y)` and `Point((x, y))` create a
position. `Matrix()` creates the zero matrix, `Matrix(sx, sy)` scales,
`Matrix(degrees)` rotates, and `Matrix(sx, sy, 1)` shears. For an explicit
transform, pass six coordinates or one six-item sequence. Coordinate keyword
arguments are supported when every coordinate is supplied. Both classes are
immutable NamedTuples: indexing, unpacking, tuple equality, hashing, and
pickling work as before, while arithmetic returns geometry values.

```python
from pylopdf import Matrix, Point

scale = Matrix(2, 3)
shift = Matrix(1, 0, 0, 1, 20, 30)
transform = scale * shift
position = Point(10, 20) * transform  # Point(x=40.0, y=90.0)
original = position / transform      # Point(x=10.0, y=20.0)
page.insert_text(position, "Transformed position")
```

Multiplication composes transforms from left to right. A point `(x, y)`
multiplied by `(a, b, c, d, e, f)` becomes `(a*x + c*y + e, b*x + d*y + f)`.
With the display-space y axis pointing down, `Matrix(90)` sends `(1, 0)`
to `(0, 1)`. `~matrix` returns its inverse, or the zero matrix if singular;
`point / matrix` and `matrix / other` raise `ZeroDivisionError` if the
divisor is singular. Finite transformations whose result cannot be represented
also raise `PdfError`.

| Operation | Result |
|---|---|
| `point + other`, `point - other` | componentwise operation with a point, two-item sequence, or scalar |
| `matrix + other`, `matrix - other` | componentwise operation with a matrix, six-item sequence, or scalar |
| `value * scalar`, `value / scalar`, `-value`, `+value` | a new value, or the same immutable value for unary `+` |
| `abs(value)`, `value.norm()` | Euclidean length of the coordinates |
| `bool(value)` | false only when every coordinate is zero |
| `point.unit`, `point.abs_unit` | unit direction, optionally with nonnegative components; the origin stays zero |
| `point.distance_to(other, unit="px")` | distance to a point or the closest location in a normalized rectangle; units `px` (PDF points), `in`, `cm`, `mm` |
| `matrix.is_rectilinear` | whether axes remain aligned, with a `1e-5` zero tolerance |

Coordinates must be finite numbers. Incorrect coordinate sequence lengths or
nonfinite values raise `PdfError`; invalid argument types raise `TypeError`.
Inputs are bounded sequences, not arbitrary iterables. Mutating methods such as `transform`,
`invert`, and `pretranslate` are absent: assign the operator's result instead.
Addition and scalar multiplication place the geometry on the left.

These are value operations, so passing a transformed point to drawing changes
its position, not the text's orientation or size. Rendering still accepts
`scale=` / `dpi=`; `get_pixmap(matrix=)` remains roadmap work.

## Rectangles and quads { #rectangles }

`Rect()` is the zero rectangle. `Rect(x0, y0, x1, y1)`, `Rect((x0, y0, x1, y1))`,
`Rect(top_left, bottom_right)`, `Rect(top_left, x1, y1)`, and
`Rect(x0, y0, bottom_right)` take coordinates or corner points. `IRect` accepts
the same arguments and rounds fractional edges outward: `x0` and `y0` down,
`x1` and `y1` up, also after arithmetic. `Quad(ul, ur, ll, lr)` takes four
points; `rect.quad` turns a rectangle into one. Every page API that returns a
rectangle returns a `Rect`, and every API that takes one also accepts a plain
four-item sequence.

```python
from pylopdf import Matrix, Point, Rect

hits = page.search_for("Total")
area = hits[0] | hits[-1]                    # union of the first and last hit
inside = [hit for hit in hits if hit in page.rect]
box = area * Matrix(1, 0, 0, 1, 0, 20)        # moved 20 points down
quad = area.morph(area.tl, Matrix(30))       # turned 30 degrees around a corner
```

| Operation | Result |
|---|---|
| `rect + other`, `rect - other` | componentwise with a rectangle, four-item sequence, or scalar |
| `rect * scalar`, `rect / scalar` | every coordinate scaled |
| `rect * matrix`, `rect / matrix` | the bounding box of the transformed corners; the infinite rectangle stays infinite |
| `rect \| point`, `rect \| other` | the smallest rectangle that also includes a point or a rectangle |
| `rect & other` | the intersection; disjoint rectangles give an invalid, empty result |
| `point in rect` | `x0 <= x < x1` and `y0 <= y < y1` |
| `other in rect`, `quad in rect` | a valid rectangle, or a quad's bounding box, within the edges, which count as inside |
| `rect.width`, `rect.height` | never negative |
| `rect.is_empty`, `rect.is_valid`, `rect.is_infinite` | `x0 >= x1` or `y0 >= y1`; `x0 <= x1` and `y0 <= y1`; equal to `INFINITE_RECT()` |
| `rect.intersects(other)` | whether two finite, nonempty rectangles share some area |
| `rect.round()`, `rect.irect` | the covering `IRect`, tolerating 0.001 of noise at each edge |
| `rect.torect(other)` | the matrix that maps one rectangle onto the other |
| `rect.morph(point, matrix)`, `quad.morph(point, matrix)` | a `Quad` transformed around a fixed point |
| `rect.normalized()` | the rectangle with ordered edges, in place of pymupdf's in-place `normalize()` |
| `abs(rect)`, `rect.get_area(unit="px")` | the area; `abs()` is 0 for an empty or infinite rectangle |
| `quad * matrix`, `quad / matrix`, `quad + other` | every corner transformed or shifted |
| `point in quad`, `rect in quad`, `other_quad in quad` | containment, edges included; for a rectangle or quad, every corner must be inside |
| `quad.rect`, `quad.width`, `quad.height` | the bounding box, and the longer edge of each opposite pair |
| `quad.is_rectangular`, `quad.is_convex`, `quad.is_empty` | right angles within 1e-5 in `rect.quad`'s turning direction; a convex outline; an edge pair shorter than 1e-5 |

pymupdf's in-place `include_point`, `include_rect`, `intersect`, `normalize`,
and `transform` are absent; assign `r | p`, `r & other`, `r.normalized()`, or
`r * m` instead. As in pymupdf, `IRect` has no `abs()` and is always true,
while a `Rect` is false only when all four coordinates are zero and a `Quad`
when it is empty. Where pymupdf decides a rectangle inside a quad by two
corners only, pylopdf checks all four.
