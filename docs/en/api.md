---
title: API reference
description: A map of pylopdf's Document, Page, Font, Shape, Pixmap, geometry types, low-level objects and exceptions.
---

# API reference

This page lists the public API by task. Every function and class also has a
docstring: run `help(pylopdf.Document)` for the details.

Conventions used throughout:

- **Page numbers start at 0**, including in outlines. Negative indexes count
  from the end.
- **Coordinates** are in points (1/72 inch) with the origin at the top left of
  the page as it is displayed, with page rotation and the visible area (the
  CropBox) applied. Extraction, search, drawing, and rendering share them.
- **Rectangles** are returned as [`Rect`](#rectangles). Wherever a rectangle
  or point is accepted, a plain tuple works too.
- **Size limits.** Methods that can read or produce a lot of data take a
  `max_size` (or similar) argument with a safe default. Pass `None` to remove
  the limit for input you trust. A refusal raises
  [`LimitError`](security.md#error-codes).
- **pymupdf names keep pymupdf's meaning.** Where pylopdf differs, the
  [migration guide](migration.md) says so.

## Document { #document }

```python
pylopdf.open(source, /, *, password=None, limits=None)
pylopdf.Document(*, limits=None)
```

`open` reads a PDF from a path (`str` or `os.PathLike`) or from the file's
contents (`bytes`, `bytearray`, or `memoryview`); read a file object first,
as in `pylopdf.open(file.read())`. `Document()` creates an empty PDF.
`limits` takes a [`DocumentLimits`](security.md#untrusted-pdfs) policy for
untrusted input. Use the document as a context manager, or call `close()`.

### Open, inspect, and save { #document-io }

| Member | Purpose |
|---|---|
| `pages` | The pages; see [Pages](#document-pages) |
| `metadata` / `set_metadata(m)` | Title, author, and the other Info fields, plus `format` and `encryption`. `set_metadata` removes keys whose value is empty, and `{}` removes all of them |
| `needs_password`, `unlock(password)` | Whether the document is locked, and unlock it: `unlock` returns `Access.USER` or `Access.OWNER` and raises `PasswordError` for a wrong password |
| `is_repaired` | Whether opening had to repair damaged cross-reference data |
| `permissions` | What the document permits, as `Permissions` flags: none while it is locked, `Permissions.ALL` for an unencrypted document or after the owner password, else the flags the encryption grants. Test one with `Permissions.PRINT in doc.permissions` |
| `is_dirty` | Whether the document was edited since it was opened. A new empty document starts edited, and saving does not reset it, as in pymupdf |
| `is_closed`, `path` | Whether the document is closed, and the file it was opened from as a `pathlib.Path`, or `None` for bytes and an empty document |
| `limits`, `complexity` | The policy the document was opened with, and cheap structural facts (pages, objects, streams) for deciding whether to process it |
| `save(path, /, *, compress=True, prune=True, dedupe=False, renumber=False, object_streams=False, encryption=None)` | Write the PDF. The file is replaced only after the complete write succeeds |
| `to_bytes(..., max_size=512 MiB)` | The same output as `bytes` |
| `close()` | Release the document; `with` does this for you |

By default, saving compresses the streams that Flate makes smaller and
removes the objects that nothing references; `compress=False` and
`prune=False` keep them as they are. `dedupe=True` merges duplicate objects
and streams, `renumber=True` numbers the remaining objects consecutively, and
`object_streams=True` packs objects into compressed object streams. These
options change the document itself, not only the output. To encrypt, pass
`encryption=Encryption(user_password=..., owner_password=...,
permissions=...)`, which writes AES-256 encryption; the owner password
defaults to the user password, and the document in memory stays unencrypted.

```python
doc.save("out.pdf", dedupe=True, object_streams=True)
doc.save(
    "locked.pdf",
    encryption=pylopdf.Encryption(user_password="secret", permissions=pylopdf.Permissions.PRINT),
)
```

### Pages { #document-pages }

`doc.pages` is a sequence of [`Page`](#page) views: `len(doc.pages)`,
`doc.pages[i]` (negative indexes count from the end), slices such as
`doc.pages[2:5]`, which return a list, and iteration.

| Member | Purpose |
|---|---|
| `doc.pages.new(size=(595, 842), *, index=None)` | Add a blank page, A4 by default, appended unless `index` is given |
| `doc.pages.insert(index, pages)`, `doc.pages.extend(pages)` | Insert or append copies of one page or a sequence of pages, from this document or another one, with their links and form fields |
| `del doc.pages[i]`, `del doc.pages[a:b]` | Delete pages |
| `doc.pages.select(indexes)` | Keep the listed pages in the listed order; repeating an index duplicates the page |
| `doc.pages.move(index, to)` | Move a page so that it ends at index `to` |
| `doc.pages.labeled(label)` | The indexes of the pages labeled `label` |
| `get_outline()` / `set_outline(entries)` | Read or replace the table of contents as `OutlineEntry(level, title, page)` items |
| `get_page_labels()` / `set_page_labels(labels)` | Page label ranges, such as roman numerals for front matter |

```python
merged = pylopdf.Document()
for path in ["a.pdf", "b.pdf"]:
    merged.pages.extend(pylopdf.open(path).pages)
del merged.pages[0]                         # drop the first page
merged.pages.insert(0, merged.pages[-1])    # copy the last page to the front
merged.pages.select(range(len(merged.pages) - 1, -1, -1))  # reverse the order
```

`insert` and `extend` take index positions as `list.insert` does, so an index
past the end appends. The pages of one call come from one document, and a
page of another document appears at most once per call. Pages of another
document are imported with their links, annotations, and form fields; a field
name already in use gains the smallest unused `+N` suffix. A copy of a page of
the same document is a new page object that shares its content streams and
resources with the original until either is edited.

Adding, deleting, or reordering pages makes earlier `Page` views stale: using
one raises `StalePageError`, and iterating over `doc.pages` while changing
them raises `RuntimeError`. Deleting or selecting pages also removes the
links, outline entries, form fields, and tagged-PDF structure that pointed
only to the removed pages. It does not remove every copy of their content, so
it is not secure redaction.

### Text and Markdown { #document-text }

| Member | Purpose |
|---|---|
| `get_text(pages=None)` | Plain text of every page, or of the listed pages, as one string |
| `to_markdown(pages=None, *, table_strategy="lines", max_size=64 MiB)` | Markdown with headings, lists, emphasis, multi-column order, vertical CJK, and tables |

`table_strategy="lines"` converts tables drawn with ruling lines,
`"text"` also converts borderless tables that are clearly aligned, and `None`
turns table conversion off.

### Rendering and images { #document-rendering }

| Member | Purpose |
|---|---|
| `render_pages(pages=None, *, matrix=, dpi=, alpha=False, background=, workers=, max_size=512 MiB)` | PNG bytes for every page, or the listed pages, in order, rendered in parallel |
| `extract_image(xref)` | One image with pymupdf's keys; JPEG data is returned as is and other images as PNG |
| `rewrite_images(*, dpi_threshold=None, dpi_target=0, quality=0, ...)` | Downsample and recompress images to make the file smaller |
| `set_fallback_font(font, kind="sans", index=0, *, font_language="ja")` | Font used to display Chinese, Japanese, or Korean text whose font is not embedded; the CJK extras set this up automatically |

`rewrite_images` follows pymupdf's keywords: images with more than
`dpi_threshold` dots per inch where they are drawn are resampled to
`dpi_target`, and `quality` (1–100) re-encodes them as JPEG. It changes only
plain 8-bit gray or RGB images without masks, keeps an image whose new
encoding would not be smaller, and returns statistics.

### Forms, attachments, and other data { #document-data }

| Member | Purpose |
|---|---|
| `get_form_fields()` | Every form field with its name, type, value, and options |
| `set_form_field(name, value, *, font=, fontfile=, fontbuffer=, fontindex=, font_language=)` | Fill a field and regenerate how it looks; see [Forms](#forms) |
| `attachments` | Embedded files as a mapping; see [Attachments](#attachments) |
| `get_pdfa_claim()` | The PDF/A part and conformance the file declares, such as `(2, "B")`. This reads the declaration only; it does not validate |
| `get_xml_metadata(*, max_size=1 MiB)` / `set_xml_metadata(metadata, *, max_size=1 MiB)` / `del_xml_metadata()` | The XMP metadata packet as text (`""` when there is none). `set_metadata` does not change it |
| `language_tag` / `set_language_tag(tag)` | The document's language as a complete BCP 47 tag, such as `zh-TW` or `sr-Latn-RS`, or `None`. `set_language_tag(None)` removes it, and a malformed tag raises |
| `pagemode` / `set_pagemode(mode)`, `pagelayout` / `set_pagelayout(layout)` | How a viewer opens the document, such as `"UseOutlines"` or `"TwoColumnLeft"`; `"UseNone"` and `"SinglePage"` when unset |
| `markinfo` / `set_markinfo(markinfo)` | The tagged-PDF flags as a `MarkInfo` dictionary (`{}` when absent). The setter writes all three flags, and a flag left out becomes `False` |
| `has_links()`, `has_annots()` | Whether any page has a link, or an annotation other than a link or form widget |
| `objects`, `trailer`, `catalog` | Direct access to PDF objects; see [Low-level objects](#low-level-objects) |

`language_tag` replaces pymupdf's `language`, which keeps at most three
letters of the tag, so `zh-TW` and `zh-HK` both read as `zh-Hant`. Writing XMP
leaves the packet uncompressed even when saving compresses streams, so tools
that search files for it still find it.

## Page { #page }

A `Page` is a view of one page of its document. Fetch it with `doc.pages[i]`.
Two views are equal when they view the same page of the same document and
no page was added, deleted, or reordered in between.

### Text and search { #page-text }

| Member | Purpose |
|---|---|
| `get_text(option="text")` | `"text"`, `"words"`, `"blocks"`, `"dict"`, or `"rawdict"` |
| `search_for(text, *, max_hits=4096)` | Case-insensitive search; returns a list of `Rect` |
| `find_tables(*, clip=None, strategy=None)` | Tables as a `TableFinder`; see [Tables](#tables) |
| `to_markdown(*, table_strategy="lines")` | Markdown for this page |
| `get_text_ocr(...)`, `apply_ocr(...)`, `insert_ocr_text_layer(words)` | OCR; see [Offline OCR](ocr.md) |
| `content(*, clip=None, annotations=True)` | Every text run, path, and image the page paints; see [Page content](#content) |
| `get_drawings()` | Vector paths; see [Vector drawings](#drawings) |
| `get_links()` | Links with their targets |
| `get_label()` | The page label, such as `"iv"` |

`"words"` returns `Word` tuples `(x0, y0, x1, y1, word, block, line, word_no)`
and `"blocks"` returns `Block` tuples, both in pymupdf's order. `"dict"`
returns blocks, lines, and spans with their bounding boxes, font, size, and
style flags. `"rawdict"` adds each glyph; see
[Positioned text](#positioned-text).

Reading order follows lines and columns, including right-to-left text and
vertical Japanese and Chinese. Ruby, warichu, and text in mixed orientations
are not interpreted.

### Rendering { #page-rendering }

| Member | Purpose |
|---|---|
| `get_pixmap(*, matrix=None, dpi=None, clip=None, alpha=False, background=None)` | Render to a [`Pixmap`](#pixmap) |
| `get_svg_image(*, matrix=None, text_as_path=True)` | Render to an SVG string |
| `get_images(*, full=False)` | The image objects the page uses, as `ImageItem` tuples |
| `get_image_info(*, hashes=False, xrefs=False)` | Where each image is drawn, with its size and transform |

As in pymupdf, a pixmap is RGB on white by default and RGBA with
`alpha=True`. `dpi` takes precedence over `matrix`, which must be a scaling
matrix such as `Matrix(2, 2)`. `clip` renders only part of the page.

### Page geometry { #page-geometry }

| Member | Purpose |
|---|---|
| `index`, `document` | The page's index in its document, and the document |
| `rect` | The visible page area, with rotation applied |
| `rotation` / `set_rotation(deg)` | Display rotation in multiples of 90 |
| `mediabox`, `cropbox` / `set_mediabox(r)`, `set_cropbox(r)` | Page boxes in pymupdf's convention |
| `xref`, `obj` | The page object's number and a [low-level handle](#low-level-objects) |

### Adding content { #page-content }

| Member | Purpose |
|---|---|
| `insert_text(point, text, ...)` | Write text starting at a point; see [Text and fonts](#fonts) |
| `insert_textbox(rect, text, ...)` | Write wrapped text inside a rectangle |
| `insert_image(rect, *, filename=, stream=, pixmap=, rotate=0, keep_proportion=True, overlay=True)` | Draw a JPEG, PNG, or `Pixmap`; returns its object number |
| `show_pdf_page(rect, docsrc, pno=0, *, keep_proportion=True, overlay=True)` | Draw a page of another PDF, or of this one, as vectors |
| `draw_line`, `draw_rect`, `draw_circle`, `draw_oval`, `draw_polyline`, `draw_quad`, `draw_sector`, `draw_curve`, `draw_bezier`, `draw_squiggle`, `draw_zigzag` | Draw one shape; style options such as `color`, `fill`, `width`, and `dashes` are keyword-only |
| `draw(*, underlay=False)` | A [canvas](#canvas) that draws paths, text, images, and pages in order |
| `new_shape()` | Draw several paths and text runs at once; see [Shapes](#shapes) |
| `replace_text(search, replacement, *, default_char=None)` | Replace text drawn in simple (single-byte) fonts |

The same image or page drawn again is stored once in the file.

### Annotations and links { #page-annotations }

| Member | Purpose |
|---|---|
| `annots(*, types=None)` | Read-only `Annot` objects with `type`, `rect`, `info`, `colors`, `flags`, and `xref` |
| `add_highlight_annot(quads, *, color=, opacity=0.4, content=None)` | Highlight one or more rectangles or quads, such as search results |
| `insert_link(link)` | Add a web link (`kind=LINK_URI`) |

Annotation types and flags are the `PDF_ANNOT_*` and `PDF_ANNOT_IS_*`
constants.

## Positioned text and glyphs { #positioned-text }

`page.get_text("rawdict")` keeps the `"dict"` structure but replaces each
span's `text` with a `chars` list:

| Key | Meaning |
|---|---|
| `c` | The text of one glyph; a ligature such as "fi" keeps both letters |
| `bbox` | Approximate bounds `(x0, y0, x1, y1)` |
| `origin` | Where the glyph's baseline starts, `(x, y)` |

```python
with pylopdf.open("report.pdf") as doc:
    layout = doc.pages[0].get_text("rawdict")
    for block in layout["blocks"]:
        for line in block["lines"]:
            for span in line["spans"]:
                for char in span["chars"]:
                    print(char["c"], char["bbox"], char["origin"])
```

Bounds are estimated from the baseline and font size, not from the glyph
outlines. Spaces that pylopdf infers from gaps are not added as entries, so
joining the `c` values does not always reproduce `get_text()`. Glyphs that have
no Unicode mapping in the PDF are skipped with a `PylopdfWarning`.

The structure resembles
[pymupdf's rawdict](https://pymupdf.readthedocs.io/en/latest/textpage.html#structure-of-dictionary-outputs)
but returns text blocks only, without image or vector blocks and without
color, opacity, or extra font metrics. The
[overlay example](https://github.com/yhay81/pylopdf/blob/main/examples/rawdict-overlay.py)
draws glyph bounds and origins as an HTML preview.

## Page content { #content }

`page.content()` returns what the page paints, in paint order, as hayro
interprets it: a `Content` sequence of `TextRun`, `Path`, and `Image` items in
display coordinates. Every value is immutable.

```python
with pylopdf.open("report.pdf") as doc:
    content = doc.pages[0].content()
    for run in content.text_runs:
        print(run.text, run.font.name, run.size, run.fill)
        for glyph in run.glyphs:
            print(glyph.text, glyph.origin, glyph.bbox)
    for path in content.paths:
        print(path.bbox, path.fill, path.stroke)
    for image in content.images:
        print(image.xref, image.bbox, image.width, image.height)
```

| Item | Fields |
|---|---|
| `TextRun` | `glyphs`, `font`, `render_mode`, `fill`, `stroke`, and `bbox`; `text` and `size` read them |
| `Glyph` | `glyph_id`, `text`, `matrix`, `advance`, and `bbox`; `origin` and `size` read the matrix |
| `Path` | `segments` (`MoveTo`, `LineTo`, `CurveTo`, and `ClosePath`), `fill`, `fill_rule`, `stroke`, and `bbox` |
| `Image` | `xref`, `width`, `height`, `bits`, `colorspace`, `components`, `is_stencil`, `mask`, `encoded_size`, `resolution`, `matrix`, and `bbox` |

Every item also has its `blend_mode`, its `soft_mask`, and a `context`: the
`GraphicsContext` of `clips`, transparency `groups`, and `marked_content`
around it, outermost first, whose `opacity` and `clip_bbox` combine them.
Items painted in the same context share one context object.

A paint is a `Color` (sRGB with alpha), a `ShadingPattern`, or a
`TilingPattern`. A `Stroke` has its `paint`, `width`, `cap`, `join`,
`miter_limit`, `dashes`, and `dash_offset`, scaled to display space. A text
run's `font` is a `FontRef` with the font program's `name`, `weight`, and
style flags.

- Glyph boxes are the exact boxes of the glyph outlines, so a space has none;
  a Type 3 glyph's box is where its procedure paints.
- Text in a clipping render mode is painted as its fill or stroke, and text
  that only clips is not an item. A fill directly followed by a stroke of the
  same path, as `B` paints it, is one path.
- A shading painted with `sh` is a `Path` that covers the clipped area and
  fills with a `ShadingPattern`.
- hayro paints every image inside a transparency group that holds the
  opacity, blend mode, and soft mask in force, so an image's context ends
  with that group. A clip that covers the whole visible area is left out, as
  hayro leaves it out.

`clip=` keeps the items whose box meets a rectangle, and `annotations=False`
leaves out annotation appearances. One call reads at most `max_elements`
glyphs, path and clip segments, and items, 262,144 by default.

What hayro does not report yet is not in the model: the color space and
components of a color, layer names and the other marked-content properties,
the font dictionary behind a `FontRef`, and the object of an image mask. They
are listed as
[engine work](https://github.com/yhay81/pylopdf/blob/main/ROADMAP.md#engine-work).

## Vector drawings { #drawings }

`page.get_drawings()` returns one `DrawingInfo` dictionary per filled or
stroked path, in pymupdf's format: `type` (`"f"`, `"s"`, or `"fs"`), `items`
(lines and cubic curves), `rect`, colors, opacity, line width, cap, join,
dashes, and `seqno`. Paths filled with a pattern have `None` colors. The paths
of [`page.content()`](#content) also carry their clips, groups, and marked
content.

## Canvas { #canvas }

`page.draw()` returns a `Canvas`. Use it in a `with` block: everything drawn
inside is written as one Form XObject when the block ends, in the order of
the calls, and nothing is written if the block raises an exception.
Coordinates are display coordinates, as everywhere else.

```python
from pylopdf import Color, Matrix, PathBuilder, Stroke

navy = Color(0.1, 0.2, 0.4)
with page.draw() as canvas:
    canvas.rect((40, 40, 555, 120), radius=8, fill=Color(0.95, 0.96, 0.97))
    canvas.text("Inspection report", (56, 90), size=20, fill=navy)
    canvas.line((56, 105), (539, 105), stroke=Stroke(paint=navy, width=0.5))
    fit = canvas.textbox((56, 140, 539, 400), body, size=10.5)
    canvas.image((400, 420, 539, 520), "chart.png")
    with canvas.opacity(0.15), canvas.transform(Matrix(1, 0, 0, 1, 0, 600)):
        canvas.page((56, 0, 539, 200), letterhead[0])
```

| Method | Draws |
|---|---|
| `path(path, *, fill=None, stroke=None, fill_rule=FillRule.NONZERO)` | A `PathBuilder`, a `Path` item's geometry, or a list of segments |
| `line`, `rect(rect, *, radius=0)`, `circle`, `ellipse`, `polygon`, `bezier` | One path each, with the options of `path` |
| `text(text, at, *, font=None, size=11, fill=..., stroke=None, line_height=None)` | Lines whose first baseline starts at `at` |
| `textbox(rect, text, *, font=None, size=11, align="left", line_height=None, ...)` | Text wrapped inside `rect`; returns a `TextFit` |
| `image(rect, source, *, keep_aspect=True, rotate=0)` | A JPEG or PNG path, encoded bytes, or a `Pixmap` |
| `page(rect, source, *, keep_aspect=True)` | A page of any open document, this one included |
| `draw(item)` | A `Path` item that [`page.content()`](#content) returned |

Paths take a `Color` fill and a stroke: a `Stroke` with width, cap, join,
miter limit, and dashes, or a `Color` for a 1-point line. A path with neither
is stroked with a 1-point black line. A color's `alpha` makes that fill or
stroke translucent. `PathBuilder` builds geometry with `move_to`, `line_to`,
`curve_to`, `quad_to`, and `close`, and adds complete shapes with `rect`,
`ellipse`, `circle`, `polygon`, `sector`, `zigzag`, and `wave`; every method
returns the builder, so calls chain.

Text is drawn in a [`Font`](#font). An OpenType face is shaped with HarfRust
and embedded as a subset, once per canvas however many calls use it. Without
`font`, Western European text is drawn in Helvetica and other text in an
installed [font extra](getting-started.md#installation) for its script. Text is
filled, stroked, or both. `textbox` breaks lines at Unicode line-break
opportunities; when the text does not fit, it draws nothing and
`TextFit.spare_height` is the negative shortfall, so `fit.fits` is false.

Four context managers apply to everything drawn inside them, and nest:

| Scope | Effect |
|---|---|
| `clip(path, *, fill_rule=...)` | Paints only inside the path |
| `transform(matrix)` | Transforms the drawing; nested transforms apply innermost first |
| `opacity(alpha)` | Composites the drawing as one group, then paints it at `alpha` |
| `blend(mode)` | Blends the drawing with what is below it, with a `BlendMode` |

`page.draw(underlay=True)` writes below the page's existing content. An image
or page that a canvas draws several times, or that the document placed
before, is stored once. A page is drawn as it was when `canvas.page()` was
called. `draw(item)` repeats an item's clips, group opacities, and blend
modes, but not its soft masks; text runs, images, and pattern paints cannot
be drawn yet. A canvas holds at most 1,048,576 path points and nests at most
16 scopes; beyond that, `LimitError` has the code `path_point_count` or
`canvas_scope_depth`.

## New documents { #create }

`pylopdf.create()` starts a document that krilla writes as a whole.
`builder.page(size)` returns a [canvas](#canvas) for a new page, which joins
the document when its `with` block ends; `builder.to_bytes()` returns the
file and `builder.finish()` opens it as a `Document`.

```python
from pathlib import Path

import pylopdf
from pylopdf import Color, TagRole

font = pylopdf.Font(fontfile="NotoSans-Regular.ttf")
builder = pylopdf.create(standard="pdf/ua-1", language="en", title="Annual report")
with builder.page((595.28, 841.89)) as canvas:
    canvas.bookmark("Summary")
    with canvas.tag(TagRole.HEADING_1, title="Summary"):
        canvas.text("Summary", (56, 80), font=font, size=20)
    with canvas.tag(TagRole.PARAGRAPH):
        canvas.textbox((56, 100, 539, 300), body, font=font, size=11)
    with canvas.tag(TagRole.FIGURE, alt="Revenue by quarter"):
        canvas.image((56, 320, 300, 480), "chart.png")
    canvas.line((56, 800), (539, 800), stroke=Color(0.6, 0.6, 0.6))
Path("report.pdf").write_bytes(builder.to_bytes())
```

| Argument of `create()` | Meaning |
|---|---|
| `standard` | `"pdf/a-1a"` through `"pdf/a-4f"`, `"pdf/ua-1"`, or several, such as `("pdf/a-2a", "pdf/ua-1")` |
| `tagged` | Build a structure tree from `tag` scopes; PDF/UA and the PDF/A `a` levels imply it |
| `language`, `title`, `author`, `subject`, `keywords` | The document's BCP 47 language and metadata |
| `created` | The creation date, the current time by default; a fixed value makes the output reproducible |

Writing a document with a `standard` validates it, and a violation raises
`PdfError` that lists every problem krilla finds, such as transparency in
PDF/A-1 or a missing title or outline in PDF/UA, instead of writing the file.
Requirements that need judgment, such as meaningful alternate text, remain
yours.

A new document's canvas draws what a page's canvas draws, with these
differences:

- Every font is embedded, so text needs an OpenType [`Font`](#font): text
  without `font` uses an installed font extra for its script, and Western
  European text needs `font` until krilla draws the Standard 14 fonts.
- `canvas.page()` cannot place other pages yet; draw them on the finished
  document's pages.
- `tag(role, *, alt=None, lang=None, title=None, actual_text=None)` makes
  what the block draws a structure element. Roles are the `TagRole` members,
  such as `PARAGRAPH`, `HEADING_1` to `HEADING_6`, `LIST` and `LIST_ITEM`,
  `TABLE`, `TABLE_ROW`, and `TABLE_DATA`, and `FIGURE` with its `alt` text.
  Scopes nest, each line of text inside one is a span of it, and content
  outside every scope, or inside an `ARTIFACT` scope, is an artifact.
- `bookmark(title, *, level=1, at=(0, 0))` adds an outline entry that shows
  `at` on the page; a level can grow by one at a time.

`finish()` and `to_bytes()` can be called again after more pages. Saving the
`Document` that `finish()` returns writes it again with lopdf, so keep
`to_bytes()` for the file exactly as krilla validated it. A document holds at
most 65,536 pages and 65,536 bookmarks, and a tag's texts or a bookmark title
at most 64 KiB (`tag_text_size`).

## Tables { #tables }

`page.find_tables()` finds tables drawn with ruling lines, including merged
cells. `strategy="text"` also detects borderless tables when rows and columns
are clearly aligned; it is not the default because aligned multi-column prose
can look like a table. `clip=` keeps only tables entirely inside a rectangle.

The result is a `TableFinder`; its `tables` are `Table` objects with `bbox`,
`row_count`, `col_count`, `cells`, `extract()` (cell text by row, `None` for
the cells a merged cell covers), and `to_markdown()`. `Table.confidence` is a
0–1 score for ranking candidates, not a probability, and
`Table.diagnostics` explains it.

## Text and fonts { #fonts }

### Writing text { #writing-text }

```python
page.insert_text(point, text, *, fontsize=11, lineheight=None, font=None,
                 fontname="helv", fontfile=None, fontbuffer=None, fontindex=0,
                 font_language=None, color=(0, 0, 0), overlay=True)
page.insert_textbox(rect, text, *, align=pylopdf.TEXT_ALIGN_LEFT,
                    expandtabs=1, ...)   # same font options
```

Choose the font with one of these arguments:

- nothing, or `fontname`: a font from an installed extra when the text needs
  one, otherwise a Standard 14 font (`"helv"` by default, or for example
  `"Times-Bold"`). Standard 14 fonts are not embedded and cover Western
  European (WinAnsi) characters only;
- `fontfile` or `fontbuffer`: an OpenType font, of which only the glyphs used
  are embedded;
- `font`: a [`Font`](#font) you have already loaded.

One font must contain every character of the text; pylopdf does not switch
fonts per character. Text in Kana, Hangul, Bopomofo, Arabic, Hebrew,
Devanagari, or Thai picks the matching installed extra automatically. Chinese
characters alone are ambiguous, so they use the Japanese font by default; pass
`font_language="zh-CN"`, `"zh-TW"`, `"ko"`, or `"ja"` to choose.

`insert_text` splits a string into lines with `str.splitlines()`, or takes a
list with one line per item. Lines are `lineheight × fontsize` apart; by
default `lineheight` comes from the font's ascender and descender, as in
pymupdf. Lines that would fall below the bottom of the page are skipped, and
the method returns the number of lines drawn.

`insert_textbox` wraps text at the places Unicode allows a line break
(UAX #14), keeps explicit line breaks, expands tabs, and splits a word that is
too long for a line. `align` takes `TEXT_ALIGN_LEFT`, `TEXT_ALIGN_CENTER`,
`TEXT_ALIGN_RIGHT`, or `TEXT_ALIGN_JUSTIFY`. It returns the height left over;
a negative value means the text did not fit, and then nothing is drawn.

Right-to-left text is shaped correctly. When the PDF is read back, lines that
are entirely right-to-left, or that contain one Latin word or number inside
right-to-left text, come out in logical order; other mixed-direction lines
come out in visual order. Some complex-script clusters, especially in long
Devanagari runs, can extract slightly differently from the original text.

### Font { #font }

```python
pylopdf.Font(fontname=None, fontfile=None, fontbuffer=None, *, language=None,
             is_bold=False, is_italic=False, is_serif=False, weight=None,
             fontindex=0, family=None, allow_regular_fallback=False)
```

| Example | Loads |
|---|---|
| `Font("helv")`, `Font("Times-Bold")` | A Standard 14 font |
| `Font(fontfile="NotoSans-Regular.ttf")` | An OpenType file |
| `Font(fontfile="NotoSansCJKjp-VF.otf", weight=700)` | One weight of a variable font |
| `Font(language="ja")`, `Font(family="Noto Sans JP")` | A font from an installed extra; `is_serif=True` picks the serif face |
| `Font(language="he", is_bold=True)` | The bold weight of an installed variable font |

Methods and properties: `text_length(text, fontsize=11)` (the width as drawn,
including kerning), `ascender`, `descender`, `has_glyph(chr)`, `name`,
`family`, `weight`, `is_bold`, `is_italic`, `is_serif`, and `units_per_em`.

A variable font loaded without `weight` uses its default instance, which is
not always the regular weight: the variable Noto Sans CJK JP defaults to Thin.
Pass `weight=400` for regular. The `fontfile` and `fontbuffer` arguments of the
drawing methods use the default instance too; pass a `Font` as `font=` to draw
another weight.

The Arabic, Hebrew, Hindi, and Thai extras ship variable fonts, so any weight
on their axis works (100–900; 400–700 for Noto Naskh Arabic). The CJK extras
ship one regular face per style: `Font(language="ja", is_bold=True)` raises
`PdfError`, so load a bold file with `fontfile=` instead, or pass
`allow_regular_fallback=True` to accept the regular face with a warning.
pylopdf never imitates bold or italic by distorting glyphs.

pylopdf does not search system font directories and has no global font
registry, so the output depends only on the fonts you pass in.

### Shapes { #shapes }

`page.new_shape()` returns a `Shape`, pymupdf's tool for drawing several paths
and text runs as one unit:

```python
regular = pylopdf.Font("helv")
bold = pylopdf.Font("hebo")
body = "Findings are listed below. " * 20

with page.new_shape() as shape:
    shape.draw_rect((50, 50, 545, 792))
    shape.finish(color=(0.72, 0.78, 0.82), fill=(0.95, 0.96, 0.97), width=0.5)
    shape.draw_circle((80, 140), 7)
    shape.finish(color=(1, 1, 1), fill=(0.83, 0.26, 0.05), width=1.2)
    shape.insert_text((56, 80), "Inspection report", font=bold, fontsize=18)
    shape.insert_textbox((56, 160, 540, 400), body, font=regular, fontsize=10.5)
```

- `draw_*` methods add path segments and return the end point; a segment that
  starts where the previous one ended continues the same path.
- `finish(*, width=1, color=(0,), fill=None, lineCap=0, lineJoin=0,
  dashes=None, even_odd=False, closePath=True, fill_opacity=1,
  stroke_opacity=1)` styles everything drawn since the previous `finish`.
  Colors are gray, RGB, or CMYK tuples from 0 to 1.
- `insert_text` and `insert_textbox` work like the `Page` methods.
- `commit()` draws the shape: the paths in order, then all text on top. The
  `with` block commits when it ends without an exception.

Each font is embedded once per commit, however many lines use it, so drawing a
page's text through one shape keeps the file small. Unlike pymupdf, pylopdf
raises `PdfError` instead of silently dropping or blackening drawing:
`commit()` refuses paths that `finish()` did not style, and `finish()` refuses
a style that draws nothing. `morph=` and `oc=` are not supported yet.

### Forms { #forms }

`set_form_field` fills text, combo box, list box, check box, and radio button
fields, and regenerates how each one looks. Western European (WinAnsi) text
is sized to fit in Helvetica. For other characters, pass `fontfile`, `fontbuffer`, or
a loaded `font`, or install a font extra. Comb fields, alignment, and maximum
lengths are respected; a value that is too long raises and leaves the document
unchanged. Rich text, push button actions, and signatures are not supported;
see [ecosystem recipes](ecosystem.md#signatures) for signing.

## Attachments { #attachments }

`doc.attachments` is a mapping from names to `Attachment` objects:

```python
doc.attachments.add("data.csv", csv_bytes, description="Source data")
for name in doc.attachments:
    item = doc.attachments[name]
    print(item.filename, item.size, item.description)
data = doc.attachments["data.csv"].read()
doc.attachments["data.csv"].write(new_bytes)
del doc.attachments["data.csv"]
```

`Attachment` also has `name`, `creation_date`, and `mod_date`. A missing name
raises `KeyError`.

## Low-level objects { #low-level-objects }

`doc.objects` maps object numbers to the PDF's objects, and `doc.trailer`,
`doc.catalog`, and `page.obj` are dictionary handles. Objects are Python
values rather than PDF syntax:

```python
from pylopdf import Name

catalog = doc.catalog
catalog["PageLayout"] = Name("TwoColumnLeft")   # a name; a plain str is a text string
font = doc.objects.add({"Type": Name("Font"), "Subtype": Name("Type1"), "BaseFont": Name("Helvetica")})
content = doc.objects.add_stream(b"BT /F9 24 Tf 72 72 Td (Hi) Tj ET")
page.obj["Resources"] = {"Font": {"F9": doc.objects[font]}}   # stored as a reference
page.obj["Contents"] = doc.objects[content]
doc.objects[content].read()                     # decoded stream data
```

| PDF value | Python value |
|---|---|
| null, boolean, integer, real | `None`, `bool`, `int`, `float` |
| name | `Name`, a `str` without the leading slash |
| string | `String`, `bytes` whose `.text` decodes it |
| array, dictionary | `Array` (a mutable sequence), `Dictionary` (a mutable mapping) |
| stream | `Stream`, a dictionary with `read(*, decode=True)` and `write(data, *, filter="FlateDecode")` |

Changes apply immediately. A reference to a missing object reads as `None`.
Storing a handle inside another object stores a reference to it. Handles raise
`StaleObjectError` once their object is removed, or after
`save(renumber=True)` renumbers objects. pylopdf does not check that your edits
make sense as PDF.

## Geometry { #geometry-types }

`Point`, `Matrix`, `Rect`, `IRect`, and `Quad` are immutable named tuples, so
indexing, unpacking, and comparison work as for tuples. Operators return new
values; pymupdf's in-place methods such as `transform()` and `normalize()` do
not exist, so assign the result instead (`r = r * m`). All coordinates must be
finite numbers.

### Point and Matrix { #geometry }

`Point(x, y)` is a position. `Matrix(a, b, c, d, e, f)` is an affine
transform; `Matrix(sx, sy)` scales, `Matrix(degrees)` rotates, and
`Matrix(sx, sy, 1)` shears.

```python
from pylopdf import Matrix, Point

scale = Matrix(2, 3)
shift = Matrix(1, 0, 0, 1, 20, 30)
transform = scale * shift            # scale first, then shift
position = Point(10, 20) * transform  # Point(x=40.0, y=90.0)
original = position / transform      # Point(x=10.0, y=20.0)
```

| Operation | Result |
|---|---|
| `p + q`, `p - q`, `p * 2`, `-p` | Vector arithmetic |
| `p * m` | The transformed point: `(a*x + c*y + e, b*x + d*y + f)` |
| `m1 * m2` | `m1` applied first, then `m2` |
| `~m` | The inverse (the zero matrix if `m` cannot be inverted) |
| `p / m`, `m1 / m2` | Multiply by the inverse; raises `ZeroDivisionError` if it does not exist |
| `abs(p)`, `p.unit`, `p.distance_to(other, unit="px")` | Length, direction, and distance (`px`, `in`, `cm`, `mm`) |
| `m.is_rectilinear` | Whether the transform keeps edges horizontal and vertical |

Because y points down, `Matrix(90)` turns `(1, 0)` into `(0, 1)`.
`IdentityMatrix` (also `Identity`) is the identity.

### Rect, IRect, and Quad { #rectangles }

`Rect(x0, y0, x1, y1)` also accepts a tuple, or corner points as in
`Rect(top_left, bottom_right)`. `IRect` takes the same arguments and rounds
outward to whole numbers. `Quad(ul, ur, ll, lr)` is any four-cornered shape,
such as a rotated rectangle; `rect.quad` and `quad.rect` convert between them.

```python
from pylopdf import Matrix

hits = page.search_for("Total")
area = hits[0] | hits[-1]                    # covers the first and last hit
inside = [hit for hit in hits if hit in page.rect]
box = area * Matrix(1, 0, 0, 1, 0, 20)        # moved 20 points down
quad = area.morph(area.tl, Matrix(30))       # turned 30 degrees around a corner
```

| Operation | Result |
|---|---|
| `r | p`, `r | other` | The smallest rectangle that also covers a point or rectangle |
| `r & other` | The overlap |
| `r * m`, `r / m` | The bounding box of the transformed rectangle |
| `r + other`, `r * 2` | Coordinate arithmetic |
| `p in r`, `other in r` | Containment |
| `r.width`, `r.height`, `abs(r)`, `r.get_area(unit="px")` | Size and area |
| `r.is_empty`, `r.is_valid`, `r.is_infinite`, `r.intersects(other)` | Tests |
| `r.normalized()` | The rectangle with `x0 <= x1` and `y0 <= y1` |
| `r.round()`, `r.irect` | The covering `IRect` |
| `r.torect(other)` | The matrix that maps one rectangle onto another |
| `r.morph(point, m)`, `q.morph(point, m)` | A `Quad` transformed around a fixed point |
| `q * m`, `p in q`, `q.is_rectangular`, `q.is_convex` | Quad transforms and tests |

`EMPTY_RECT()`, `INFINITE_RECT()`, their `IRECT` and `QUAD` forms, and
`planish_line(p1, p2)` are pymupdf's helpers. Unlike pymupdf, checking whether
a rectangle lies inside a quad tests all four corners.

## Pixmap { #pixmap }

`Pixmap` holds the pixels of a rendered page and cannot be changed.

| Member | Purpose |
|---|---|
| `width`, `height`, `n`, `alpha`, `stride`, `colorspace` | Size and layout: `n` is 3 for RGB and 4 for RGBA |
| `samples` | The pixels as `bytes` |
| `memoryview(pix)`, `np.frombuffer(pix, dtype=np.uint8)` | The pixels without copying |
| `tobytes()` | PNG bytes |
| `save(filename)` | Write a PNG file |

## Module functions and types { #module-level }

| Name | Purpose |
|---|---|
| `open(source, /, *, password=None, limits=None)` | Open a document; see [Document](#document) |
| `probe(source, /, *, password=None, max_file_size=None)` | A `Probe` record with `page_count`, `version`, `needs_password`, `is_repaired`, and `metadata` (`title`, `author`, `subject`, `keywords`, `creator`, `producer`, `created`, and `modified` when present), read without loading the document |
| `DocumentLimits`, `DocumentLimits.web()` | Resource limits for untrusted input; see [Security](security.md#untrusted-pdfs) |
| `OcrEngine` | A reusable OCR engine; see [Offline OCR](ocr.md) |
| `Canvas`, `PathBuilder`, `TextFit` | See [Canvas](#canvas) |
| `create(...)`, `Builder`, `Standard`, `TagRole` | See [New documents](#create) |
| `Font`, `Shape` | See [Font](#font) and [Shapes](#shapes) |
| `Base14_fontnames`, `Base14_fontdict` | The Standard 14 font names that `fontname=` accepts |
| `Name`, `String`, `Array`, `Dictionary`, `Stream`, `Objects` | [Low-level objects](#low-level-objects) |
| `Content`, `TextRun`, `Path`, `Image`, `Glyph`, `FontRef`, `Color`, `Stroke`, `GraphicsContext`, and the other content types | The [content model](#content) |
| `Colorspace`, `csRGB`, `csGRAY`, `csCMYK` | pymupdf's device color spaces |

Constants keep pymupdf's names and are also grouped in enums:
`Permissions` (`PDF_PERM_*`), `LinkKind` (`LINK_*`), `TextAlign`
(`TEXT_ALIGN_*`), `AnnotType` (`PDF_ANNOT_*`), and `AnnotFlags`
(`PDF_ANNOT_IS_*`). `Access` has the members `USER` and `OWNER`, and
`Encryption` describes how to encrypt a saved file. The content model's enums
are `BlendMode`, `FillRule`, `LineCap`, `LineJoin`, `TextRenderMode`,
`ShadingType`, `SoftMaskType`, and `ImageMask`.

Dictionaries returned by the API are plain `dict` objects, described for type
checkers by `TypedDict` classes such as `TextPageDict`, `RawTextPageDict`,
`DrawingInfo`, `ImageInfo`, `LinkInfo`, `FormFieldInfo`, `DocumentMetadata`,
and `OcrWord`. Tuples such as `Word`, `Block`, `ImageItem`, and `OutlineEntry`
are named tuples.

## Exceptions and warnings { #exceptions }

| Name | Raised when |
|---|---|
| `PdfError` | The base class for pylopdf errors; a subclass of `ValueError` |
| `MalformedPdfError` | The input is not a readable PDF, including empty input |
| `PasswordError` | The password matches neither the user nor the owner password |
| `EncryptedDocumentError` | The document must be unlocked with `unlock()` first |
| `LimitError` | A [size or resource limit](security.md#error-codes) was reached; `.code` says which |
| `DocumentClosedError` | The document was closed |
| `StalePageError` | A `Page` was fetched before pages were added, removed, or reordered |
| `StaleObjectError` | A low-level handle no longer refers to an object |
| `OcrError` | OCR could not run |
| `PylopdfWarning` | A recoverable problem, such as a repaired file or a font that could not be read |

A missing file raises Python's built-in `FileNotFoundError`.
