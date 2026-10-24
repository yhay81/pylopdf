---
title: API reference
description: A map of pylopdf's Document, Page, Canvas, Font, Pixmap, geometry types, low-level objects and exceptions.
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
| `save(path, /, *, compress=True, prune=True, dedupe=False, renumber=False, object_streams=False, encryption=None, incremental=False)` | Write the PDF. The file is replaced only after the complete write succeeds |
| `can_save_incrementally` | Whether `save` and `to_bytes` accept `incremental=True` |
| `to_bytes(..., max_size=512 MiB)` | The same output as `bytes` |
| `close()` | Release the document; `with` does this for you |

By default, saving compresses the streams that Flate makes smaller and
removes the objects that nothing references; `compress=False` and
`prune=False` keep them as they are. `dedupe=True` merges duplicate objects
and streams, `renumber=True` numbers the remaining objects consecutively, and
`object_streams=True` packs objects into compressed object streams. These
options change the document itself, not only the output. To encrypt, pass
`encryption=Encryption(user_password=..., owner_password=...,
permissions=...)`, which writes AES-256 encryption, or add
`method="aes-128"` for AES-128, which PDF 1.6 readers open as well; the
owner password defaults to the user password, and the document in memory
stays unencrypted. pylopdf reads every standard encryption, RC4 included, but
never writes RC4. `encryption=Encryption.KEEP` writes the encryption the
document was opened with, keeping its passwords and permissions; it keeps
AES-128 and AES-256 and refuses RC4. `None`, the default, writes no
encryption, even for a document that was encrypted.

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

`incremental=True` writes the bytes the document was opened from unchanged
and appends one update with the objects that differ from them, so a
signature over the original bytes stays intact; an unedited document is
written exactly as it was read. It works for a document opened from
unencrypted bytes that loaded without repair, which `can_save_incrementally`
reports, and refuses `dedupe`, `renumber`, `object_streams`, and
`encryption`, which would rewrite objects the update leaves alone. `compress`
then compresses only the streams the update writes. The output may go to any
path, including the file the document came from. Such a document keeps its
original bytes in memory while it is open.

```python
with pylopdf.open("signed.pdf") as doc:
    doc.form["Name"].value = "Alice"
    doc.save("signed.pdf", incremental=True)
```

### Transactions and undo { #transactions }

`with doc.transaction(name):` groups edits: when the block raises, the
document returns to its state before the block, and otherwise the block's
edits become one step of `doc.history`, which undoes and redoes them. A
transaction inside another one restores its own edits when it raises and
leaves the step to the outer one. Only transactions record steps, and an
edit outside one ends the history; saving keeps it.

| `doc.history` member | Purpose |
|---|---|
| `undo()`, `redo()` | Return to the state before the latest applied step, or apply the latest undone step again |
| `can_undo`, `can_redo` | Whether there is a step to undo or redo |
| `steps`, `position` | The step names, oldest first, and how many of them are applied |
| `max_size` | The bytes that the steps' copies of the document may hold, 256 MiB by default; the oldest steps go first, and `None` keeps every step |
| `clear()` | Forget every step |

```python
with doc.transaction("stamp"):
    for page in doc.pages:
        with page.draw() as canvas:
            canvas.text("Draft", (36, 36))
doc.history.undo()
```

Each transaction copies the document when it starts, and undo and redo
replace its objects, so pages and other handles taken before go stale.

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

### Layers { #layers }

`doc.layers` is the sequence of the document's optional-content groups, the
layers that a viewer can show or hide, in the order that
`/OCProperties /OCGs` lists them. Each is a `Layer` handle that reads the
document on every access.

| `Layer` member | Purpose |
|---|---|
| `name` | The name a viewer shows; assignable |
| `intent` | A tuple of intents, `("View",)` unless the group sets others such as `"Design"`; assignable |
| `is_visible` | Whether the default configuration shows the group; assignable |
| `xref`, `obj` | The group's object number and its dictionary in the [object model](#low-level-objects) |

`is_visible` is the state that rendering and extraction use: content marked
for a hidden group is neither drawn nor extracted. Assigning it edits the
default configuration (`/OCProperties /D`), so `page.pixmap()`,
`page.text()`, and the other readers follow at once and the state is saved.
Showing a group hides the other groups of a radio-button group that holds it,
as viewers do. `doc.layers.add(name, *, visible=True, intent=("View",))`
adds a group, creating the optional-content properties when the document has
none.

```python
notes = doc.layers.add("Reviewer notes", visible=False)
for layer in doc.layers:
    print(layer.name, layer.is_visible)
doc.layers[0].is_visible = False
```

`doc.layers.configurations` lists the configurations, each a
`LayerConfiguration` handle: the default configuration first, which viewers
apply when they open the document and which rendering and extraction use,
then the alternatives that a viewer can switch to.

| `LayerConfiguration` member | Purpose |
|---|---|
| `index` | 0 for the default configuration |
| `name`, `creator` | The name a viewer shows and the application that made it, or `None`; assignable |
| `base_state` | The `LayerBaseState` (`ON`, `OFF`, or `UNCHANGED`) of the groups that `on` and `off` leave out; assignable |
| `on`, `off`, `locked` | Tuples of `Layer` handles: shown, hidden, and not to be changed by the user; a group in both `on` and `off` is hidden; assignable |
| `radio_groups` | Tuples of layers of which at most one shows; assignable |
| `order` | The tree a viewer's layer panel shows: `Layer` handles and `LayerOrder(label, items)` values, where an unlabeled `LayerOrder` after a layer holds its children; assignable |
| `apply()` | Makes the configuration the default; the default keeps its `order` when this one has none |

`doc.layers.configurations.add(name, *, creator=None, base_state=LayerBaseState.ON, on=(), off=())`
adds an alternative.

```python
from pylopdf import LayerOrder

default = doc.layers.configurations[0]
notes, draft = doc.layers
default.order = [LayerOrder("Review", (notes, draft))]
default.radio_groups = [(notes, draft)]
printing = doc.layers.configurations.add("Print", off=[notes, draft])
printing.apply()  # rendering and extraction now hide both
```

Content, annotations, and XObjects join a group through their `/OC` entry,
which names a group or a membership. `LayerMembership(layers, *,
policy=VisibilityPolicy.ANY_ON, expression=None)` is a membership as a value:
`policy` (`ALL_ON`, `ANY_ON`, `ANY_OFF`, or `ALL_OFF`) decides from the
visibility of `layers`, and `expression`, a `LayerExpression(operator,
operands)` with `"and"`, `"or"`, or `"not"` over layers and expressions,
takes precedence where viewers read it. The `layer` property of
`Annotation`, `Link`, `Widget`, and `ImageObject` reads the entry as a
`Layer`, a `LayerMembership`, or `None`; assigning one sets it, writing a new
membership dictionary for a `LayerMembership`, and `del` removes it.
`page.resources.layers` maps the names that the page's marked content uses
(`/OC /Name BDC`) to their groups and memberships.

