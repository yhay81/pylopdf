# pylopdf API design

Status: adopted on 2026-10-22 together with the [roadmap](../ROADMAP.md)
direction. The rules in section 1 apply to every new and changed API now. The
target signatures in the later sections are proposals: each milestone
confirms or amends them here before implementation. A change to a rule
changes this document first.

The design follows the engines. lopdf holds the document as a graph of
objects, hayro interprets content into paths, glyph runs, and images, and
krilla writes the same kinds of items. pylopdf gives each of these one Python
form, then builds pages, structure, understanding, and editing on top of
them.

## 1. Rules

### One way

- Each task has one way to do it. There are no aliases, synonyms,
  compatibility layers, or deprecated twins. A convenience that only shortens
  another API (`doc.get_page_text(i)` for `doc.pages[i].text()`) is not
  provided.
- Two APIs overlap only where the PDF model itself has two levels, such as a
  form field and its widgets, and this document names each such case.
- Before 1.0, a redesign removes the old API in the same release. Every
  removed or renamed API gets a codemod rule and a migration hint
  ([section 13](#13-migration)).

### Names

- **Properties** expose cheap state of a document or page: `page.rotation`,
  `doc.language`, `page.label`. When changing the state is one atomic edit,
  the property is assignable (`page.rotation = 90`), and a removable state
  also supports `del` (`del doc.xmp`). There are no `get_` and `set_` pairs.
- **Collections** are properties that return live views implementing the
  standard protocols: `doc.pages` is a sequence that supports `del`, `insert`,
  and `extend`; `doc.attachments` and `doc.form` are mappings; and
  `page.annotations` and `page.links` are sequences with `add`.
- **Computed results** are methods named after what they return, with options
  as keyword-only parameters: `page.text()`, `page.words()`,
  `page.layout()`, `page.tables()`, `page.content()`, `page.pixmap()`, and
  `page.svg()`.
- **Actions** are verbs: `save`, `select`, `unlock`, `apply_ocr`, `redact`,
  and `sanitize`.
- Booleans start with `is_` or `has_`. Names use full words, except
  established PDF terms such as `xref`, `bbox`, and `dpi`.
- One spelling per concept across the library: `rect` for a rectangle that
  the PDF stores (a page, an annotation, a widget), `bbox` for an extent that
  pylopdf computes (a glyph, a word, a content item), `index` for a zero-based
  position, and `xref` for an object number.
- A pymupdf name is used where it is also the clearest name by these rules,
  as `Rect`, `Matrix`, `xref`, and `apply_ocr` are. Matching pymupdf is never
  the reason.

### Types

| Kind | Use | Rule |
| --- | --- | --- |
| Handle | Document-backed objects: `Page`, `Annotation`, `Link`, `Field`, `Widget`, `Attachment`, `OutlineItem`, low-level objects | A view of a document, a generation, and an object number that reads the document on every access. Equality compares those; a stale handle raises `StaleObjectError`. |
| Value | Geometry, colors, fonts, pixmaps, content items | Immutable and hashable where its content allows. |
| Record | Results such as `Word`, `Block`, `Table`, `OcrWord`, `Probe` | `@dataclass(frozen=True, slots=True, kw_only=True)` with named fields; no positional tuples. |
| Specification | Input that describes something to create: `Highlight(...)`, `Encryption(...)`, `PageLabelRange(...)` | A frozen dataclass validated on construction. |
| Enum | Codes and flags | `IntEnum`, `IntFlag`, or `StrEnum` classes; no module-level constants. |
| Option | A choice among strings, such as `format="png"` | A `Literal` type, with one `@overload` per value when the value changes the return type. |

Geometry stays `Point`, `Rect`, `IRect`, `Quad`, and `Matrix`: immutable
NamedTuples with geometric operators (`|` for union, `&` for intersection,
`*` for transformation, `~` for inversion), validated against non-finite
coordinates. Their constants are class attributes (`Matrix.IDENTITY`,
`Rect.EMPTY`, `Rect.INFINITE`). A `TypedDict` appears only as the result of
an explicit `to_dict()` that serializes a record for JSON.

### Coordinates

Every position on a page, read or written, is in display space: points from
the top-left corner of the visible page, y downward, after the CropBox offset
and `/Rotate`. `Page.rect` describes this space. On a page with `/Rotate 90`,
a line of text that runs across the unrotated page runs down the display
page, inside `Page.rect`.

The visible area is the CropBox clipped to the MediaBox, as hayro's
`Page::initial_transform` defines it. Page boxes are the one exception to
display space, because they define it: `page.boxes` reports and accepts them
in PDF user space as stored.

### Pages

Page indexes are zero-based everywhere, the outline and page labels included,
and negative indexes count from the end as they do for Python sequences.

### Errors and warnings

All library errors derive from `PdfError`, a `ValueError` subclass:

- `MalformedPdfError` for input that cannot be read as a PDF, including empty
  input;
- `PasswordError` for a wrong password and `EncryptedDocumentError` for an
  operation on a document that is still locked;
- `LimitError` with a stable `code` for every refused resource;
- `StaleObjectError` for a handle whose object or page changed, with
  `StalePageError` for pages;
- `DocumentClosedError` and `OcrError`.

A missing file raises the built-in `FileNotFoundError`. When an engine
refuses input, pylopdf maps the refusal to one of these types; an engine's
panic never reaches the caller as a `BaseException`. Warnings go through the
`warnings` module as `PylopdfWarning`.

### Limits

Untrusted-input caps are keyword-only `max_*` parameters with bounded
defaults and `None` as the trusted opt-out, plus `DocumentLimits` at open
time. Exceeding one raises `LimitError` and returns no partial result.

### Mutation

- **Immediate and atomic.** A method that changes the document plans all
  fallible work, then commits; on refusal nothing changes. Setters on handles
  apply immediately and regenerate appearances in the same operation, so no
  handle has an `update()`.
- **Scoped builders.** A builder that collects drawing before it writes, such
  as the canvas, is a context manager that commits once on a clean exit,
  writes nothing after an exception, and emits a `ResourceWarning` when it is
  collected uncommitted.
- **Consistency.** An editing method keeps every structure that depends on
  what it changes valid, such as appearances, widths and encodings, the
  structure tree, and name and number trees, and its docstring names them.
  When it cannot, it refuses. What it invalidates and cannot repair, such as
  a signature over changed bytes or a PDF/A or PDF/UA claim, it reports
  ([Goal](../ROADMAP.md#goal)).
- **Transactions.** `with doc.transaction():` makes a group of edits atomic
  and records one undo step ([Persistence](../ROADMAP.md#persistence)).
- **Staleness.** A structural change bumps the page generation. `Page`
  handles then raise `StalePageError`, and annotation, link, field, and
  widget handles raise `StaleObjectError` when their page changed or their
  object was deleted.

### Exact preservation (Persistence target)

Untouched values survive full and incremental saves without rounding.
Real numbers retain their source decimal tokens or an exact decimal
representation; Python `float`, Rust `f32`, and Rust `f64` are approximate
views and cannot be the only persistence representation. Exact numeric
access and assignment must not require conversion through a float, and
reading an approximate view must not change the retained value. The final
public shape of exact numeric values is confirmed in
[Persistence](../ROADMAP.md#persistence) before implementation; today's
float-based object model does not meet this target.

### Concurrency

One `Document`, and every handle derived from it, is used by one thread at a
time; distinct documents run in parallel; `Document.render()` parallelizes
inside one call. There is no process-global mutable state, including no font
registry: output depends only on the fonts passed in and the installed font
extras.

### Engines

Each public concept documents the engine that implements it. An engine
limitation surfaces as a typed error or a documented restriction, never as a
silent gap, and its fix is engine work on the roadmap.

### Python 3.11 floor

Python 3.11 is the oldest supported version. Its stable ABI includes
`Py_buffer`, so every wheel exposes the `Pixmap` buffer. `typing.Self` may be
imported at runtime, dataclasses use `slots=True` and `kw_only=True`, and no
syntax newer than 3.11 appears. Public type aliases stay `TypeAlias`
assignments, because a PEP 695 `type` statement needs 3.12 and changes what
`typing.get_args` returns at runtime.

## 2. Object model

```
pylopdf.open(source) / pylopdf.Document()
└─ Document ──────── objects, catalog, trailer          lopdf
   │                 metadata, xmp, language, outline, page_labels,
   │                 attachments, form, layers, images
   ├─ pages: Pages ── sequence of Page
   │  └─ Page ─────── content()                          hayro
   │                  text(), words(), layout(), search(), tables(), markdown()
   │                  pixmap(), svg()                    hayro
   │                  draw() → Canvas                    krilla
   │                  annotations, links, widgets, resources, boxes
   ├─ render(), text(), markdown()   batched over pages
   └─ save(), to_bytes()                                 lopdf
pylopdf.create() → Builder ── page() → Canvas ── finish() → Document   krilla
Values:  Point, Rect, IRect, Quad, Matrix, Font, Pixmap, content items
```

## 3. The graphics model

Reading and writing share one set of item types. `Page.content()` returns
them as hayro interprets the page; `Canvas.draw(item)` paints them again.

| Item | Fields | hayro `Device` | Written as |
| --- | --- | --- | --- |
| `TextRun` | `glyphs` (`Glyph`: glyph ID, Unicode, matrix, advance, ink box), `font`, `render_mode`, `fill` paint, `stroke` | `draw_glyph_run` | krilla `draw_glyphs` |
| `Path` | `segments` (`MoveTo`, `LineTo`, `CurveTo`, `ClosePath`), `fill` paint, `fill_rule`, `stroke` | `draw_path`, `draw_rect` | path operators |
| `Image` | `xref` (0 for inline images and masks), size, bits per component, color space, stencil flag, mask kind, encoded size, `matrix` | `draw_image` | an Image XObject |

Every item also carries its `bbox` in display space, its own `blend_mode` and
`soft_mask`, and a `context`: a `GraphicsContext` that lists, outermost
first, the `clips` (from `push_clip_path`; written with `push_clip_path`),
the transparency `groups` with their opacity, blend mode, and soft mask (from
`push_transparency_group`; written with `push_opacity`, `push_blend_mode`,
and `push_mask`), and the `marked_content` with tags and MCIDs (from
`begin_marked_content`; written with `start_tagged`). Items painted in the
same context share one context object, so `Content` stays a flat sequence and
the nesting is recovered by comparing contexts.

Paints are a `Color` (an sRGB approximation with alpha), a `ShadingPattern`,
or a `TilingPattern`; a shading painted with `sh` is a `Path` that fills the
clipped area with a `ShadingPattern`. A `Stroke` holds a paint and the line
style in display space. Fonts are `FontRef` values with the font program's
PostScript name, weight, and style flags. `Content` is a sequence of items in
paint order with typed views (`content.text_runs`, `content.paths`,
`content.images`).

What hayro does not report yet joins these values as engine work lands: a
color's color space and components, marked-content properties (optional
content layers and `/ActualText`), the font dictionary's object, subtype, and
embedding, the clipping text render modes, the source Form XObject or
annotation of an item, a separate callback for `sh`, and the stream and paint
of image masks. Until the text, word, drawing, and image views derive from
the same interpretation ([Content model](../ROADMAP.md#content-model)), they
interpret the page separately and agree with it on the bundled corpus.

## 4. Documents

| API | Notes |
| --- | --- |
| `pylopdf.open(source, /, *, password=None, limits=None)` → `Document` | `source` is a path, `bytes`, `bytearray`, or `memoryview`. |
| `pylopdf.Document(*, limits=None)` | An empty document; `limits` sets the budgets for the pages it receives. |
| `pylopdf.probe(source, /, *, password=None, max_file_size=None)` → `Probe` | A record of `page_count`, `version`, `is_encrypted`, `is_repaired`, and `metadata` (the Info entries present, by the names `doc.metadata` uses) without a full load. |
| `doc.save(path, /, *, compress=True, prune=True, dedupe=False, renumber=False, object_streams=False, encryption=None, incremental=False)` | Each option is a boolean named after its effect. `compress` and `prune` default to true, as qpdf and pikepdf write files; the four that change objects change the document itself, and handles to removed or renumbered objects go stale. `encryption` takes an `Encryption(user_password=..., owner_password=..., permissions=..., method="aes-256")` value; `None` writes plaintext. Saving over the open file replaces it atomically. `incremental` comes with Persistence. |
| `doc.to_bytes(*, ..., max_size=...)` | The options of `save`. |
| `doc.unlock(password)` → `Access` | `Access.USER` or `Access.OWNER`; a wrong password raises `PasswordError`, and an unencrypted document grants `Access.OWNER`. |
| `doc.needs_password`, `is_repaired`, `is_dirty`, `is_closed`, `permissions` | `needs_password` is true until `unlock` succeeds; `permissions` is a `Permissions` flag value. |
| `doc.path` | The file the document came from, or `None`. |
| `doc.limits`, `doc.complexity`, `doc.pdfa_claim` | |
| `doc.fallback_fonts` | An assignable mapping from a CJK language to the `FallbackFont(sans, serif)` record of the `Font` values that render text set in non-embedded fonts of that language; `del` returns to the installed font extras. |
| `doc.close()`, the context manager | |

## 5. Pages and structure

| API | Notes |
| --- | --- |
| `doc.pages` | `len`, indexing and slicing with negative indexes, iteration. |
| `doc.pages.new(size=(595, 842), *, index=None)` → `Page` | A blank page, appended unless `index` is given, which is clamped as `list.insert` clamps it. |
| `doc.pages.insert(index, pages)`, `doc.pages.extend(pages)` | `pages` is one `Page` or an iterable of pages of one open document, this one or another; `index` is clamped as `list.insert` clamps it. Pages are always copied as independent page objects: pages of another document with their links, annotations, form fields, and structure, each at most once per call, and pages of the same document as new page objects that share content streams and resources. |
| `del doc.pages[i]`, `del doc.pages[a:b]` | Removes pages with their form fields and structure content. |
| `doc.pages.select(indexes)` | Keeps the listed pages in the listed order; a repeated index makes an independent copy. |
| `doc.pages.move(index, to)` | The page ends at index `to` and keeps its page object. |
| `doc.pages.labeled(label)` → `list[int]` | The indexes of pages with a label. |
| `page.index`, `page.document`, `page.xref`, `page.obj` | Two `Page` views are equal when they share document, generation, and index. |
| `page.rect`, `page.rotation`, `page.boxes`, `page.user_to_display`, `page.label` | `rotation` is assignable. `boxes` has `media`, `crop`, `bleed`, `trim`, and `art`, assignable in PDF user space and validated against the MediaBox. `user_to_display` is the `Matrix` from PDF user space to display space. |
| `page.resources` | The fonts, images, Form XObjects, color spaces, and graphics states that the page's resources reach, as handles. |
| `doc.metadata` | A live mutable mapping of the Info entries present: `title`, `author`, `subject`, `keywords`, `creator`, `producer`, `created`, `modified`, and `trapped`, the dates as the PDF date strings stored ([D7](../ROADMAP.md#decisions)). Values are non-empty strings; `del` removes an entry, and assigning a mapping replaces every entry. |
| `doc.version`, `doc.encryption_method`, `doc.pdfa_claim` | The header version, such as `"1.7"`; the encryption the document was opened with, or `None`; and the PDF/A self-declaration as a `PdfaClaim(part, conformance)` record, or `None`. |
| `doc.xmp` | The XMP packet as text, or `None`; assignable and deletable. |
| `doc.language`, `doc.page_mode`, `doc.page_layout`, `doc.mark_info` | Assignable and deletable. `language` is the complete BCP 47 tag, `page_mode` and `page_layout` take `PageMode` and `PageLayout`, and `mark_info` a `MarkInfo(marked, user_properties, suspects)` record. |
| `doc.outline` | A tree of `OutlineItem` handles (`title`, `destination`, `children`, `color`, `bold`, `italic`, `is_open`), editable in place; assigning a list of `OutlineEntry` values replaces the tree. A destination is a `Destination(page, point, zoom)`, a `RemoteDestination`, a `Launch`, a `NamedAction`, or a URI string, the targets that links share. |
| `doc.page_labels` | A list of `PageLabelRange(start, style, prefix, first)` values; assigning a list replaces the ranges. |
| `doc.attachments` | A mapping of `Attachment` handles with `add(name, data, /, *, filename=None, description=None)`; each handle has `filename`, `description`, `size`, `created`, `modified`, `read()`, and `write(data)`. |
| `doc.layers` | Optional-content groups and configurations ([Structure](../ROADMAP.md#structure)). |
| `doc.images` | A mapping of image objects by `xref`; each `ImageObject` has `xref`, `obj`, `width`, `height`, `bits`, `colorspace`, `alternate_colorspace`, `filters`, `soft_mask`, `mask`, `is_stencil`, `extract()` for the encoded image as an `EncodedImage(format, data, resolution)`, and `to_pixmap(alpha=False)`. |

## 6. Reading

| API | Notes |
| --- | --- |
| `page.content(*, text_runs=True, paths=True, images=True, clip=None, annotations=True)` → `Content` | The graphics model of section 3; the three kinds select the items to build. |
| `page.text(*, clip=None, ...)` → `str` | Reading order. |
| `page.words(*, clip=None, ...)` → `list[Word]` | `Word(text, bbox, block, line, index)`. |
| `page.layout(*, clip=None, chars=False, ...)` → `Layout` | `Block`, `Line`, `Span`, and, with `chars=True`, `Char` records with provenance; `to_dict()` serializes it in pymupdf's dictionary shape. |
| `page.search(text, *, clip=None, quads=False, max_hits=4096)` → `list[Rect] \| list[Quad]` | |
| `page.tables(*, strategy="lines", clip=None, ...)` → `list[Table]` | `Table` has `bbox`, `rows`, `cells`, `diagnostics`, `markdown()`, and `to_pandas()`. |
| `page.markdown(...)`, `doc.markdown(pages=None, *, chunks=False, ...)` | With `chunks=True`, a list of chunks, each with its page and display-space boxes. |
| `doc.text(pages=None)` | Batched plain text that shares one interpreter cache. |
| `page.ocr(*, engine=None, dpi=300, clip=None, rotation=0, ...)` → `list[OcrWord]` | Recognizes without editing. |
| `page.apply_ocr(*, engine=None, words=None, ...)` | Adds an invisible text layer, from pylopdf's engine or from `words` that another engine recognized; skips pages that already have text unless told otherwise. |
| `OcrEngine(...)`, `engine.recognize(pixmap)` | |

## 7. Rendering

| API | Notes |
| --- | --- |
| `page.pixmap(*, dpi=None, matrix=None, clip=None, alpha=False, background=None, annotations=True, colorspace="rgb")` → `Pixmap` | `dpi` and `matrix` are exclusive. Named after its result; 0.15's `Page.render()` returned PNG bytes, and a `Pixmap` supports the buffer protocol, so reusing that name would have let `path.write_bytes(page.render())` write raw pixels without an error. |
| `doc.render(pages=None, *, ..., format="png", workers=None, max_size=...)` → `list[bytes]` | The parallel batch of encoded pages, with the options of `page.pixmap()`. |
| `page.svg(*, annotations=True, max_size=...)` → `str` | Text is drawn as paths at 72 dpi; hayro-svg takes no transform. |
| `Pixmap` | `width`, `height`, `channels`, `alpha`, `stride`, `colorspace`, and the buffer protocol (`memoryview(pixmap)`, `numpy.asarray(pixmap)`); `Pixmap.from_array(...)`; `to_bytes(format="png", *, quality=95)`; `save(path, /, *, format=None, quality=95)`. Image processing belongs to NumPy, Pillow, and OpenCV through tested recipes. |

## 8. Writing

`page.draw(*, underlay=False)` returns a `Canvas`; pages of a new document
return one from `builder.page(size)`.

| Canvas API | Notes |
| --- | --- |
| `path(path, *, fill=None, stroke=None, fill_rule=FillRule.NONZERO)` | `path` is a `Path` item or a geometry built with `PathBuilder`, whose constructors also cover sectors, wavy lines, and zigzag lines. |
| `line`, `rect(rect, *, radius=0, ...)`, `circle`, `ellipse`, `polygon`, `bezier` | Shortcuts that build one path. |
| `text(text, at, *, font=None, size=11, fill=..., stroke=None, line_height=None, rotate=0)` | Shaped with HarfRust and embedded as a subset by krilla. The fill and the stroke select the render mode; invisible text belongs to `page.apply_ocr`. `rotate` turns the lines clockwise about `at` in right angles, as `image` turns images; a `transform` scope turns by other angles. |
| `textbox(rect, text, *, font=None, size=11, align="left", line_height=None, rotate=0, ...)` → `TextFit` | Wraps with UAX #14 and places lines as `text` does, the first baseline one ascender below the top; on overflow it draws nothing and reports the shortfall. `rotate` turns the lines inside `rect`, which they then wrap across for 90 and 270. |
| `image(rect, source, *, keep_aspect=True, rotate=0)` | `source` is a path, encoded bytes, a `Pixmap`, or an `ImageObject`; the same image is stored once. |
| `page(rect, source, *, clip=None, keep_aspect=True, rotate=0)` | Places another page, which may belong to the same document. |
| `clip(path)`, `transform(matrix)`, `opacity(alpha)`, `blend(mode)`, `mask(*, subtype=SoftMaskType.LUMINOSITY)`, `layer(layer)`, `tag(role, ...)` | Context managers that scope a graphics state; `tag` scopes build a tagged new document's structure tree. `mask` returns a second canvas, whose drawing in the block is the soft mask of what the block draws on the first, in the user space where the block starts. |
| `bookmark(title, *, level=1, at=(0, 0))` | An outline entry of a new document. |
| `draw(item)` | Paints a content item. |

Strokes take a paint or a `Stroke(paint=..., width=1.0, cap=LineCap.BUTT,
join=LineJoin.MITER, miter_limit=10.0, dashes=(), dash_offset=0.0)` value,
the type that content items carry. Fonts are `Font` values, which have no public
constructor: `Font.standard(name)` for the Standard 14 fonts by PostScript
name (`StandardFontName`), `Font.from_file(path, /, *, index=0,
weight=None)` and `Font.from_bytes(data, /, *, index=0, weight=None)`, both
bounded by `max_size`, and `Font.installed(*, language=None, family=None,
serif=False, weight=None, italic=False)` for the faces of installed font
extras. A `Font` reports `name`, `family`, `weight`, `is_bold`, `is_italic`,
`is_serif`, `ascender`, `descender`, and `units_per_em`, tells with
`has_glyph(char)` whether it has a glyph for a character, and measures text
as the canvas draws it: `text_width(text, size)` for one line, and
`text_height(text, size, *, width, line_height=None)` for the height that
`textbox` needs for the text wrapped `width` points wide, so a page can be
planned before anything is drawn.

`pylopdf.create(*, standard=None, tagged=False, language=None, title=None,
author=None, subject=None, keywords=(), created=None)` returns a `Builder`
for new documents that krilla writes as a whole: `builder.page(size)`
returns a canvas for a new page, the canvas's `tag(role, *, alt=None,
lang=None, title=None, actual_text=None, scope=None, numbering=None)` scopes,
where a table header takes a `TableHeaderScope` and a list a
`ListNumbering`, give the structure tree, `bookmark(title, level=1)` the
outline, and `link(rect, target, *, alt=None)` link annotations to a URI or a
`Destination` of the document, which join the tag scope they are drawn in;
`builder.to_bytes()` returns the file as krilla wrote it, and
`builder.finish()` opens it as a `Document`. `standard` takes one name or
several, such as `("pdf/a-2a", "pdf/ua-1")`; writing validates with krilla's
validators and raises on a violation. A new document embeds every font, so
its text needs an OpenType `Font` until krilla draws the Standard 14 fonts.

## 9. Interaction

| API | Notes |
| --- | --- |
| `page.annotations` | A sequence of `Annotation` handles for the annotations other than links, widgets, and popups, with typed properties (`type`, `rect`, `contents`, `author`, `subject`, `name`, `icon`, `intent`, `created`, `modified`, `color`, `interior_color`, `opacity`, `border`, `blend_mode`, `vertices`, `strokes`, `line_endings`, `popup_rect`, `is_open`, `in_reply_to`, `replies`, `rotation`, `file`, `language`, `flags`, `xref`, `obj`, `page`), `add(specification)` → `Annotation`, and `del`, which removes the popup as well. |
| Annotation specifications | `Highlight`, `Underline`, `StrikeOut`, `Squiggly`, `Note`, `FreeText`, `LineAnnotation`, `Square`, `Circle`, `Polygon`, `PolyLine`, `Ink`, `Stamp`, `FileAttachment`, `Caret`, and `Redaction`: frozen dataclasses whose first argument is the geometry in display coordinates and whose style fields are keyword-only, named as the properties are (`color`, `interior_color`, `border`, `opacity`, `blend_mode`, `contents`). `Square(rect, *, color=Color(1, 0, 0), interior_color=None, border=Border(), clouds=0, opacity=1.0, blend_mode=None, contents=None)` and `Circle` take the same fields, and `Polygon` takes `clouds` too: the intensity of a cloudy border (`/BE`) from 0 to 2, which `Annotation.clouds` reads and sets. The line specification is `LineAnnotation(start, end, *, ..., line_endings=(LineEnding.NONE, LineEnding.NONE))`, because `Line` names the record of `page.layout()`; `PolyLine(vertices, ...)` takes `line_endings` too, `Polygon(vertices, ...)` does not, and `Ink(strokes, ...)` has no interior colour. Handles read the points as `vertices` (lines, polygons, polylines) and `strokes` (ink), and the endings as `line_endings`. `Note(at, *, contents=None, icon=NoteIcon.NOTE, color=Color(1, 1, 0), opacity=1.0, is_open=False)` places a 20-point icon. `FreeText(rect, text, *, font=None, size=11, text_color=Color(0, 0, 0), color=None, border=Border(0), align="left", rotate=0, callout=None, callout_ending=LineEnding.NONE, opacity=1.0)` lays its text out as `Canvas.textbox` does; `rotate` turns clockwise as displayed, as the canvas's `rotate` does, and `Annotation.rotation` reads it back. `Stamp(rect, *, icon=StampIcon.APPROVED, color=None, opacity=1.0, rotate=0, contents=None)` and `Caret(at, ...)` complete the icon types. `FileAttachment(at, data, *, filename, description=None, icon=AttachmentIcon.PUSH_PIN, ...)` embeds a file, which `Annotation.file` reads back as an `AnnotationFile` (`filename`, `description`, `size`, `read()`, `write(data)`), the shape of `Attachment`. `Redaction(rects, *, color=Color(1, 0, 0), fill=Color(0, 0, 0), overlay_text=None, font=None, size=11, text_color=Color(0, 0, 0), align="left", contents=None)` only marks areas; `page.redact` applies them (Rewriting). Style, text, geometry, icon, rotation, and language properties are assignable, and `del` removes the optional ones; each assignment draws the appearance again, as the mutation rules require. Popups and replies are handle properties: `popup_rect` (assignable, `del` removes the popup), `is_open`, `in_reply_to` (assignable), and `replies`. |
| `Border(width=1.0, *, style=None, dashes=())` | A value: the width in points, a `BorderStyle` (`SOLID`, `DASHED`, `BEVELED`, `INSET`, `UNDERLINE`), and the dash pattern that a dashed border needs. |
| Appearances | One engine draws every annotation type that pylopdf creates from the annotation dictionary through the canvas recording, so creation, property edits, and the appearances that rendering synthesizes for annotations without one (on the rendering snapshot only) draw alike. |
| `page.links` | A sequence of `Link` handles (`rect` and `target`, both assignable, plus `xref`, `obj`, and `page`), `add(rect, target)`, and `del`. A target is a URI string, a `Destination(page, point=None, zoom=None)`, a `RemoteDestination`, a `Launch`, or a `NamedAction`; a link that pylopdf cannot follow reads as `None`. |
| `doc.form` | A mapping of `Field` handles by fully qualified name: `value`, `label`, `max_length`, `default_value`, `flags`, and `choices` (assignable), `type`, `widgets`, `scripts`, `is_signed`, and `reset()`, with `doc.form.need_appearances`, `doc.form.signature_flags` (`SignatureFlags`), and `doc.form.repair_radio_groups()`; `del doc.form[name]` removes a field with its widgets. `doc.form.font` is the OpenType `Font` that value assignments draw with, or `None` for Helvetica and the installed font extras; it belongs to the `Document` object, as `doc.fallback_fonts` does, and is not saved. |
| `page.widgets` | `Widget` handles for a field's appearances on the page: `rect`, `field`, `flags`, styling (`border`, `border_color`, `background_color`, `caption`, `text_color`, `font`, `font_size`, all assignable), `button_states`, `on_state`, and `scripts`; `field.widgets` lists a field's widgets. A widget's value is its field's value, the one overlap that the PDF model makes. `page.widgets.add(TextField("name", rect, ...))` and the other field specifications (`CheckBox`, `RadioButton`, `ComboBox`, `ListBox`, `PushButton`, `SignatureField`, frozen dataclasses with the name and rectangle positional and the style keyword-only) create a field with its first widget, or add a widget to the existing field of that name, such as another button of a radio group, and return the `Widget`; `del page.widgets[i]` removes a widget, and its field with the last one. |
| Scripts | JavaScript actions are authored and removed, never run. `field.scripts` maps the field triggers of `/AA` (keystroke, format, validate, calculate) and `widget.scripts` the widget triggers (focus, blur, enter, exit, down, up, and a pushbutton's activation action) from a `ScriptTrigger` member to the source text; both are assignable and support `del`. |
| `doc.destinations` | A read-only mapping from the names of named destinations to `Destination` values. |
| `doc.flatten(*, annotations=True, widgets=True)` | Draws appearances into page content and removes the objects: every annotation but links, with their popups, and every widget, with the form. |

## 10. Rewriting

`page.redact(...)` applies the page's redaction annotations, or areas given
directly, and returns a report that hayro re-interpretation verified.
`doc.sanitize(...)` removes scripts, actions, embedded files, metadata,
hidden text, and comments, and returns a report. `doc.rewrite_images(...)`
recompresses images, and `page.replace_text(search, replacement, ...)`
replaces text in simple fonts.

## 11. Low-level objects

The typed object model exposes lopdf's graph.

- `Document.objects` is an `Objects` mapping from object numbers to indirect
  objects, with `add(value)` and `add_stream(data, dictionary=None, *,
  filter="FlateDecode")` returning the new object number, assignment
  replacing an existing object, and `len()` counting the numbers in use.
  `Document.trailer` and `Document.catalog` are dictionary handles,
  `Page.obj` is the page dictionary, and `Page.xref` its object number.
- Values map to Python types: `None`, `bool`, `int`, `float`, `Name` (a `str`
  subclass without the slash, so it compares equal to its text), `String`
  (`bytes` with a `text` view that decodes PDF text strings), and live
  handles: `Array` (a mutable sequence), `Dictionary` (a mutable mapping
  keyed by name text), and `Stream` (a dictionary with `read(*, decode=True,
  max_size=64 MiB)` and `write(data, /, *, filter="FlateDecode")`).
- A handle addresses an indirect object, or a direct container inside one, by
  object number and a path of keys and indexes, and reads the document on
  every access. References resolve on read; a reference to a missing object
  reads as `None` (ISO 32000-2 §7.3.10), and a chain longer than 32
  references raises.
- Writing is explicit about types. A `str` is a text string (ASCII as is,
  otherwise UTF-16BE), `bytes` and `String` are strings, a name needs
  `Name`, lists and mappings become direct objects nested at most 32 levels
  (`object_value_depth`), an indirect handle inside a container becomes a
  reference, and a handle assigned to `doc.objects[xref]` or passed to
  `add()` is copied. A handle from another document is refused; page
  insertion imports across documents.
- Every write invalidates derived caches and remembered placements, compares
  the page order with a fresh walk, and bumps the page generation when it
  changed.
- Generation numbers are hidden from the mapping: the number is the key, and
  a number stored under several generations resolves to the highest. Handles
  report `xref` and `generation`.
- A handle raises `StaleObjectError` when its object no longer exists, when
  its path no longer leads to a container, or after saving with
  `renumber=True` or unlocking renumbered the objects.

## 12. Enums

| Enum | Members |
| --- | --- |
| `AnnotationType` | the PDF annotation subtypes |
| `AnnotationFlags` | `HIDDEN`, `PRINT`, `NO_ZOOM`, `NO_ROTATE`, `NO_VIEW`, `READ_ONLY`, `LOCKED`, `TOGGLE_NO_VIEW`, `LOCKED_CONTENTS`, `INVISIBLE` |
| `Permissions` | `PRINT`, `MODIFY`, `COPY`, `ANNOTATE`, `FORM`, `ACCESSIBILITY`, `ASSEMBLE`, `PRINT_HQ` |
| `Access` | `USER`, `OWNER` |
| `PageMode`, `PageLayout` | the catalog's `/PageMode` and `/PageLayout` names |
| `Colorspace` | `GRAY`, `RGB` (CMYK when hayro or a conversion renders it) |
| `BlendMode` | the sixteen blend modes of ISO 32000-2 §11.3.5 |
| `FillRule`, `LineCap`, `LineJoin` | `NONZERO`, `EVEN_ODD`; `BUTT`, `ROUND`, `SQUARE`; `MITER`, `ROUND`, `BEVEL` |
| `TextRenderMode` | `FILL`, `STROKE`, `FILL_STROKE`, `INVISIBLE` |
| `ShadingType` | the shading types of ISO 32000-2 §8.7.4.5 |
| `SoftMaskType`, `ImageMask` | `ALPHA`, `LUMINOSITY`; `SOFT`, `EXPLICIT`, `COLOR_KEY` |

Language codes are options, not enums: `FontLanguage` and `CjkFontLanguage`
are `Literal` types of BCP 47 tags such as `"ja"` and `"zh-CN"`, as
`StandardFontName` and `OcrRotation` are of their values.

## 13. Migration

The capability map (`api/pymupdf-parity.json`) records how pylopdf covers
each pymupdf name. Three tools read it:

- looking up a pymupdf name that pylopdf does not provide raises an
  `AttributeError` whose message names the replacement or the milestone that
  adds the capability;
- `python -m pylopdf.migrate` rewrites mechanical replacements in pymupdf
  code and in pylopdf 0.15 code, and marks every other place with a
  `# TODO(pylopdf):` comment;
- `docs/en/llms.txt` lists the replacements for coding assistants.

There is no compatibility layer and no `fitz` module.

## 14. API reset

The reset applies these rules to the existing surface in one release
([API reset](../ROADMAP.md#api-reset)). Each row gets a codemod rule and a
hint.

| Current API | Target |
| --- | --- |
| `Document(filename, stream, filetype, *, password, limits, max_decompressed_size)` and `open` as the same callable | `open(source, /, *, password=None, limits=None)`; `Document()` creates an empty document; the `max_decompressed_size` shorthand goes in favor of `limits` |
| `peek_metadata(filename, stream, password, *, max_file_size)` → `MetadataProbe` | `probe(source, /, *, password=None, max_file_size=...)` → `Probe` |
| `doc[i]`, `len(doc)`, iteration, `load_page`, `page_count` | `doc.pages[i]`, `len(doc.pages)`, iteration over `doc.pages` |
| `new_page(pno, *, width, height)` | `doc.pages.new(size, *, index=None)` |
| `insert_pdf(docsrc, *, from_page, to_page, start_at)` | `doc.pages.insert(index, other.pages[a:b])` or `doc.pages.extend(...)` |
| `fullcopy_page(pno, to)` | `doc.pages.insert(to, doc.pages[pno])` |
| `delete_page(pno)`, `delete_pages(...)` | `del doc.pages[i]`, `del doc.pages[a:b]`, or `doc.pages.select(...)` |
| `select(page_numbers)` | `doc.pages.select(indexes)` |
| `get_page_numbers(label)` | `doc.pages.labeled(label)` |
| `page_xref(pno)` | `doc.pages[pno].xref` |
| `get_page_text`, `get_page_pixmap`, `get_page_images` | the page's own methods |
| `Document.get_text(pages)` | `doc.text(pages=None)` |
| `Document.to_markdown(...)`, `Page.to_markdown(...)` | `doc.markdown(...)`, `page.markdown(...)` |
| `render_pages(pages, ...)` | `doc.render(pages=None, ...)` |
| `save(filename, *, garbage, deflate, use_objstms, encryption, user_pw, owner_pw, permissions)`, `tobytes(...)` | `save(path, /, *, compress, prune, dedupe, renumber, object_streams, encryption=Encryption(...))`, `to_bytes(...)` |
| `authenticate(password)` → `int` | `unlock(password)` → `Access` |
| `needs_pass`, `is_encrypted`, `name`, `is_pdf` | `needs_password`, `path`; `is_pdf` goes |
| `has_links()`, `has_annots()` | `any(page.links for page in doc.pages)` and the same for annotations |
| `metadata` and `set_metadata(m)` | `doc.metadata` as a live mapping |
| `get_xml_metadata()`, `set_xml_metadata()`, `del_xml_metadata()` | `doc.xmp` |
| `language_tag` and `set_language_tag()` | `doc.language` |
| `pagemode`, `set_pagemode()`, `pagelayout`, `set_pagelayout()`, `markinfo`, `set_markinfo()` | `doc.page_mode`, `doc.page_layout`, `doc.mark_info` |
| `get_toc()`, `set_toc()` | `doc.outline` |
| `get_page_labels()`, `set_page_labels()`, `Page.get_label()` | `doc.page_labels`, `page.label` |
| `get_form_fields()`, `set_form_field()` | `doc.form` |
| `extract_image(xref)` | `doc.images[xref].extract()` |
| `get_pdfa_claim()` | `doc.pdfa_claim` |
| `set_fallback_font(...)` | `doc.fallback_fonts`, an assignable mapping from CJK language to `FallbackFont` |
| `Page.number`, `Page.parent` | `page.index`, `page.document` |
| `set_rotation()` | assignment to `page.rotation` |
| `mediabox`, `set_mediabox()`, `cropbox`, `set_cropbox()`, `artbox`, `bleedbox`, `trimbox` and their setters | `page.boxes` in PDF user space |
| `transformation_matrix`, `rotation_matrix`, `derotation_matrix` | `page.user_to_display` |
| `get_text("text")`, `get_text("words")`, `get_text("blocks" / "dict" / "rawdict")` | `text()`, `words()`, `layout()` |
| `search_for(text, ...)` | `search(text, ...)` |
| `find_tables(...)` → `TableFinder` | `tables(...)` → `list[Table]`; `Table.extract()` and `to_markdown()` become `rows` and `markdown()` |
| `get_drawings()`, `get_image_info()` | `content(text_runs=False, images=False).paths`, `content(text_runs=False, paths=False).images` |
| `get_images(full=)` | `page.resources.images` |
| `annots()`, `add_highlight_annot(...)` | `page.annotations`, `page.annotations.add(Highlight(...))` |
| `get_links()`, `insert_link(link)` | `page.links`, `page.links.add(rect, target)` |
| `get_pixmap(...)`, `get_svg_image(...)` | `pixmap(...)`, `svg(...)` |
| `insert_text`, `insert_textbox`, `insert_image`, `show_pdf_page`, `draw_*`, `new_shape()` and `Shape` | `page.draw()` and `Canvas` |
| `get_text_ocr(...)`, `insert_ocr_text_layer(words)` | `ocr(...)`, `apply_ocr(words=...)` |
| `Pixmap.n`, `samples`, `tobytes()`, `save(filename)` | `channels`, the buffer protocol, `to_bytes(format=...)`, `save(path, /, *, format=None)` |
| `Font(fontname, fontfile, fontbuffer, *, language, family, weight, ...)`, `text_length()` | `Font.standard()`, `Font.from_file()`, `Font.from_bytes()`, `Font.installed()`, `text_width()` |
| `Annot` with `info` and `colors` dictionaries | `Annotation` with typed properties |
| `FileDataError(PdfError, RuntimeError)`, `EmptyFileError` | `MalformedPdfError(PdfError)` |
| `AnnotType`, `AnnotFlags`, `EncryptionMethod` | `AnnotationType`, `AnnotationFlags`, `Encryption(method=...)` |
| the 80 module constants (`PDF_ANNOT_*`, `PDF_ENCRYPT_*`, `PDF_PERM_*`, `LINK_*`, `TEXT_ALIGN_*`, `CS_*`, `csGRAY`, `csRGB`, `csCMYK`, `Base14_fontnames`, `Base14_fontdict`) | enum members, `Font.standard()` names, and the `align` strings of `Canvas.textbox` |
| `Identity`, `IdentityMatrix`, `EMPTY_*`, `INFINITE_*`, `FZ_MIN_INF_RECT`, `FZ_MAX_INF_RECT`, `planish_line` | class attributes of the geometry types and `Matrix.planish(p1, p2)` |
| `Word`, `Block`, `ImageItem`, and `PageLabelSpec` tuples; `TextPageDict`, `DrawingInfo`, `ImageInfo`, `LinkInfo`, `FormFieldInfo`, and the other TypedDicts | frozen dataclasses and handles, with `to_dict()` where a JSON form is needed |

Unchanged: `Document.objects`, `catalog`, `trailer`, the value types of
section 11, `attachments`, `limits`, `complexity`, `DocumentLimits`,
`OcrEngine`, `rewrite_images`, `replace_text`, `Rect`, `IRect`,
`Quad`, `Matrix`, `Point`, and the exception types other than the two above.

## 15. Open questions

- How an edit reports what it invalidated, a signature or a conformance
  claim: a warning, a value that the method returns, or a record on the
  document that `save` checks ([Conformance](../ROADMAP.md#conformance)).
- How the specification map's typed APIs for features that pymupdf lacks,
  such as multimedia, 3D, measurement, and article threads, fit the
  collections and handles of section 1 without one family of methods per
  dictionary type.
- Which parts of the HTML and CSS subset for text boxes parley covers, and
  which need pylopdf's own code.
