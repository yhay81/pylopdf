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
| `metadata` | The Info entries present as a live mapping: `title`, `author`, `subject`, `keywords`, `creator`, `producer`, `created`, `modified`, and `trapped`, the dates as PDF date strings. Assigning a key or calling `update()` writes, `del` removes an entry, assigning a mapping replaces every entry, and `{}` or `clear()` removes them all |
| `version`, `encryption_method` | The PDF version of the file header, such as `"1.7"`, and the encryption the document was opened with, such as `"Standard V5 R6 256-bit AES"`, or `None` |
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
    encryption=pylopdf.Encryption(
        user_password="secret",
        owner_password="owner-secret",
        permissions=pylopdf.Permissions.PRINT,
    ),
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
| `outline` | The outline (bookmarks) as a tree of `OutlineItem` handles; see [Outline and page labels](#outline) |
| `page_labels` | Page label ranges, such as roman numerals for front matter, as `PageLabelRange` records |

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
links, form fields, and tagged-PDF structure that pointed only to the removed
pages, and an outline item that led to one stays in place without a
destination, as in pymupdf. It does not remove every copy of their content,
so it is not secure redaction.

### Outline and page labels { #outline }

`doc.outline` is the outline as a sequence of its top-level `OutlineItem`
handles. Each item's `children` hold the rest of the tree, and
`doc.outline.walk()` visits every item in document order. An item reads the
document on every access, and assigning one of its properties edits the
document at once.

| `OutlineItem` member | Purpose |
|---|---|
| `title` | The title the viewer shows; assignable |
| `destination` | Where the item leads, a target from the table below or `None`; assignable |
| `page` | The zero-based page of a `Destination`, else `None` |
| `level`, `children` | The depth, 1 for a top-level item, and the child items |
| `color`, `bold`, `italic` | How the title shows: a `Color` without transparency or `None`, and two flags; assignable |
| `is_open` | Whether the children show when the document opens; assignable on an item with children |
| `xref`, `obj` | The item's object number and its dictionary in the [object model](#low-level-objects) |
| `to_entry()` | The item without its children, as an `OutlineEntry` |

Assigning a list of `OutlineEntry(level, title, destination=None, *,
color=None, bold=False, italic=False, is_open=False)` records replaces the
whole outline, and `del doc.outline` removes it. Levels start at 1 and grow by
at most one from one entry to the next; an entry's children are the entries
of the next level that follow it. `doc.outline.entries()` returns the current
outline in the same form, so it can be edited as a list and assigned back.

```python
from pylopdf import Destination, OutlineEntry

doc.outline = [
    OutlineEntry(1, "Chapter 1", Destination(0), bold=True, is_open=True),
    OutlineEntry(2, "Section 1.1", Destination(1, point=(72, 100))),
    OutlineEntry(1, "Project site", "https://example.com"),
]
for item in doc.outline.walk():
    if item.page == 1:
        item.title = item.title.upper()
```

| Target | Leads to |
|---|---|
| `Destination(page, point=None, zoom=None)` | A zero-based page of this document. `point` is the top left of the view in display coordinates and `zoom` its magnification; without either, the viewer fits the page in the window |
| a `str` | A URI |
| `RemoteDestination(file, page=0, name=None)` | A page, or a named destination, of another PDF file |
| `Launch(file)` | A file, opened with the application the viewer associates with it |
| `NamedAction(name)` | A viewer action such as `"NextPage"` or `"FirstPage"` |

A named destination reads as the `Destination` that it names, and a
destination whose page no longer exists reads as `None`. Reads and writes
stop at 65,536 items, 64 levels, and 16 MiB of titles and target text
(`LimitError` codes `toc_entry_count`, `toc_text_size`, and
`toc_input_size`).

Editing a title or destination checks the resulting outline's total text
before changing it. A refused edit leaves the outline unchanged. File
targets support Unicode names, and coordinates and zoom must fit finite PDF
real numbers.
For many title or destination edits, change the list from `entries()` and
assign it once, so the total text is checked once.

`doc.page_labels` is a list of `PageLabelRange(start, style=DECIMAL,
prefix="", first=1)` records. A range labels the pages from its zero-based
`start` up to the next range with `prefix` followed by a number, counting from
`first`, in its `PageLabelStyle`: `DECIMAL`, `UPPER_ROMAN`, `LOWER_ROMAN`,
`UPPER_LETTERS`, or `LOWER_LETTERS`, or `None` for the prefix alone. Assigning
a list replaces the ranges in page order, and the first range must start at
page 0; assigning `[]` or `del doc.page_labels` removes them. `page.label` is
one page's label, `""` when the document has none, and
`doc.pages.labeled(label)` finds the pages with a label.
An unsupported numbering style in an existing PDF reads as a prefix-only
range.

```python
from pylopdf import PageLabelRange, PageLabelStyle

doc.page_labels = [PageLabelRange(0, PageLabelStyle.LOWER_ROMAN), PageLabelRange(4, prefix="A-")]
print([page.label for page in doc.pages])  # ['i', 'ii', 'iii', 'iv', 'A-1', 'A-2']
```

### Text and Markdown { #document-text }

| Member | Purpose |
|---|---|
| `text(pages=None)` | Plain text of every page, or of the listed pages, as one string |
| `markdown(pages=None, *, table_strategy="lines", max_size=64 MiB)` | Markdown with headings, lists, emphasis, multi-column order, vertical CJK, and tables |

`table_strategy="lines"` converts tables drawn with ruling lines,
`"text"` also converts borderless tables that are clearly aligned, and `None`
turns table conversion off.

Word gaps on one line follow `text()` even when the font or style
changes. Spaces stay outside emphasis markers, and combining marks stay
attached to their base characters.

### Rendering and images { #document-rendering }

| Member | Purpose |
|---|---|
| `render(pages=None, *, dpi=None, matrix=None, alpha=False, background=None, annotations=True, colorspace="rgb", format="png", quality=95, workers=None, max_size=512 MiB)` | PNG or JPEG bytes for every page, or the listed pages, in order, rendered in parallel |
| `images` | The image objects by object number; see [Images](#images) |
| `rewrite_images(*, dpi_threshold=None, dpi_target=0, quality=0, ...)` | Downsample and recompress images to make the file smaller |
| `fallback_fonts` | The fonts that render Chinese, Japanese, or Korean text whose font is not embedded, by language; the CJK extras fill it automatically |

`rewrite_images` follows pymupdf's keywords: images with more than
`dpi_threshold` dots per inch where they are drawn are resampled to
`dpi_target`, and `quality` (1–100) re-encodes them as JPEG. It changes only
plain 8-bit gray or RGB images without masks, keeps an image whose new
encoding would not be smaller, and returns statistics.

`fallback_fonts` maps `"ja"`, `"ko"`, `"zh-CN"`, and `"zh-TW"`, the
languages of the Adobe-Japan1, Korea1, GB1, and CNS1 character collections,
to `FallbackFont(sans=None, serif=None)` records of OpenType `Font` values.
A non-embedded font whose name marks it as serif, such as a Mincho, Song,
Ming, or Batang face, renders with `serif`, and every other one with `sans`;
when one of them is `None`, the other serves both. Until it is edited, the
mapping lists the installed font extras (`pylopdf[jp]`, `[ko]`, `[zh-cn]`,
and `[zh-tw]`). Assigning or deleting one language changes only that
language, assigning a mapping replaces every language, and
`del doc.fallback_fonts` returns to the installed extras. A face is shared
rather than copied, so one `Font` can serve many documents.

```python
from pylopdf import FallbackFont, Font

doc.fallback_fonts["ja"] = FallbackFont(Font(fontfile="NotoSansJP-Regular.otf"), Font(fontfile="NotoSerifJP-Regular.otf"))
doc.fallback_fonts = {}           # no fallback: such text is not drawn
del doc.fallback_fonts            # back to the installed font extras
```

### Forms, attachments, and other data { #document-data }

| Member | Purpose |
|---|---|
| `get_form_fields()` | Every form field with its name, type, value, and options |
| `set_form_field(name, value, *, font=, fontfile=, fontbuffer=, fontindex=, font_language=)` | Fill a field and regenerate how it looks; see [Forms](#forms) |
| `attachments` | Embedded files as a mapping; see [Attachments](#attachments) |
| `pdfa_claim` | The PDF/A part and conformance the file declares, as a `PdfaClaim(part=2, conformance="B")` record, or `None`. This reads the declaration only; it does not validate |
| `xmp` | The XMP metadata packet as text, or `None`. Assign a string to write it and `del doc.xmp` to remove it. Reads and writes stop at 1 MiB; read a larger packet with `doc.catalog["Metadata"].read(max_size=None)`. `metadata` does not change it |
| `language` | The document's language as a complete BCP 47 tag, such as `zh-TW` or `sr-Latn-RS`, or `None`. Assign a tag to write it and `del doc.language` to remove it; a malformed tag raises |
| `page_mode`, `page_layout` | How a viewer opens the document, as `PageMode` and `PageLayout` members such as `PageMode.USE_OUTLINES` or `PageLayout.TWO_COLUMN_LEFT`; `USE_NONE` and `SINGLE_PAGE` when unset. Assign a member or its exact name |
| `mark_info` | The tagged-PDF flags as a `MarkInfo(marked=, user_properties=, suspects=)` record, or `None` when absent. Assigning writes all three flags; `del` removes them |
| `objects`, `trailer`, `catalog` | Direct access to PDF objects; see [Low-level objects](#low-level-objects) |

`language` keeps the complete tag, where pymupdf's keeps at most three
letters, so that `zh-TW` and `zh-HK` both read as `zh-Hant` there. Writing XMP
leaves the packet uncompressed even when saving compresses streams, so tools
that search files for it still find it.

## Page { #page }

A `Page` is a view of one page of its document. Fetch it with `doc.pages[i]`.
Two views are equal when they view the same page of the same document and
no page was added, deleted, or reordered in between.

### Text and search { #page-text }

| Member | Purpose |
|---|---|
| `text()` | The page's text in reading order |
| `words()` | The words in reading order, as `Word` records |
| `layout(*, chars=False)` | Blocks, lines, and spans as a `Layout`; see [Positioned text](#positioned-text) |
| `search(text, *, max_hits=4096)` | Case-insensitive search; returns a list of `Rect` |
| `tables(*, strategy="lines", clip=None)` | Tables as a list of `Table` records; see [Tables](#tables) |
| `markdown(*, table_strategy="lines")` | Markdown for this page |
| `ocr(...)`, `apply_ocr(*, words=None, ...)` | OCR, and an invisible text layer from it or from another engine's words; see [Offline OCR](ocr.md) |
| `content(*, text_runs=True, paths=True, images=True, clip=None, annotations=True)` | Every text run, path, and image the page paints; see [Page content](#content) |
| `label` | The page label, such as `"iv"`; see [Outline and page labels](#outline) |

A `Word` has `text`, `bbox`, and `block`, `line`, and `index`, which locate
it in the page's layout: the indexes of its block, of its line in that block,
and of the word in that line.

Reading order follows lines and columns, including right-to-left text and
vertical Japanese and Chinese. Ruby, warichu, and text in mixed orientations
are not interpreted.

### Rendering { #page-rendering }

| Member | Purpose |
|---|---|
| `pixmap(*, dpi=None, matrix=None, clip=None, alpha=False, background=None, annotations=True, colorspace="rgb")` | Render to a [`Pixmap`](#pixmap) |
| `svg(*, annotations=True, max_size=64 MiB)` | Render to an SVG string, with text drawn as paths |
| `resources` | What the page's resources reach: `resources.images` lists its image objects; see [Images](#images) |

A pixmap is RGB on white at 72 dpi by default and RGBA with `alpha=True`.
Pass `dpi` or `matrix`, not both; a matrix must only scale, such as
`Matrix(2, 2)`. `clip` renders only part of the page, and
`annotations=False` leaves out annotations and form widgets, and
`colorspace="gray"` renders gray pixels.

### Page geometry { #page-geometry }

| Member | Purpose |
|---|---|
| `index`, `document` | The page's index in its document, and the document |
| `rect` | The visible page area in display space, with rotation applied |
| `rotation` | 0, 90, 180, or 270; assign a multiple of 90 to rotate the page clockwise |
| `boxes` | The MediaBox, CropBox, BleedBox, TrimBox, and ArtBox as `Rect` values in PDF user space, each assignable |
| `user_to_display` | The `Matrix` that maps PDF user space to display space |
| `xref`, `obj` | The page object's number and a [low-level handle](#low-level-objects) |

Page boxes are the one place that uses PDF user space, with y upward from the
bottom of the MediaBox, because they define display space. `page.boxes` reads
them as stored, with their corners ordered: `media` defaults to A4 at the
origin, `crop` to the MediaBox, and `bleed`, `trim`, and `art` to the CropBox.
Assigning `media` removes the other boxes, as pymupdf's `set_mediabox` does;
assigning another box needs a rectangle with area inside the MediaBox, and
`del page.boxes.crop` (or `bleed`, `trim`, `art`) restores its default.
`page.user_to_display` maps user space to display space, and
`~page.user_to_display` maps back.

```python
page.boxes.crop = (36, 36, 576, 756)                # show a Letter page without its half-inch margin
trim_on_screen = page.boxes.trim * page.user_to_display
page.rotation = 90
```

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
| `annotations` | The page's annotations other than links and form widgets, as `Annotation` handles |
| `links` | The page's links as `Link` handles, in the order the page lists them |

`page.annotations` is a sequence of `Annotation` handles, in the order the
page lists them; a popup belongs to the annotation that names it and is not
listed. A handle reads the document on every access and has `type`, an
`AnnotationType` member, `rect` in display coordinates, the texts
`contents`, `author`, `subject`, `name`, `icon`, and `intent`, the dates
`created` and `modified` as the PDF date strings stored, `color` and
`interior_color` as `Color` records or `None`, `opacity`, `flags`, an
`AnnotationFlags` value, and `xref`, `obj`, and `page`.
`page.annotations.add(Highlight(rects, *, color=Color(1, 1, 0), opacity=0.4,
contents=None))` highlights one or more rectangles or quads, such as search
results, and returns the new handle; `del page.annotations[i]` removes an
annotation with its popup. The members of `AnnotationType` and
`AnnotationFlags` keep the values of pymupdf's `PDF_ANNOT_*` and
`PDF_ANNOT_IS_*` constants.

```python
from pylopdf import AnnotationType, Color, Highlight

note = page.annotations.add(Highlight(page.search("total"), color=Color(1, 0.8, 0), contents="Check"))
for annotation in page.annotations:
    if annotation.type is AnnotationType.TEXT:
        print(annotation.author, annotation.contents)
```

Annotation reads stop at 4,096 annotations and 1 MiB of text per page, and
a highlight's contents stop at 1 MiB (`LimitError` code
`annotation_input_size`).

`page.links` is a sequence of `Link` handles: `page.links.add(rect, target)`
adds a link over a rectangle in display coordinates and returns its handle,
and `del page.links[i]` removes one. A handle reads the document on every
access, and assigning its `rect` or `target` edits the link. Targets are those
of the [outline](#outline): a URI string, a `Destination`, a
`RemoteDestination`, a `Launch`, or a `NamedAction`. A link whose action
pylopdf does not read, such as JavaScript, or whose destination names no page
has the target `None`. `xref` and `obj` give the annotation's object number
and its dictionary in the [object model](#low-level-objects), which holds its
border, color, and flags.

```python
from pylopdf import Destination

for rect in page.search("Chapter 2"):
    page.links.add(rect, Destination(5))
for link in page.links:
    if isinstance(link.target, str) and link.target.startswith("http:"):
        link.target = "https:" + link.target.removeprefix("http:")
```

Link reads stop at 4,096 annotations and 1 MiB of target text per page, and a
link's target text stops at 1 MiB when written (`LimitError` code
`annotation_input_size`).

## Positioned text and glyphs { #positioned-text }

`page.layout()` returns a `Layout` with the page's `width`, `height`, and
`blocks`. Each record holds the next level down, and every box is in display
space:

| Record | Fields |
|---|---|
| `Block` | `text`, `bbox`, `lines` |
| `Line` | `text`, `bbox`, `direction` (the baseline's unit vector), `is_vertical`, `spans` |
| `Span` | `text`, `bbox`, `origin`, `size`, `font`, `is_bold`, `is_italic`, `is_serif`, `is_monospaced`, `chars` |
| `Char` | `text`, `bbox`, `origin` |

`layout(chars=True)` fills each span's `chars` with its glyphs. A `Char`
keeps the complete text of one glyph, so a ligature such as "fi" keeps both
letters, and its `origin` is where the glyph's baseline starts.

```python
with pylopdf.open("report.pdf") as doc:
    layout = doc.pages[0].layout(chars=True)
    for block in layout.blocks:
        for line in block.lines:
            for span in line.spans:
                for char in span.chars:
                    print(char.text, char.bbox, char.origin)
```

Bounds are estimated from the baseline and font size, not from the glyph
outlines. Spaces that pylopdf infers from gaps are not added as characters, so
joining the characters does not always reproduce `text()`. Glyphs that have no
Unicode mapping in the PDF are skipped with a `PylopdfWarning`.

`layout.to_dict()` returns plain dictionaries for JSON, in the shape of
[pymupdf's dict, or its rawdict for a layout with characters](https://pymupdf.readthedocs.io/en/latest/textpage.html#structure-of-dictionary-outputs),
with text blocks only, without image or vector blocks and without color,
opacity, or extra font metrics. The
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

`text_runs=False`, `paths=False`, and `images=False` leave out that kind of
item without building it, so reading only the paths or the images of a
text-heavy page is many times faster. `clip=` keeps the items whose box meets
a rectangle, and `annotations=False` leaves out annotation appearances. One call reads at most `max_elements`
glyphs, path and clip segments, stroke dash lengths, and items, 262,144 by
default.

What hayro does not report yet is not in the model: the color space and
components of a color, layer names and the other marked-content properties,
the font dictionary behind a `FontRef`, and the object of an image mask. They
are listed as
[engine work](https://github.com/yhay81/pylopdf/blob/main/ROADMAP.md#engine-work).

## Images { #images }

`doc.images` maps object numbers to the document's image objects, and
`page.resources.images` lists the ones that a page's resources reach, once
each, those of the Form XObjects that it uses included. Each is an
`ImageObject` that reads the document on every access:

| Member | Meaning |
|---|---|
| `xref`, `obj` | The object number, and the image's stream in the [object model](#low-level-objects) |
| `width`, `height`, `bits` | The size in pixels and the bits per component |
| `colorspace`, `alternate_colorspace` | The color space, such as `"DeviceRGB"` or `"ICCBased"`, and the base or alternate space of an indexed, ICC-based, or separation space |
| `filters` | The stream's filters in decoding order, such as `("DCTDecode",)` |
| `soft_mask`, `mask`, `is_stencil` | The soft mask and the stencil mask, as image objects, and whether the image is itself a stencil mask |
| `extract()` | The image's data as an `EncodedImage` with `format`, `data`, and `resolution`: JPEG data as it is stored and any other image as PNG, without its masks |
| `to_pixmap(*, alpha=False)` | The image's pixels as a [`Pixmap`](#pixmap): RGB without its masks, or RGBA in which they set the transparency |

```python
from pathlib import Path

with pylopdf.open("scan.pdf") as doc:
    for image in doc.pages[0].resources.images:
        encoded = image.extract()
        Path(f"image-{image.xref}.{encoded.format}").write_bytes(encoded.data)
```

Where an image is drawn belongs to the page's content:
`page.content(text_runs=False, paths=False).images` lists every placement
with its box and matrix.

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
filled, stroked, or both. `text` places each line `line_height × size` below
the previous one, by default at the font's own line height, its ascender minus
its descender. `textbox` breaks lines at Unicode line-break opportunities and
spaces them the same way, with the first baseline one ascender below the top
of `rect`; when the text does not fit, it draws nothing and
`TextFit.spare_height` is the negative shortfall, so `fit.fits` is false.
[`Font.text_height`](#font) measures the height that a textbox needs before
anything is drawn, which sizes a frame around the text or decides a page
break:

```python
font = pylopdf.Font.from_file("NotoSansJP-Bold.otf")
height = font.text_height(note, 10.5, width=300)
if y + height + 16 > page.rect.height - 40:
    page = doc.pages.new()
    y = 40
with page.draw() as canvas:
    canvas.rect((40, y, 356, y + height + 16), radius=4, stroke=navy)
    canvas.textbox((48, y + 8, 348, y + 8 + height), note, font=font, size=10.5)
```

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

font = pylopdf.Font.from_file("NotoSans-Regular.ttf")
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

`page.tables()` finds tables drawn with ruling lines, including merged
cells. `strategy="text"` also detects borderless tables when rows and columns
are clearly aligned; it is not the default because aligned multi-column prose
can look like a table. `clip=` keeps only tables entirely inside a rectangle.

The result is a list of `Table` records with `bbox`, `rows` (the cell text,
row by row), `cells` (each cell's `Rect` in the same shape), `diagnostics`,
and `markdown()`. A merged cell keeps its text and box in its top-left slot,
and the slots it covers are `None`. `diagnostics.confidence` is a 0–1 score
for ranking candidates, not a probability; the other diagnostics explain it.

```python
for table in page.tables():
    header, *body = table.rows
    print(table.bbox, header, len(body))
```

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
pylopdf.Font.standard(name)
pylopdf.Font.from_file(path, /, *, index=0, weight=None, max_size=64 MiB)
pylopdf.Font.from_bytes(data, /, *, index=0, weight=None, max_size=64 MiB)
pylopdf.Font.installed(*, language=None, family=None, serif=False, weight=None, italic=False)
```

| Example | Loads |
|---|---|
| `Font.standard("Helvetica")`, `Font.standard("Times-Bold")` | A Standard 14 font, by its PostScript name |
| `Font.from_file("NotoSansJP-Bold.otf")`, `Font.from_bytes(data)` | An OpenType font, from a file or its bytes |
| `Font.from_file("NotoSansCJKjp-VF.otf", weight=700)` | One weight of a variable font |
| `Font.from_file("fonts.ttc", index=1)` | One face of a font collection |
| `Font.installed(language="ja")`, `Font.installed(family="Noto Sans JP")` | A font from an installed extra; `serif=True` picks a language's serif face |
| `Font.installed(language="he", weight=700)` | The bold weight of an installed variable font |

A font measures text as the [canvas](#canvas) draws it. `text_width(text,
size)` is the width of one line, kerning and ligatures included.
`text_height(text, size, *, width, line_height=None)` is the height that
`canvas.textbox` needs for the text wrapped `width` points wide, so a textbox
of that height fits the text exactly. Properties: `name`, `family`, `weight`,
`is_bold`, `is_italic`, `is_serif`, `units_per_em`, `ascender`, and
`descender`; `has_glyph(char)` tells whether the face has a glyph for a
character. `typing.get_args(pylopdf.StandardFontName)` lists the 14 names that
`Font.standard()` takes. Standard 14 fonts are not embedded and cover Western
European (WinAnsi) characters only.

A variable font loaded without `weight` uses its default instance, which is
not always the regular weight: the variable Noto Sans CJK JP defaults to Thin.
Pass `weight=400` for regular. The `fontfile` and `fontbuffer` arguments of the
drawing methods use the default instance too; pass a `Font` as `font=` to draw
another weight.

The Arabic, Hebrew, Hindi, and Thai extras ship variable fonts, so any weight
on their axis works (100–900; 400–700 for Noto Naskh Arabic). The CJK extras
ship one regular face per style: `Font.installed(language="ja", weight=700)`
raises `PdfError`, so load a bold face, such as Noto Sans JP Bold, with
`Font.from_file()`. pylopdf never imitates bold or italic by distorting
glyphs.

pylopdf does not search system font directories and has no global font
registry, so the output depends only on the fonts you pass in.

### Shapes { #shapes }

`page.new_shape()` returns a `Shape`, pymupdf's tool for drawing several paths
and text runs as one unit:

```python
regular = pylopdf.Font.standard("Helvetica")
bold = pylopdf.Font.standard("Helvetica-Bold")
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

hits = page.search("Total")
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

`Pixmap` holds the pixels of a rendered page or image and cannot be changed.

| Member | Purpose |
|---|---|
| `width`, `height`, `channels`, `alpha`, `stride`, `colorspace` | Size and layout: `channels` is 1 for gray, 2 for gray with alpha, 3 for RGB, and 4 for RGB with alpha; `colorspace` is `Colorspace.GRAY` or `Colorspace.RGB` |
| `numpy.asarray(pix)`, `memoryview(pix)` | The pixels without copying, shaped `(height, width)` for gray and `(height, width, channels)` otherwise |
| `bytes(pix)` | A copy of the pixels |
| `Pixmap.from_array(array)` | A pixmap with a copy of a `uint8` array shaped as above |
| `to_bytes(format="png", *, quality=95, max_size=64 MiB)` | PNG or JPEG bytes; JPEG has no alpha |
| `save(path, /, *, format=None, quality=95)` | Write PNG or JPEG; the extension `.png`, `.jpg`, or `.jpeg` names the format unless `format` does |

Image processing belongs to NumPy, Pillow, and OpenCV:
`PIL.Image.fromarray(numpy.asarray(pix))` gives a Pillow image.

## Module functions and types { #module-level }

| Name | Purpose |
|---|---|
| `open(source, /, *, password=None, limits=None)` | Open a document; see [Document](#document) |
| `probe(source, /, *, password=None, max_file_size=None)` | A `Probe` record with `page_count`, `version`, `needs_password`, `is_repaired`, and `metadata` (`title`, `author`, `subject`, `keywords`, `creator`, `producer`, `created`, and `modified` when present), read without loading the document |
| `DocumentLimits`, `DocumentLimits.web()` | Resource limits for untrusted input; see [Security](security.md#untrusted-pdfs) |
| `OcrEngine` | A reusable OCR engine; see [Offline OCR](ocr.md) |
| `Canvas`, `PathBuilder`, `TextFit` | See [Canvas](#canvas) |
| `create(...)`, `Builder`, `Standard`, `TagRole` | See [New documents](#create) |
| `Font`, `StandardFontName`, `Shape` | See [Font](#font) and [Shapes](#shapes) |
| `Name`, `String`, `Array`, `Dictionary`, `Stream`, `Objects` | [Low-level objects](#low-level-objects) |
| `Content`, `TextRun`, `Path`, `Image`, `Glyph`, `FontRef`, `Color`, `Stroke`, `GraphicsContext`, and the other content types | The [content model](#content) |
| `Colorspace` | The color spaces of a [`Pixmap`](#pixmap): `GRAY` and `RGB` |
| `Outline`, `OutlineItem`, `OutlineEntry`, `Destination`, `RemoteDestination`, `Launch`, `NamedAction`, `PageLabelRange`, `PageLabelStyle` | [Outline and page labels](#outline) |
| `Annotation`, `Annotations`, `AnnotationType`, `AnnotationFlags`, `Highlight`, `Link`, `Links` | [Annotations and links](#page-annotations) |
| `PageBoxes` | [Page geometry](#page-geometry) |
| `FallbackFont`, `FallbackFonts` | [Rendering and images](#document-rendering) |

Constants keep pymupdf's names and are also grouped in enums:
`Permissions` (`PDF_PERM_*`), `TextAlign` (`TEXT_ALIGN_*`), `AnnotationType`
(`PDF_ANNOT_*`), and `AnnotationFlags` (`PDF_ANNOT_IS_*`). `Access` has the members `USER` and `OWNER`, and
`Encryption` describes how to encrypt a saved file. The content model's enums
are `BlendMode`, `FillRule`, `LineCap`, `LineJoin`, `TextRenderMode`,
`ShadingType`, `SoftMaskType`, and `ImageMask`.

Text, table, OCR, and page content results are records: frozen dataclasses
such as `Word`, `Block`, `Line`, `Span`, `Char`, `Layout`, `Table`,
`TableDiagnostics`, `OcrWord`, `Path`, `Image`, `MarkInfo`, `PdfaClaim`,
`OutlineEntry`, `PageLabelRange`, and the outline targets. Other dictionaries
returned by the API are plain `dict` objects, described for type checkers by
`TypedDict` classes such as `FormFieldInfo`.

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
