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
pylopdf.Document(filename=None, stream=None, filetype=None, *,
                 password=None, limits=None, max_decompressed_size=None)
```

`pylopdf.open` is the same callable. Pass a path as `filename`, or the file's
contents as `stream` (`bytes`, `bytearray`, `memoryview`, or `io.BytesIO`).
`Document()` with no arguments creates an empty PDF. `limits` takes a
[`DocumentLimits`](security.md#untrusted-pdfs) policy for untrusted input. Use
the document as a context manager, or call `close()`.

### Open, inspect, and save { #document-io }

| Member | Purpose |
|---|---|
| `page_count`, `len(doc)` | Number of pages |
| `metadata` / `set_metadata(m)` | Title, author, and the other Info fields, plus `format` and `encryption`. `set_metadata` removes keys whose value is empty, and `{}` removes all of them |
| `needs_pass`, `is_encrypted`, `authenticate(password)` | Encryption state; `authenticate` unlocks the document and returns pymupdf's codes: 0 if the password is wrong, otherwise 1, 2, 4, or 6 |
| `is_repaired` | Whether opening had to repair damaged cross-reference data |
| `permissions` | The permission flags, as pymupdf reports them: `0` while a password is needed, `-4` (all) for an unencrypted document or after the owner password, else the encryption's `/P` value. Test a flag with `doc.permissions & Permissions.PRINT` |
| `is_dirty` | Whether the document was edited since it was opened. A new empty document starts edited, and saving does not reset it, as in pymupdf |
| `is_closed`, `is_pdf`, `name` | Whether the document is closed; always `True`; the `filename` it was opened with, or `None` for a stream, an empty document, or after `close()` |
| `limits`, `complexity` | The policy the document was opened with, and cheap structural facts (pages, objects, streams) for deciding whether to process it |
| `save(filename, *, garbage=0, deflate=False, use_objstms=False, encryption=, user_pw=, owner_pw=, permissions=)` | Write the PDF. The file is replaced only after the complete write succeeds |
| `tobytes(..., max_size=512 MiB)` | The same output as `bytes` |
| `close()` | Release the document; `with` does this for you |

`garbage` takes pymupdf's levels: 1 removes unused objects, 2 also renumbers
the rest, and 3 and 4 also merge duplicate objects and streams. `deflate`
compresses streams, and `use_objstms` packs objects into compressed object
streams. `encryption` is `PDF_ENCRYPT_NONE` (the default) or
`PDF_ENCRYPT_AES_256`; passing a password without AES-256 raises.

### Pages { #document-pages }

| Member | Purpose |
|---|---|
| `doc[i]`, `load_page(pno)`, iteration | [`Page`](#page) objects |
| `new_page(pno=-1, *, width=595, height=842)` | Insert a blank page (A4 by default) |
| `insert_pdf(docsrc, *, from_page=-1, to_page=-1, start_at=-1)` | Copy pages from another document, with their links and form fields |
| `select(page_numbers)` | Keep the listed pages in the listed order; repeating a number duplicates the page |
| `delete_page(pno)`, `delete_pages(...)` | Remove pages; `delete_pages` takes one number, two numbers, `from_page=`/`to_page=`, or an iterable |
| `fullcopy_page(pno, to=-1)` | Insert an independent copy of a page |
| `get_outline()` / `set_outline(entries)` | Read or replace the table of contents as `OutlineEntry(level, title, page)` items |
| `get_page_labels()` / `set_page_labels(labels)` | Page label ranges, such as roman numerals for front matter |
| `get_page_numbers(label, only_one=False)` | The zero-based numbers of the pages labeled `label` |

Deleting or selecting pages also removes the links, outline entries, form
fields, and tagged-PDF structure that pointed only to the removed pages. It
does not remove every copy of their content, so it is not secure redaction.

### Text and Markdown { #document-text }

| Member | Purpose |
|---|---|
| `get_text(pages=None)` | Plain text of every page, or of the listed pages, as one string |
| `get_page_text(pno, option="text")` | Same as `doc[pno].get_text(option)` |
| `to_markdown(pages=None, *, table_strategy="lines", max_size=64 MiB)` | Markdown with headings, lists, emphasis, multi-column order, vertical CJK, and tables |

`table_strategy="lines"` converts tables drawn with ruling lines,
`"text"` also converts borderless tables that are clearly aligned, and `None`
turns table conversion off.

### Rendering and images { #document-rendering }

| Member | Purpose |
|---|---|
| `get_page_pixmap(pno, ...)` | Same as `doc[pno].get_pixmap(...)` |
| `render_pages(pages=None, *, matrix=, dpi=, alpha=False, background=, workers=, max_size=512 MiB)` | PNG bytes for every page, or the listed pages, in order, rendered in parallel |
| `get_page_images(pno, *, full=False)` | Same as `doc[pno].get_images(full=...)` |
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
| `get_ocgs()`, `add_ocg(...)`, `get_oc(xref)` / `set_oc(xref, oc)`, `get_ocmd(xref)` / `set_ocmd(...)` | Layers; see [Optional content](#optional-content) |
| `objects`, `trailer`, `catalog`, `page_xref(pno)` | Direct access to PDF objects; see [Low-level objects](#low-level-objects) |

`language_tag` replaces pymupdf's `language`, which keeps at most three
letters of the tag, so `zh-TW` and `zh-HK` both read as `zh-Hant`. Writing XMP
leaves the packet uncompressed even with `deflate=True`, so tools that search
files for it still find it.

## Page { #page }

A `Page` is a view of one page of its document. Fetch it with `doc[i]`.

### Text and search { #page-text }

| Member | Purpose |
|---|---|
| `get_text(option="text")` | `"text"`, `"words"`, `"blocks"`, `"dict"`, or `"rawdict"` |
| `search_for(text, *, max_hits=4096)` | Case-insensitive search; returns a list of `Rect` |
| `find_tables(*, clip=None, strategy=None)` | Tables as a `TableFinder`; see [Tables](#tables) |
| `to_markdown(*, table_strategy="lines")` | Markdown for this page |
| `get_text_ocr(...)`, `apply_ocr(...)`, `insert_ocr_text_layer(words)` | OCR; see [Offline OCR](ocr.md) |
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
| `number`, `parent` | Page number and document |
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
| `new_shape()` | Draw several paths and text runs at once; see [Shapes](#shapes) |
| `replace_text(search, replacement, *, default_char=None)` | Replace text drawn in simple (single-byte) fonts |

The same image or page drawn again is stored once in the file.

### Annotations and links { #page-annotations }

| Member | Purpose |
|---|---|
| `annots(*, types=None)` | Read-only `Annot` objects with `type`, `rect`, `info`, `colors`, `flags`, `xref`, and `get_oc()` |
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
    layout = doc[0].get_text("rawdict")
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

## Vector drawings { #drawings }

`page.get_drawings()` returns one `DrawingInfo` dictionary per filled or
stroked path, in pymupdf's format: `type` (`"f"`, `"s"`, or `"fs"`), `items`
(lines and cubic curves), `rect`, colors, opacity, line width, cap, join,
dashes, and `seqno`. Paths filled with a pattern have `None` colors. Clipping
paths, groups, and layer names are not returned.

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

`insert_textbox` wraps text at the places Unicode allows a line break (UAX
#14), keeps explicit line breaks, expands tabs, and splits a word that is too
long for a line. `align` takes `TEXT_ALIGN_LEFT`, `TEXT_ALIGN_CENTER`,
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

## Optional content { #optional-content }

Optional content groups, often called layers, let a viewer show or hide parts
of a page. Rendering and text extraction show a group as the document's
default configuration (`/OCProperties /D`) says.

| Member | Purpose |
|---|---|
| `doc.get_ocgs()` | Every group as `{xref: {"name", "intent", "on", "usage"}}`; `on` is the default configuration's state |
| `doc.add_ocg(name, config=-1, on=1, intent=None, usage=None)` | Add a group to the default configuration, or to `/Configs[config]`, and return its xref |
| `doc.get_oc(xref)` / `doc.set_oc(xref, oc)` | The group or membership dictionary that an image or Form XObject belongs to; `oc=0` removes it |
| `doc.get_ocmd(xref)` / `doc.set_ocmd(xref=0, ocgs=None, policy=None, ve=None)` | A membership dictionary, which combines groups by policy (`"AnyOn"`, `"AllOff"`, ...) or by a visibility expression such as `["and", 5, ["not", 6]]` |
| `page.get_oc_items()` | The `(name, xref, "ocg" or "ocmd")` entries of the page's `/Properties` resources, which marked content in the page refers to |
| `annot.get_oc()` | The xref of an annotation's group or membership dictionary, or 0 |

```python
hidden = doc.add_ocg("Draft marks", on=False)
doc.set_oc(form_xref, hidden)   # the Form XObject is no longer drawn
```

Rendering and extraction combine a membership dictionary's `ocgs` by its
`policy`; like hayro, they do not evaluate a visibility expression. pymupdf
reports `on` from the state at opening, so it misses later edits, and it
reports a group without `/Usage /CreatorInfo` as on whatever its state.

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
`save(garbage=2)` or higher renumbers objects. pylopdf does not check that
your edits make sense as PDF.

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
| `open(...)` | Same as `Document(...)` |
| `peek_metadata(filename=None, stream=None, password=None, *, max_file_size=None)` | Metadata and page count without loading the document |
| `DocumentLimits`, `DocumentLimits.web()` | Resource limits for untrusted input; see [Security](security.md#untrusted-pdfs) |
| `OcrEngine` | A reusable OCR engine; see [Offline OCR](ocr.md) |
| `Font`, `Shape` | See [Font](#font) and [Shapes](#shapes) |
| `Base14_fontnames`, `Base14_fontdict` | The Standard 14 font names that `fontname=` accepts |
| `Name`, `String`, `Array`, `Dictionary`, `Stream`, `Objects` | [Low-level objects](#low-level-objects) |
| `Colorspace`, `csRGB`, `csGRAY`, `csCMYK` | pymupdf's device color spaces |

Constants keep pymupdf's names and are also grouped in enums:
`Permissions` (`PDF_PERM_*`), `EncryptionMethod` (`PDF_ENCRYPT_*`), `LinkKind`
(`LINK_*`), `TextAlign` (`TEXT_ALIGN_*`), `AnnotType` (`PDF_ANNOT_*`), and
`AnnotFlags` (`PDF_ANNOT_IS_*`).

Dictionaries returned by the API are plain `dict` objects, described for type
checkers by `TypedDict` classes such as `TextPageDict`, `RawTextPageDict`,
`DrawingInfo`, `ImageInfo`, `LinkInfo`, `FormFieldInfo`, `DocumentMetadata`,
and `OcrWord`. Tuples such as `Word`, `Block`, `ImageItem`, and `OutlineEntry`
are named tuples.

## Exceptions and warnings { #exceptions }

| Name | Raised when |
|---|---|
| `PdfError` | The base class for pylopdf errors; a subclass of `ValueError` |
| `FileDataError` | The input is not a readable PDF; also a `RuntimeError`, as in pymupdf |
| `EmptyFileError` | The input is empty |
| `PasswordError` | Decryption with the given password failed |
| `EncryptedDocumentError` | The document must be unlocked with `authenticate()` first |
| `LimitError` | A [size or resource limit](security.md#error-codes) was reached; `.code` says which |
| `DocumentClosedError` | The document was closed |
| `StalePageError` | A `Page` was fetched before pages were added, removed, or reordered |
| `StaleObjectError` | A low-level handle no longer refers to an object |
| `OcrError` | OCR could not run |
| `PylopdfWarning` | A recoverable problem, such as a repaired file or a font that could not be read |

A missing file raises Python's built-in `FileNotFoundError`.