```python
from pylopdf import LayerExpression, LayerMembership, VisibilityPolicy

notes, draft = doc.layers
doc.images[12].layer = draft  # the image renders only while draft shows
page.annotations[0].layer = LayerMembership((notes, draft), policy=VisibilityPolicy.ALL_ON)
print(page.resources.layers)  # {'OC1': Layer(xref=5, name='Notes'), ...}
```

hayro, which renders and extracts, ignores an annotation's own `/OC`, a
visibility expression, and a membership stored directly in `/Properties`, so
those change only what other viewers show.

Reads stop at 65,536 groups in one array (`LimitError` code `layer_count`)
or `order` and expression entries and 32 levels of either, and 1 MiB of name and intent
text per group or of name, creator, and label text per configuration
(`layer_text_size`); names and labels above 1 MiB are refused before they are
written (`layer_input_size`).

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
| `sanitize(*, scripts=True, actions=True, embedded_files=True, metadata=True, hidden_text=True, comments=True, links=False, form_values=False, thumbnails=True)` | Remove scripts, actions, attachments, metadata, hidden text, and comments; see [Sanitization](#sanitization) |
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
| `form` | The form fields as a mapping from full names to `Field` handles; see [Forms](#forms) |
| `attachments` | Embedded files as a mapping; see [Attachments](#attachments) |
| `pdfa_claim` | The PDF/A part and conformance the file declares, as a `PdfaClaim(part=2, conformance="B")` record, or `None`. This reads the declaration only; it does not validate |
| `xmp` | The XMP metadata packet as text, or `None`. Assign a string to write it and `del doc.xmp` to remove it. Reads and writes stop at 1 MiB; read a larger packet with `doc.catalog["Metadata"].read(max_size=None)`. `metadata` does not change it |
| `language` | The document's language as a complete BCP 47 tag, such as `zh-TW` or `sr-Latn-RS`, or `None`. Assign a tag to write it and `del doc.language` to remove it; a malformed tag raises |
| `page_mode`, `page_layout` | How a viewer opens the document, as `PageMode` and `PageLayout` members such as `PageMode.USE_OUTLINES` or `PageLayout.TWO_COLUMN_LEFT`; `USE_NONE` and `SINGLE_PAGE` when unset. Assign a member or its exact name |
| `mark_info` | The tagged-PDF flags as a `MarkInfo(marked=, user_properties=, suspects=)` record, or `None` when absent. Assigning writes all three flags; `del` removes them |
| `viewer_preferences` | The `/ViewerPreferences` as a `ViewerPreferences` record, or `None` when absent: `hide_toolbar`, `hide_menubar`, `hide_window_ui`, `fit_window`, `center_window`, `display_title`, `non_full_screen_page_mode` (a `PageMode`), `direction` (`ReadingDirection.L2R` or `R2L`), `view_area`, `view_clip`, `print_area`, `print_clip` (`PageBoundary` members), `print_scaling` (`PrintScaling`), `duplex` (`Duplex`), `pick_tray_by_pdf_size`, `print_page_ranges` (zero-based `(first, last)` pairs), `copies`, and `enforce` (preference names), each `None` when absent. Assigning a record writes the fields that are set; `del` removes the dictionary |
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
| `text(*, clip=None, ...)` | The page's text in reading order, or the text in a rectangle |
| `words(*, clip=None, ...)` | The words in reading order, as `Word` records |
| `layout(*, clip=None, chars=False, ...)` | Blocks, lines, and spans as a `Layout`; see [Positioned text](#positioned-text) |
| `selection(start, end, *, clip=None)` | The text a viewer selects by dragging from one display-space point to another, in reading order |
| `search(text, *, clip=None, quads=False, max_hits=4096, ...)` | Case-insensitive search; returns a list of `Rect`, or with `quads=True` one `Quad` per line of each match |
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

The text views share keyword-only options, and `doc.text()` takes those of
`text()` for every page:

| Option | Default | Effect |
|---|---|---|
| `clip` | `None` | Keep the glyphs whose box's center lies inside a display-space rectangle, in the page's reading order |
| `clipped` | `False` | Keep the glyphs that the page boundary or the clip paths hide entirely; `Span.is_clipped` flags them. Invisible text, such as an OCR layer, is always kept |
| `ligatures` | `True` | Keep a ligature character such as U+FB01; `False` expands U+FB00 to U+FB06 to their letters |
| `whitespace` | `True` | Keep whitespace characters; `False` makes each one a space |
| `dehyphenate` | `False` | Remove a hyphen that ends a line between two letters when the word continues in lowercase on the next line of the block, and join the word |
| `unmapped` | `"skip"` | What a glyph without Unicode becomes: `"skip"` leaves it out with a `PylopdfWarning`, `"replacement"` gives U+FFFD, and `"glyph_id"` the character whose code point is its glyph ID (U+FFFD for Type 3 glyphs) |
| `exact_bboxes` | `False` | Box each glyph by its outline, as `page.content()` does, instead of by its advance and the font's ascender and descender; not taken by `text()` |

A dehyphenated word is one `Word` on its first line, whose box spans both
parts, and `Line.text` puts it on that line, while the spans keep their
glyphs; `text()` joins the two lines, and `search()` finds a match across
the join as one rectangle. Each combination of `clipped`, `unmapped`, and
`exact_bboxes` interprets the page once and is cached on its own; a page's
glyphs count once against `DocumentLimits.max_text_glyphs`, at the largest of
its interpretations.

### Rendering { #page-rendering }

| Member | Purpose |
|---|---|
| `pixmap(*, dpi=None, matrix=None, clip=None, alpha=False, background=None, annotations=True, colorspace="rgb")` | Render to a [`Pixmap`](#pixmap) |
| `svg(*, annotations=True, max_size=64 MiB)` | Render to an SVG string, with text drawn as paths |
| `resources` | What the page's resources reach: `resources.images` lists its image objects; see [Images](#images) |

A pixmap is RGB on white at 72 dpi by default and RGBA with `alpha=True`.
Pass `dpi` or `matrix`, not both. Any invertible matrix renders: the pixmap
covers the transformed page, so `Matrix(90) * Matrix(2, 2)` turns the page a
quarter turn clockwise at 144 dpi, and a translation moves no pixels.
`clip` renders only part of the page, and
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
| `draw(*, underlay=False)` | A [canvas](#canvas) that draws text, lines, rectangles, and other paths, images, and pages of any document in order; see [Writing text](#writing-text) |
| `replace_text(search, replacement, *, default_char=None)` | Replace text drawn in simple (single-byte) fonts |
| `operations` | The page's content as [operations](#operations); assignable |
| `clean_contents()` | Write the content as one stream of normalized, balanced operations and drop unused resources; see [Content operations](#operations) |
| `clip_to_rect(rect)` | Remove what lies outside a rectangle and clip the content to it; see [Redaction](#redaction) |
| `redact(areas=None, *, text="remove", images="pixels", graphics="touched", fill=Color(0, 0, 0))` | Remove what redaction annotations or areas cover; see [Redaction](#redaction) |

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
`interior_color` as `Color` records or `None`, `opacity`, `border`, a
`Border` record or `None`, `blend_mode`, a `BlendMode` member or `None`,
`flags`, an `AnnotationFlags` value, `vertices`, `strokes`, and
`line_endings`, `popup_rect`, `is_open`, `in_reply_to`, `replies`,
`rotation`, and `file`, and `xref`, `obj`, and `page`.

`page.annotations.add(specification)` adds the annotation that a
specification describes, with its appearance, so every viewer draws it
alike, and returns the new handle; `del page.annotations[i]` removes an
annotation with its popup. Created annotations are printable and record the
time of their creation in `created` and `modified`.

| Specification | Adds |
|---|---|
| `Highlight(rects, *, color=Color(1, 1, 0), opacity=0.4, contents=None)` | A highlight over one or more rectangles or quads, such as search results, drawn in multiply blending |
| `Underline(rects, *, color=Color(0, 0, 1), opacity=1.0, contents=None)` | A one-point line just above the bottom of each rectangle |
| `StrikeOut(rects, *, color=Color(1, 0, 0), opacity=1.0, contents=None)` | A one-point line through the middle of each rectangle |
| `Squiggly(rects, *, color=Color(1, 0, 0), opacity=1.0, contents=None)` | A zigzag along the bottom of each rectangle |
| `Note(at, *, contents=None, icon=NoteIcon.NOTE, color=Color(1, 1, 0), opacity=1.0, is_open=False)` | A note shown as a 20-point icon whose top-left corner is `at`; `NoteIcon` has `COMMENT`, `HELP`, `INSERT`, `KEY`, `NEW_PARAGRAPH`, `NOTE`, and `PARAGRAPH` |
| `FreeText(rect, text, *, font=None, size=11, text_color=Color(0, 0, 0), color=None, border=Border(0), align="left", rotate=0, callout=None, callout_ending=LineEnding.NONE, opacity=1.0)` | Text in a box, laid out as `Canvas.textbox` lays it out: `color` fills the box, `border` outlines it in the text colour, `rotate` turns the text clockwise as displayed, and `callout` draws a line from the point the text explains, through an optional knee, to the box |
| `Stamp(rect, *, icon=StampIcon.APPROVED, color=None, opacity=1.0, rotate=0, contents=None)` | A rubber stamp: its label as large as fits, centred in a rounded frame, in `color` or the stamp's own colour (green for approval, red for restriction, blue for the rest), turned clockwise by `rotate` as displayed |
| `Caret(at, *, color=Color(0, 0, 1), opacity=1.0, contents=None)` | A caret in a 20-point square whose top-left corner is `at`, pointing up as the page is displayed |
| `FileAttachment(at, data, *, filename, description=None, icon=AttachmentIcon.PUSH_PIN, color=Color(1, 1, 0), opacity=1.0, contents=None, max_size=64 MiB)` | A file embedded in the page behind a 20-point `AttachmentIcon` (`GRAPH`, `PUSH_PIN`, `PAPERCLIP`, `TAG`) |
| `Redaction(rects, *, color=Color(1, 0, 0), fill=Color(0, 0, 0), overlay_text=None, font=None, size=11, text_color=Color(0, 0, 0), align="left", contents=None)` | Areas marked for removal, outlined and crossed out in `color` until applied; applying them with [`page.redact()`](#redaction) removes what they cover, fills them with `fill`, and writes `overlay_text` in a Standard 14 `font` |
| `Square(rect, *, color=Color(1, 0, 0), interior_color=None, border=Border(), clouds=0, opacity=1.0, blend_mode=None, contents=None)` | A rectangle whose border `color` strokes and `interior_color` fills; either may be `None` |
| `Circle(rect, *, ...)` | The ellipse inscribed in `rect`, with the fields of `Square` |
| `LineAnnotation(start, end, *, color=Color(1, 0, 0), interior_color=None, border=Border(), line_endings=(LineEnding.NONE, LineEnding.NONE), opacity=1.0, blend_mode=None, contents=None)` | A straight line with a shape at each end, whose closed shapes `interior_color` fills |
| `Polygon(vertices, *, color=..., interior_color=None, border=..., clouds=0, opacity=..., blend_mode=..., contents=...)` | A closed polygon through two or more points |
| `PolyLine(vertices, *, ..., line_endings=...)` | An open line through two or more points, with the fields of `LineAnnotation` |
| `Ink(strokes, *, color=..., border=..., opacity=..., blend_mode=..., contents=...)` | Freehand strokes, each a sequence of one or more points, drawn with round caps and joins |

A `Border(width=1.0, *, style=None, dashes=())` describes a border: its
width in points, a `BorderStyle` (`SOLID`, `DASHED`, `BEVELED`, `INSET`, or
`UNDERLINE`), and for a dashed border 1 to 32 dash and gap lengths; dashes
without a style select `DASHED`. The border of a square or circle lies inside
its rectangle, and widgets draw the beveled, inset, and underline styles that
other annotations draw solid. `clouds`, from 0 to 2, draws the border of a
square, circle, or polygon as a cloud whose bumps grow with it, inside the
rectangle of a square or circle; `annotation.clouds` reads and sets it. `opacity` and `blend_mode` apply to every paint
of the appearance. Points are `Point` values or pairs in display
coordinates; the rectangle of a line, polygon, polyline, or ink annotation is
the bounds of its points widened for the stroke and the line endings, and one
annotation holds at most 65,536 points (`LimitError` code
`annotation_point_count`). `LineEnding` names the shapes of ISO 32000-2:
`NONE`, `SQUARE`, `CIRCLE`, `DIAMOND`, `OPEN_ARROW`, `CLOSED_ARROW`, `BUTT`,
`REVERSED_OPEN_ARROW`, `REVERSED_CLOSED_ARROW`, and `SLASH`. A handle reads
the points back as `vertices` (a line's two ends, or a polygon's or
polyline's points) or `strokes` (an ink annotation's), and the shapes as
`line_endings`; each is `None` for other types.

Without `font`, free text in WinAnsi characters is drawn in Helvetica and
other text in the face of an installed font extra, as on a canvas. A
Standard 14 font is named in `/DA`, so other viewers can redraw the text,
and an OpenType face is embedded in the appearance. Text that does not fit
its box raises `PdfError` and adds nothing. `rotation` reads how far an
annotation's content is turned clockwise as the page is displayed, 0 for
free text and stamps written upright. `StampIcon` lists the fourteen stamps
of ISO 32000-2 in the order of pymupdf's `STAMP_*` codes.

A file attachment's `file` is an `AnnotationFile` with the `filename`,
`description`, and `size` of the embedded file, `read(*, max_size=64 MiB)`,
and `write(data, /, *, max_size=64 MiB)`, which replaces the data and keeps
the name and description; `file` is `None` for other annotations. The file
is not one of `doc.attachments`, and its data, name, and description are
bounded as attachments' are (`embedded_file_size`,
`embedded_file_input_size`).

Annotations are edited by assigning their properties: `rect` (for the
types that a rectangle places; lines, polygons, polylines, and ink move with
`vertices` and `strokes`), `contents`, `author`, `subject`, `name`,
`language`, `icon` (a `NoteIcon`, `StampIcon`, or `AttachmentIcon` member
that fits the type), `color`, `interior_color`, `opacity`, `flags`,
`border`, `blend_mode`, `rotation` (free text, stamps, carets, and file
attachments), `vertices`, `strokes`, and `line_endings`. `del` removes the
texts, the colours, the border, and the blend mode. Each assignment checks
its value, updates `modified`, and draws the appearance again for the types
that pylopdf draws, at once; there is no `update()`. Edited free text keeps
the installed font extra it was drawn with, and an assignment that leaves
the text without room raises and changes nothing. Assignments to an
annotation stored in the page's `/Annots` array itself store the page's
annotations as objects first, so other handles of such annotations found
by their index go stale.

A handle's `popup_rect` is the display rectangle of its popup, the window
that shows the annotation's text, or `None`: assigning a rectangle adds or
moves the popup, which the page lists after it, and `del` removes it.
`is_open` tells whether the popup, or a note without one, opens when the page
is shown, and is assignable on an annotation with a popup and on a note.
`in_reply_to` is the annotation of the same page that this one answers, or
`None`, assignable and deletable, and `replies` lists the annotations that
answer this one. A note's icon neither zooms nor turns with the page
(ISO 32000-2 §12.5.6.4): viewers that honour this, such as MuPDF and
Acrobat, keep it upright on a turned page, which hayro does not yet do.

The members of `AnnotationType` and `AnnotationFlags` keep the values of
pymupdf's `PDF_ANNOT_*` and `PDF_ANNOT_IS_*` constants.

```python
from pylopdf import AnnotationType, Border, Color, Highlight, Square

note = page.annotations.add(Highlight(page.search("total"), color=Color(1, 0.8, 0), contents="Check"))
page.annotations.add(Square((72, 72, 288, 144), color=Color(0, 0, 1), border=Border(2, dashes=(4, 2))))
for annotation in page.annotations:
    if annotation.type is AnnotationType.TEXT:
        print(annotation.author, annotation.contents)
```

Rendering draws an annotation of any of these types that another producer
wrote without an appearance from its dictionary, without changing the
document; it draws none of them when they would draw more than 262,144 path
points in all or when one holds more points than a created one may.

Annotation reads stop at 4,096 annotations and 1 MiB of text per page, and
the contents of a specification stop at 1 MiB (`LimitError` code
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

`doc.destinations` is a read-only mapping from the names of the document's
named destinations to the `Destination` each leads to, or `None` when it
names no page: the `/Names /Dests` tree first, then the legacy catalog
`/Dests` dictionary. Links and the outline already read named destinations
as the destinations they name. Reads stop at 65,536 names
(`named_destination_count`) and 16 MiB of names
(`named_destination_text_size`).

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
| `Line` | `text`, `bbox`, `direction` (the baseline's unit vector), `is_vertical`, `spans`, `quad` |
| `Span` | `text`, `bbox`, `origin`, `size`, `font`, `is_bold`, `is_italic`, `is_serif`, `is_monospaced`, `color`, `render_mode`, `is_clipped`, `ascender`, `descender`, `chars`, `quad` |
| `Char` | `text`, `bbox`, `origin`, `is_synthetic` |

A span is a run of glyphs in one font and size that are painted alike:
`color` is the paint as an sRGB `Color` whose `alpha` is the opacity (the
fill of filled text, the stroke of text that is only stroked, and `None` for
a pattern paint and for invisible text), `render_mode` is the
`TextRenderMode` that `page.content()` reports as well, and `is_clipped` is
true when the clip paths leave no part of the glyphs visible. `ascender` and
`descender` are the font's, in em, as MuPDF reads them from an OpenType or
TrueType program; hayro hands out no program for Type 1 fonts, the standard
14 fonts among them, which therefore report MuPDF's defaults of 0.8 and
-0.2.

A `bbox` is axis-aligned; a `quad` is the same box along the baseline, the
smallest rectangle in the direction of the first glyph's baseline that holds
every glyph's box, so it follows text that a page sets at an angle; a
character's quad is its span's, cut to the character's advance, which
`page.search(char, quads=True)` gives as well. Text at any angle is
assembled into lines along its baseline.

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

Bounds span the glyph's advance and, across the baseline, the font's
ascender and descender, scaled to one em together when they span less, as
pymupdf's boxes do; `exact_bboxes=True` uses the glyph outlines instead.
Each space that pylopdf infers from a gap is a `Char` whose `is_synthetic`
is true and whose box spans the gap, as MuPDF reports such spaces, so a
span's characters join to its text. Glyphs that have no Unicode mapping in
the PDF are skipped with a `PylopdfWarning` unless `unmapped=` asks for a
placeholder.

`layout.to_dict()` returns plain dictionaries for JSON, in the shape of
[pymupdf's dict, or its rawdict for a layout with characters](https://pymupdf.readthedocs.io/en/latest/textpage.html#structure-of-dictionary-outputs),
with text blocks only, without image or vector blocks. Spans have pymupdf's
`color` (an sRGB integer), `alpha` (0 to 255), `ascender`, `descender`, and
`char_flags` (16 for filled text, 32 for stroked text, and 64 for clipped
text), but not `bidi`; characters have `synthetic`. The
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

## Redaction { #redaction }

`page.redact()` removes what the page's `Redaction` annotations cover and
returns a `RedactionReport`. The content goes from the file, not only from
view: text, images, and vector graphics in the areas are taken out of the
content streams, and the annotations become their fills.

```python
from pylopdf import Color, Rect, Redaction

page = doc.pages[0]
for word in page.search("Confidential"):
    page.annotations.add(Redaction(word, fill=Color(0, 0, 0), overlay_text="REDACTED"))
report = page.redact()
print(report.glyphs, report.images, report.paths, report.annotations)

# Areas without annotations, in display coordinates.
page.redact([Rect(72, 100, 300, 140)], images="remove", fill=None)
doc.save("redacted.pdf")   # a full save; see below
```

| Option | Values |
|---|---|
| `text` | `"remove"` (default) removes every glyph whose box meets an area; `"keep"` leaves text |
| `images` | `"pixels"` (default) blanks the covered pixels in a copy of the image; `"remove"` removes the placements an area touches; `"keep"` |
| `graphics` | `"touched"` (default) removes paths and shadings that meet an area; `"covered"` only those inside one; `"keep"` |
| `fill` | The color that fills areas given directly, or `None`; annotations use their own fill and overlay text |

- A glyph's box spans its advance and the font's ascent and descent. Glyphs
  are removed in every font (simple, CID, and Type 3), in both writing
  modes, and in every rendering mode, so invisible OCR text goes as well. The
  glyphs after a removed one keep their positions through `TJ` adjustments,
  and the `/ActualText`, `/Alt`, and `/E` of the marked content around it go
  with it.
- A blanked image is a new image that only that placement uses, its covered
  pixels opaque black and its masks merged into its alpha; a stencil mask
  stays a stencil mask. Inline images are blanked or removed in the same way.
- A Form XObject placement that loses something is replaced by a copy, so
  other placements and other pages keep the original. A path that also clips
  keeps clipping.
- Annotations and form widgets that overlap an area are removed, with their
  popups; a field goes with its last widget.
- After the edit hayro interprets the page again, and whatever it still
  paints or extracts inside an area is removed in another pass. A page where
  that does not converge raises `PdfError` and stays as it was.
- The bytes the document was opened from still hold the removed content, so
  a redacted document can no longer be saved with `incremental=True`; save it
  in full, and run [`doc.sanitize()`](#sanitization) as well: metadata,
  attachments, bookmarks, and the structure tree can hold the same text
  elsewhere in the file.

`page.clip_to_rect(rect)` removes what lies outside a rectangle with the
same machinery: glyphs that reach outside it, the image pixels outside it,
the paths and shadings entirely outside it, and the annotations outside it,
and then clips the content to it, so what crosses its edge shows only inside.
It returns a `RedactionReport` as well.

| `RedactionReport` field | Counts |
|---|---|
| `areas` | The applied areas, in display space |
| `glyphs` | Glyphs removed |
| `images`, `blanked_images` | Image placements removed, and placements blanked |
| `paths`, `shadings` | Vector graphics removed |
| `annotations` | Annotations and widgets that overlapped an area, the applied redaction annotations not included |
| `forms` | Form XObject placements copied |

## Sanitization { #sanitization }

`doc.sanitize()` removes what a document carries besides its visible pages
and returns a `SanitizationReport`. Run it before sharing a file, after
[redaction](#redaction).

```python
report = doc.sanitize()                       # every category below except links and form values
report = doc.sanitize(links=True, form_values=True, metadata=False)
print(report.scripts, report.embedded_files, report.hidden_glyphs, report.objects)
doc.save("clean.pdf")                         # a full save
```

| Keyword | Default | Removes |
|---|---|---|
| `scripts` | `True` | JavaScript actions wherever they are named, the JavaScript name tree, and an XFA form |
| `actions` | `True` | Automatic actions (`/OpenAction`, every `/AA`) and link or outline actions other than `GoTo`, `URI`, and `Named` |
| `embedded_files` | `True` | Attached files, file attachment annotations, associated files, and portfolios |
| `metadata` | `True` | The Info dictionary and every `/Metadata`, `/PieceInfo`, and `/LastModified` entry |
| `hidden_text` | `True` | Text in an invisible rendering mode, such as an OCR layer; the text around it keeps its position |
| `comments` | `True` | Every annotation except links and widgets, with popups |
| `links` | `False` | Link annotations |
| `form_values` | `False` | Field values, reset to their defaults |
| `thumbnails` | `True` | Page thumbnails |

Objects that nothing reaches any more go in every case. The document can no
longer be saved with `incremental=True` afterwards, since the bytes it was
opened from still hold what went. The call is atomic, except that
`form_values` resets the fields one by one afterwards. The report counts each
category, the fields reset, and the unreachable objects removed.

## Content operations { #operations }

`page.operations` is the page's content as a sequence of operations, for
programs that work below the [content model](#content). It reads the content
streams as the renderer reads them, joined in order, and tokenized with
hayro's parser, so the operations are the ones that rendering and extraction
interpret.

```python
from pylopdf import Name, Operation, Operations, String

ops = page.operations
for op in ops:
    print(op.operator, op.operands)

# Drop marked-content properties, keep everything else as it was written.
page.operations = [op for op in ops if op.operator not in ("BDC", "BMC", "EMC")]

# Draw on top, with an operation written from values.
page.operations = [*ops, Operation("re", (10, 10, 50, 20)), Operation("f")]

# A Form XObject's content.
form = doc.objects[12]
Operations.parse(form.read()).to_bytes() == form.read()   # True
```

| Value | Fields |
|---|---|
| `Operations` | An immutable sequence of `Operation` and `InlineImage` items, with `tail`, the bytes after the last operation; `Operations.parse(data)` and `to_bytes()` |
| `Operation(operator, operands=())` | The operator's text, such as `"Tj"`, and the operand values |
| `InlineImage(dictionary, data)` | An inline image's dictionary as written (`W`, `H`, `BPC`, `CS`, `F`) and its still-encoded data |

Operands are object-model values: `None`, `bool`, `int`, `float`, `Name`,
`String`, tuples for arrays, and dictionaries keyed by name text.

- Every operation that a parse produced keeps its bytes, with the comments
  and white space before it, so `Operations.parse(data).to_bytes() == data`
  and writing back an edited sequence rewrites only the operations that
  changed. New operations are written from their values.
- Assigning `page.operations` writes one new compressed stream as
  `/Contents`, owned by the page, so other pages that shared the old streams
  keep them. Resources are not changed: a new operation that names a font or
  XObject needs the page's `/Resources` to hold it.
- hayro stops reading content at an operation it cannot read, such as one
  with more than ten operands. The rest stays in `tail`, which `to_bytes()`
  writes last; a list of operations has no tail.
- `page.clean_contents()` writes the content as one stream from the
  operations' values: comments, content after where the renderer stops, and
  `Q` operators that restore nothing go, the states left saved are restored
  at the end, and resource entries that nothing names go from a copy the
  page owns. `annotation.clean_contents()` does the same for each appearance
  stream of an annotation, in place. Rendering and extraction do not change.
- `page.operations` reads and writes at most 64 MiB of content and reads at
  most 1,048,576 operations; `Operations.parse(data, max_size=None,
  max_operations=None)` lifts both for trusted content.

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
| `text(text, at, *, font=None, size=11, fill=..., stroke=None, line_height=None, rotate=0)` | Lines whose first baseline starts at `at`, turned clockwise about `at` by `rotate` |
| `textbox(rect, text, *, font=None, size=11, align="left", line_height=None, rotate=0, ...)` | Text wrapped inside `rect`, turned clockwise by `rotate`; returns a `TextFit` |
| `image(rect, source, *, keep_aspect=True, rotate=0)` | A JPEG or PNG path, encoded bytes, or a `Pixmap`, turned clockwise by `rotate` |
| `page(rect, source, *, clip=None, keep_aspect=True, rotate=0)` | A page of any open document, this one included, as vectors: the part inside `clip`, a rectangle of the source page's display coordinates, turned clockwise by `rotate` |
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
`rotate` turns text clockwise by 0, 90, 180, or 270 degrees: `text` turns
its lines about `at`, and `textbox` turns them inside `rect`, so that at 90
they run down the box, wrap at its height, and stack from its right edge
leftward, and at 270 they run up from its left edge. A `transform` scope turns
text by any other angle.
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

Five context managers apply to everything drawn inside them, and nest:

| Scope | Effect |
|---|---|
| `clip(path, *, fill_rule=...)` | Paints only inside the path |
| `transform(matrix)` | Transforms the drawing; nested transforms apply innermost first |
| `opacity(alpha)` | Composites the drawing as one group, then paints it at `alpha` |
| `blend(mode)` | Blends the drawing with what is below it, with a `BlendMode` |
| `mask(*, subtype=SoftMaskType.LUMINOSITY)` | Returns a canvas for the mask: the drawing shows where the mask's drawing is light, or, with `SoftMaskType.ALPHA`, opaque |

```python
with page.draw() as canvas, canvas.mask() as mask:
    mask.rect(page.rect, fill=Color(1, 1, 1))           # white shows the drawing
    mask.circle((300, 400), 120, fill=Color(0, 0, 0))   # black hides it
    canvas.image(page.rect, "photo.jpg")
```

`canvas.tag(role, ...)` on a page of an open document adds structure
elements to the document's structure tree, creating it when the document has
none: the new elements join the tree's single `Document` element when it has
one, and the root otherwise. The canvas's Form XObject gets a `/StructParents`
key of its own in the parent tree, and each element names its marked content
in that Form, so the tags of other content stay as they are. Once a document
is tagged, drawing outside every tag scope is marked as an artifact, so the
document stays tagged. A tag scope cannot start inside an `opacity()` scope
on such a page.

```python
from pylopdf import TagRole

with page.draw() as canvas:
    with canvas.tag(TagRole.HEADING_1):
        canvas.text("Added section", (72, 72), size=18)
    with canvas.tag(TagRole.FIGURE, alt="Company logo"):
        canvas.image((72, 100, 172, 150), "logo.png")
```

The mask canvas draws in the user space where the `mask()` block starts, has
scopes of its own, and draws only inside the block; scopes of the page's
canvas inside the block do not move the mask. Nothing shows outside the
mask's drawing, so an empty mask hides the block's drawing.

`page.draw(underlay=True)` writes below the page's existing content. An image
or page that a canvas draws several times, or that the document placed
before, is stored once, whatever its rotation or clip. A page is drawn as it
was when `canvas.page()` was called; a `clip` fills `rect` in place of the
whole page, at its own aspect ratio unless `keep_aspect=False`, and nothing
outside it is painted. `draw(item)` repeats an item's clips, group
opacities, and blend modes, but not its soft masks; text runs, images, and pattern paints cannot
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
- `tag(role, *, alt=None, lang=None, title=None, actual_text=None,
  scope=None, numbering=None)` makes what the block draws a structure
  element. Roles are the `TagRole` members, such as `PARAGRAPH`, `HEADING_1`
  to `HEADING_6`, `LIST` and `LIST_ITEM`, `TABLE`, `TABLE_ROW`, and
  `TABLE_DATA`, and `FIGURE` with its `alt` text. A `TABLE_HEADER` takes the
  `TableHeaderScope` it heads (`ROW`, `COLUMN`, the default, or `BOTH`), and
  a `LIST` the `ListNumbering` of its items (`NONE`, the default, `DISC`,
  `CIRCLE`, `SQUARE`, `DECIMAL`, `LOWER_ROMAN`, `UPPER_ROMAN`,
  `LOWER_ALPHA`, or `UPPER_ALPHA`). Scopes nest, each line of text inside
  one is a span of it, and content outside every scope, or inside an
  `ARTIFACT` scope, is an artifact.
- `bookmark(title, *, level=1, at=(0, 0))` adds an outline entry that shows
  `at` on the page; a level can grow by one at a time.
- `link(rect, target, *, alt=None)` adds a link to a URI string or to a
  `Destination(page, point)` of the new document, whose page must exist when
  the document is written; `alt` describes it, as PDF/UA requires. In a
  tagged document a link belongs inside a tag scope, usually
  `TagRole.LINK` around the text it covers. Destinations with a zoom and the
  other targets are refused, because krilla writes neither. A page holds at
  most 4,096 links. The rectangle follows the current transform, using its
  bounding box after rotation or shear; the destination stays in the target
  page's display coordinates.

`finish()` and `to_bytes()` can be called again after more pages. Saving the
`Document` that `finish()` returns writes it again with lopdf, so keep
`to_bytes()` for the file exactly as krilla validated it. A document holds at
most 65,536 pages and 65,536 bookmarks, and a tag's texts, a bookmark title,
or a link's URI and description at most 64 KiB (`tag_text_size`).

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

Text is drawn with a [canvas](#canvas): `canvas.text(text, at, ...)` writes
lines from a point, and `canvas.textbox(rect, text, ...)` wraps text inside a
rectangle.

```python
from pylopdf import Color, Font

with page.draw() as canvas:
    canvas.text("CONFIDENTIAL", (40, 40), size=18, fill=Color(1, 0, 0))
    canvas.text("社外秘", (40, 70), size=18)                                    # needs pylopdf[jp]
    canvas.text("简体中文", (40, 100), font=Font.installed(language="zh-CN"))  # needs pylopdf[zh-cn]
```

Choose the font with `font=`:

- nothing: Helvetica for Western European (WinAnsi) text, otherwise a font
  from the installed extra for the text's script;
- `Font.standard(name)`: a Standard 14 font, such as `"Times-Bold"`. Standard
  14 fonts are not embedded and cover Western European characters only;
- `Font.from_file(path)` or `Font.from_bytes(data)`: an OpenType font, of
  which only the glyphs used are embedded;
- `Font.installed(language=...)` or `Font.installed(family=...)`: a font from
  an installed extra.

One font must contain every character of the text; pylopdf does not switch
fonts per character. Text in Kana, Hangul, Bopomofo, Arabic, Hebrew,
Devanagari, or Thai picks the matching installed extra automatically. Chinese
characters alone are ambiguous, so they use the Japanese font by default;
pass `font=Font.installed(language="zh-CN")`, or `"zh-TW"` or `"ko"`, to
choose another.

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
Pass `weight=400` for regular, also for the face that fills a form with
(`doc.form.font`).

The Arabic, Hebrew, Hindi, and Thai extras ship variable fonts, so any weight
on their axis works (100–900; 400–700 for Noto Naskh Arabic). The CJK extras
ship one regular face per style: `Font.installed(language="ja", weight=700)`
raises `PdfError`, so load a bold face, such as Noto Sans JP Bold, with
`Font.from_file()`. pylopdf never imitates bold or italic by distorting
glyphs.

pylopdf does not search system font directories and has no global font
registry, so the output depends only on the fonts you pass in.

### Forms { #forms }

`doc.form` is a mapping from fully qualified field names, such as
`person.first`, to `Field` handles, in name order. A handle reads the
document on every access: `name`, `type` (a `FieldType` member such as
`FieldType.TEXT`, `CHECKBOX`, `RADIO`, `PUSHBUTTON`, `COMBOBOX`, `LISTBOX`,
or `SIGNATURE`), `value`, `flags` (`FieldFlags`, inherited from parent
fields), `choices` (the `FieldChoice(value=, label=)` options of a choice
field), and `xref` and `obj`.

```python
from pylopdf import Font

for name, field in doc.form.items():
    print(name, field.type, field.value)
doc.form["customer"].value = "Alice"   # writes the value and how it looks
doc.form["agree"].value = True         # a check box's on state
doc.form.font = Font.from_file("NotoSansJP-Regular.otf")
doc.form["name"].value = "山田 太郎"
```

Assigning `value` fills text, combo box, list box, check box, and radio
button fields, and regenerates how each one looks. `True` and `False` select
a button's on state and `"Off"`. Western European (WinAnsi) text is sized to
fit in Helvetica, and other scripts use an installed font extra, unless
`doc.form.font` names an OpenType `Font` to draw every fill with, such as
`Font.installed(language="zh-CN")` for Chinese text. That setting belongs to
the `Document` object and is not saved. Comb fields, alignment, and maximum
lengths are respected; a value that is too long raises and leaves the document
unchanged. Rich text is not supported. pylopdf creates signature fields and
reads their state, `field.is_signed` and `doc.form.signature_flags` (a
`SignatureFlags` value), but does not sign or verify; see
[ecosystem recipes](ecosystem.md#signatures) for signing.

`page.widgets.add(specification)` creates a field with its widget on the
page and returns the `Widget`; a specification whose name is already a field
of the form adds another widget to that field, which must be of the same
kind, such as another button of a radio group. `del page.widgets[i]` removes
a widget, and its field with the last one, and `del doc.form[name]` removes a
field with all its widgets. A new field's name has no dots.

| Specification | Creates |
|---|---|
| `TextField(name, rect, *, value=None, default_value=None, max_length=None, font=None, font_size=0, text_color=Color(0, 0, 0), align="left", label=None, flags=FieldFlags(0), border=Border(1), border_color=Color(0, 0, 0), background_color=None)` | A text field; `flags` adds `MULTILINE`, `PASSWORD`, `COMB`, `REQUIRED`, `READ_ONLY`, and the other `FieldFlags` |
| `CheckBox(name, rect, *, checked=False, on_state="Yes", ...)` | A check box drawn with a check mark |
| `RadioButton(name, rect, *, on_state, selected=False, ...)` | One button of the radio group `name` |
| `ComboBox(name, rect, *, choices, value=None, ...)`, `ListBox(...)` | Choice fields whose `choices` are `FieldChoice` records or strings |
| `PushButton(name, rect, *, caption=None, ...)` | A button that holds no value, with its caption |
| `SignatureField(name, rect, ...)` | An unsigned signature field for a signing tool such as pyHanko |

Widgets on a turned page show upright (`/MK /R`). Fields that pylopdf
creates can be filled by pylopdf and by other tools such as pymupdf.

A field also has `label` (its `/TU` description), `max_length` (a text
field's `/MaxLen`), and `default_value` (`/DV`), all assignable and
deletable; `flags` and a choice field's `choices` (an iterable of
`FieldChoice` records or strings) are assignable too, except the flags that
decide the field's kind. `field.reset()` restores the default value, or
clears the value, and draws the widgets again. `doc.form.need_appearances`
reads and sets the form's `/NeedAppearances`, which pylopdf clears once
every fillable widget has an appearance.
`doc.form.repair_radio_groups()` gathers top-level radio buttons that
share a name, which some producers write as separate fields, under one
radio field of that name, and returns how many groups it made.

A field appears on pages through its widgets. `field.widgets` lists them in
page order, and `page.widgets` lists the widgets of a page as `Widget`
handles, which `page.annotations` leaves out. A widget has `field`, the
`Field` it shows, `rect`, `flags` (`AnnotationFlags`, such as `HIDDEN`),
`border` (a `Border`), `border_color` and `background_color` (`/MK`),
`caption` (a pushbutton's label), `text_color`, `font` (the Standard 14 font
that its `/DA` names), and `font_size` (0 fits the text to the widget), and
the `button_states` and `on_state` of a check box or radio button. All but
the button states are assignable, and `del` removes the border, its colours,
and the caption; each assignment draws the widget again for its field's
value, text and choice values in the widget's `/DA` font, size, and colour,
which value assignments now honour as well. A widget's value is its field's
value.

```python
from pylopdf import Border, Color, Font

for widget in doc.form["customer"].widgets:
    widget.background_color = Color(1, 1, 0.8)
    widget.border = Border(1)
    widget.font = Font.standard("Courier")
    widget.font_size = 10
```

Fields and widgets hold JavaScript actions in `scripts`, a mutable mapping
from `ScriptTrigger` to script text. `field.scripts` takes the field triggers
`KEYSTROKE`, `FORMAT`, `VALIDATE`, and `CALCULATE` (a calculated field is
listed in the form's `/CO` calculation order), and `widget.scripts` the
widget triggers `FOCUS`, `BLUR`, `ENTER`, `EXIT`, `DOWN`, `UP`, and
`ACTIVATE`, the action a pushbutton runs. pylopdf stores scripts and never
runs them; number and date formats are Acrobat's format scripts:

```python
from pylopdf import ScriptTrigger

total = doc.form["total"]
total.scripts[ScriptTrigger.FORMAT] = 'AFNumber_Format(2, 0, 0, 0, "", true);'
total.scripts[ScriptTrigger.KEYSTROKE] = 'AFNumber_Keystroke(2, 0, 0, 0, "", true);'
total.scripts[ScriptTrigger.CALCULATE] = 'AFSimple_Calculate("SUM", ["a", "b"]);'
del total.scripts[ScriptTrigger.CALCULATE]
```

A script, and the scripts of one mapping together, are at most 1 MiB of
UTF-8 (`LimitError` code `script_size`). A refused write leaves the mapping
unchanged. Replacing a calculation keeps its existing position in `/CO`.
Script streams use the same PDF text encodings as strings.

### Flattening { #flattening }

`doc.flatten()` draws every annotation and form widget into its page's
content and removes it, so the page shows the same and nothing on it stays
editable. Links stay. `annotations=False` keeps the annotations and
`widgets=False` keeps the form; flattening widgets removes the form with
them, and flattening annotations removes their popups. Each one is drawn
from its normal appearance, mapped onto its rectangle as ISO 32000-2
§12.5.5 describes, or, for an annotation without one, from the appearance
that rendering draws; hidden ones are removed undrawn, and a widget without
an appearance draws nothing, so fill fields before flattening them.

```python
doc.form["name"].value = "Ada Lovelace"
doc.flatten()
assert len(doc.form) == 0 and "Ada Lovelace" in doc.pages[0].text()
```

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

`Attachment` also has `name`, `created`, and `modified`, the dates as the PDF
date strings stored. `filename`, `description`, `created`, and `modified` are
assignable, and `del` removes the last three; `write()` sets `modified` to the
current time. `Annotation.file` of a file attachment has the same members. A
missing name raises `KeyError`.

```python
item = doc.attachments["data.csv"]
item.filename = "rows.csv"
item.created = "D:20260101120000+09'00'"
del item.description
```

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
`Matrix.IDENTITY` is the identity, and `Matrix.planish(p1, p2)` moves `p1`
to the origin and `p2` onto the positive x axis.

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

`Rect.EMPTY` and `Rect.INFINITE` are MuPDF's empty and infinite rectangles,
and `IRect` and `Quad` have the same class attributes. Unlike pymupdf,
checking whether a rectangle lies inside a quad tests all four corners.

## Pixmap { #pixmap }

`Pixmap` holds the pixels of a rendered page or image and cannot be changed.

| Member | Purpose |
|---|---|
| `width`, `height`, `channels`, `alpha`, `stride`, `colorspace` | Size and layout: `channels` is 1 for gray, 2 for gray with alpha, 3 for RGB, and 4 for RGB with alpha; `colorspace` is `Colorspace.GRAY` or `Colorspace.RGB` |
| `resolution` | The horizontal and vertical dots per inch: a rendered page's resolution, else 96; PNG and JPEG output states it |
| `numpy.asarray(pix)`, `memoryview(pix)` | The pixels without copying, shaped `(height, width)` for gray and `(height, width, channels)` otherwise |
| `bytes(pix)` | A copy of the pixels |
| `Pixmap.from_array(array, /, *, resolution=None)` | A pixmap with a copy of a `uint8` array shaped as above, at `resolution`, an `(x, y)` tuple of integers from 1 through 65,535 that defaults to 96; given a `Pixmap`, it shares its pixels and keeps its resolution unless told otherwise |
| `Pixmap.from_file(path, /, *, max_size=64 MiB, max_pixels=64,000,000)`, `Pixmap.from_bytes(data, /, ...)` | Decode a PNG or JPEG: PNG keeps gray, RGB, and alpha as 8-bit samples, JPEG decodes to gray or RGB (CMYK converts), and the resolution comes from the file, else 96; larger input raises `LimitError` with `image_input_size` or `image_pixel_count` |
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
| `create(...)`, `Builder`, `Standard`, `TagRole`, `TableHeaderScope`, `ListNumbering` | See [New documents](#create) |
| `Font`, `StandardFontName` | See [Font](#font) |
| `Name`, `String`, `Array`, `Dictionary`, `Stream`, `Objects` | [Low-level objects](#low-level-objects) |
| `Operations`, `Operation`, `InlineImage` | [Content operations](#operations) |
| `RedactionReport` | [Redaction](#redaction) |
| `SanitizationReport` | [Sanitization](#sanitization) |
| `Content`, `TextRun`, `Path`, `Image`, `Glyph`, `FontRef`, `Color`, `Stroke`, `GraphicsContext`, and the other content types | The [content model](#content) |
| `Colorspace` | The color spaces of a [`Pixmap`](#pixmap): `GRAY` and `RGB` |
| `Outline`, `OutlineItem`, `OutlineEntry`, `Destination`, `RemoteDestination`, `Launch`, `NamedAction`, `PageLabelRange`, `PageLabelStyle` | [Outline and page labels](#outline) |
| `Annotation`, `Annotations`, `AnnotationType`, `AnnotationFlags`, `AnnotationSpecification`, `Highlight`, `Underline`, `StrikeOut`, `Squiggly`, `Note`, `NoteIcon`, `FreeText`, `Stamp`, `StampIcon`, `Caret`, `FileAttachment`, `AttachmentIcon`, `AnnotationFile`, `Redaction`, `Square`, `Circle`, `LineAnnotation`, `Polygon`, `PolyLine`, `Ink`, `Border`, `BorderStyle`, `LineEnding`, `Link`, `Links` | [Annotations and links](#page-annotations) |
| `PageBoxes` | [Page geometry](#page-geometry) |
| `PaperSize` | Standard paper sizes as `(width, height)` pairs of points that `doc.pages.new()` takes: the ISO A, B, and C series and the North American sizes (`LETTER`, `LEGAL`, `TABLOID`, `EXECUTIVE`, `HALF_LETTER`), each with `width`, `height`, and `landscape` |
| `pdf_date(value=None)`, `parse_pdf_date(text)` | Format a `datetime`, or the current local time, as a PDF date string such as `D:20260101120000+09'00'`, and parse one, every part after the year optional, into a `datetime` that is aware when the string gives an offset |
| `Form`, `Field`, `FieldType`, `FieldFlags`, `FieldChoice` | [Forms](#forms) |
| `FallbackFont`, `FallbackFonts` | [Rendering and images](#document-rendering) |

Codes and flags are enum members, with no module-level constants:
`Permissions.PRINT` replaces pymupdf's `PDF_PERM_PRINT`, `AnnotationType.TEXT`
its `PDF_ANNOT_TEXT`, and `AnnotationFlags.HIDDEN` its `PDF_ANNOT_IS_HIDDEN`,
with the same values. `Access` has the members `USER` and `OWNER`, and
`Encryption` describes how to encrypt a saved file. The content model's enums
are `BlendMode`, `FillRule`, `LineCap`, `LineJoin`, `TextRenderMode`,
`ShadingType`, `SoftMaskType`, and `ImageMask`.

Results are records: frozen dataclasses such as `Word`, `Block`, `Line`,
`Span`, `Char`, `Layout`, `Table`, `TableDiagnostics`, `OcrWord`, `Path`,
`Image`, `MarkInfo`, `PdfaClaim`, `OutlineEntry`, `PageLabelRange`, the
outline targets, `DocumentComplexity`, and `ImageCompressionResult`. The API
returns a plain dictionary only from `Layout.to_dict()`, which gives pymupdf's
shape for JSON.

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
