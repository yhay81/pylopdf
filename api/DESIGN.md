# pylopdf API design

Status: **approved** on 2026-10-20 (drafted 2026-10-19). This document is
roadmap item F0. It defines the rules every public API follows and the target
surface for the API reset (M0) and the parity milestones (M1–M8) in
[ROADMAP.md](../ROADMAP.md). Workstreams implement against it; a change to a
rule changes this document first.

The parity manifest (`api/pymupdf-parity.json`, roadmap F3) records, for each
pymupdf 1.28 API, whether pylopdf provides it under the same name (`same`) or
replaces it (`replaced`), with a migration hint. This document states the
rules and the redesigned APIs; the manifest holds the per-name mapping.

## Confirmed decisions

These choices follow from the rules below. The maintainer confirmed all twelve
on 2026-10-20, before M0 starts.

1. **Display coordinates everywhere** ([G1](#g1--display-coordinates)). On
   rotated pages, pymupdf uses rotated space only for `Page.rect`; it
   extracts text and interprets insertion and placement coordinates in
   unrotated space. pylopdf keeps one rotation-resolved space for every API,
   even under pymupdf names, and also measures the room left on a page in it,
   as `insert_text` does when it leaves out lines below the page.
2. **Reading order is a quality attribute** ([G7](#g7--result-quality)).
   `get_text` returns pylopdf's reading order whatever pymupdf's `sort=False`
   default would produce, and an explicit `sort=False` raises instead of being
   ignored.
3. **A zero-based outline** replaces `get_toc` and `set_toc`, whose one-based
   pages are the only exception to zero-based numbering
   ([M0](#15-api-reset-m0)).
4. **`Document.attachments`** replaces the seven `embfile_*` methods
   ([attachments](#8-metadata-labels-outline-and-attachments)).
5. **A typed object model** replaces the string-based `xref_*` methods
   ([low-level objects](#13-low-level-objects)).
6. **Exceptions.** pymupdf-named `FileDataError` and `EmptyFileError` derive
   from both `PdfError` and `RuntimeError`, and a missing file raises the
   built-in `FileNotFoundError` ([G3](#g3--errors)).
7. **Keyword-only options** ([R2](#r2--same-name-same-meaning)). Parameters
   that pymupdf code passes by keyword are keyword-only, so a positional
   pymupdf call that passes one fails loudly instead of binding to a different
   parameter.
8. **pymupdf's own conveniences stay** (`get_page_text`, `get_page_pixmap`,
   `delete_page`), while pylopdf's own synonyms go (`render_page`,
   `Page.render`, `render_page_svg`).
9. **Field-level form values.** A by-name `Document.form` mapping reads and
   writes field values, while pymupdf's `Widget.field_value` keeps widget-level
   access to the same value ([forms](#10-forms)).
10. **No shared page objects** ([G8](#g8--no-shared-page-objects)). pymupdf's
    `copy_page` and repeated `select` numbers make two page-tree entries share
    one page object; pylopdf always makes independent copies, so it provides
    pymupdf's `fullcopy_page` and replaces `copy_page`.
11. **Page boxes follow pymupdf's box convention**
    ([G1](#g1--display-coordinates)): the MediaBox in PDF coordinates and the
    other boxes with a top-left origin against the MediaBox, because display
    space is itself derived from the boxes.
12. **Encryption is explicit** ([R2.7](#r2--same-name-same-meaning)).
    `save(encryption=)` takes pymupdf's `PDF_ENCRYPT_*` values and defaults to
    pymupdf's `PDF_ENCRYPT_NONE`. pymupdf silently ignores `user_pw=` and
    `owner_pw=` under that default; pylopdf raises, and no longer encrypts
    implicitly when a password is given.

## 1. Rules

### R1 — One API

- Each task has one way to do it. pylopdf adds no synonym of its own for an
  existing API, and ships no compatibility layer, alias module, or deprecated
  twin.
- Two APIs may overlap only where the PDF model itself has two levels, such as
  a form field and its widgets. This document names each such case.
- Before 1.0, a redesign removes the old API in the same release. There are no
  deprecation windows.

### R2 — Same name, same meaning

1. When pymupdf has an API with the meaning pylopdf needs, pylopdf uses
   pymupdf's name, parameter names, constants, dictionary keys, tuple order,
   and defaults, even where a clearer name exists.
2. **Meaning** covers parameters and their defaults, units, page numbering,
   return types and data shapes, error categories, and side effects. It does
   not cover heuristic result quality ([G7](#g7--result-quality)) or resource
   limits ([G4](#g4--limits)).
3. **Signature shape.** Leading parameters that pymupdf code passes
   positionally keep their position and pymupdf's name, such as `option` in
   `get_text("words")` or `pno` in `show_pdf_page(rect, docsrc, pno)`. They
   become positional-only when pymupdf's name is not worth exposing, as in
   `select(pyliste)`, which becomes `select(page_numbers, /)`. Parameters that
   pymupdf code passes by keyword are keyword-only and keep pymupdf's names, so
   a pymupdf call that passes one positionally raises `TypeError` instead of
   binding to a different parameter.
4. **Subsets.** A pymupdf-named API may omit some of pymupdf's parameters.
   Passing an omitted parameter raises `TypeError`, because it is absent from
   the signature. An unsupported value of a supported parameter raises
   `PdfError`, which is a `ValueError`. Neither case is silently ignored or
   reinterpreted.
5. **Extensions.** pylopdf may add keyword-only parameters whose defaults leave
   pymupdf's behavior unchanged, such as `max_*` limits, `font_language=`, and
   `background=`.
6. **Conveniences and aliases.** pymupdf's convenience methods and its own
   aliases are part of the vocabulary. When pylopdf provides the capability, it
   provides them under pymupdf's names and meaning: `Document.get_page_text`
   beside `Page.get_text`, `open` as the same callable as `Document`, `w` and
   `h` beside `width` and `height`, and `tl`, `tr`, `bl`, and `br` on `Rect`.
   These are not pylopdf synonyms under R1.
7. **No silently dropped intent.** Where pymupdf accepts an option and then
   silently skips the only effect the option exists for, pylopdf raises
   instead: `user_pw=` without an encrypting `encryption=` value is the case
   in point. Documented round-trip behavior is not such a case; `set_metadata`
   ignores the read-only `format` and `encryption` keys exactly as pymupdf
   does, so `doc.set_metadata(doc.metadata)` works.

### R3 — Redesign for substance, under a new name

pylopdf replaces a pymupdf design only for one of these reasons:

- hidden state that a forgotten call loses (`Annot.update()`,
  `Widget.update()`);
- objects encoded as strings (`xref_get_key` returns PDF syntax);
- inconsistent numbering (the one-based TOC);
- magic numbers that a typed value cannot keep compatible;
- unsafe or surprising defaults;
- process-global mutable state (`TOOLS`);
- a standard Python protocol — mapping, sequence, iterator, or context
  manager — that replaces a bespoke family of methods (`embfile_*`).

The replacement takes a new name, the pymupdf name is not provided, and the
manifest records a `replaced` relation with a one-sentence `hint`. Style alone
— a `get_` prefix, camelCase dictionary keys, an abbreviation — is never a
reason. Where a typed value can keep pymupdf's shape, pylopdf keeps the name
and upgrades the type instead: an IntFlag keeps an integer constant, and a
NamedTuple keeps a positional tuple.

### R4 — Global conventions

Global conventions apply to every API, including pymupdf-named ones. Each is a
deliberate, documented exception to R2 and appears in the migration guide.

#### G1 — Display coordinates

Every page-level coordinate, in and out, is in rotation-resolved display
space: points, with the origin at the top-left of the visible page and y
downward, after the CropBox offset and `/Rotate`. `Page.rect` describes this
space. pymupdf uses it for `Page.rect` only. It extracts text (`get_text`,
`search_for`) in unrotated space, with y flipped against the page top but no
rotation applied, and maps insertion and placement coordinates through a
transformation that, on rotated pages, likewise flips y without rotating, so
they land in unrotated space too. pylopdf does neither: on a page with
`/Rotate 90`, a word that pymupdf reports running across the unrotated page
runs down the display page in pylopdf, inside `Page.rect`. Code that
multiplies pymupdf's extraction results by `rotation_matrix`, or insertion
coordinates by `derotation_matrix`, must drop that step, and the codemod flags
both. On unrotated pages the two spaces coincide, CropBox offset included.
The same holds where pymupdf measures the room left on a page: `insert_text`
leaves out the lines whose baseline falls below the page, which pymupdf
measures in unrotated space down to user-space y = 0, and pylopdf in display
space down to the bottom of `Page.rect`. The two agree on unrotated pages
whose visible area ends at y = 0, which includes every page that `new_page`
creates.

Page boxes are the exception, because display space is derived from them.
They follow pymupdf's box convention: `mediabox` and `set_mediabox` use PDF
coordinates as stored, while `cropbox`, `artbox`, `bleedbox`, `trimbox`, and
their setters use unrotated coordinates with the y axis flipped against the
MediaBox top, and the setters require the box to lie inside the MediaBox.

#### G2 — Zero-based pages

Every page number is zero-based. Negative numbers count from the end where
pymupdf also accepts them, and follow pymupdf's normalization rules where it
defines them (`insert_pdf` ranges). The outline follows the same rule
(decision 3).

#### G3 — Errors

All library errors derive from `PdfError`, a `ValueError` subclass.
pymupdf's names exist where their meaning matches:
`FileDataError(PdfError, RuntimeError)` for malformed input and
`EmptyFileError(FileDataError)` for empty input. A missing file raises the
built-in `FileNotFoundError`; pymupdf code that catches
`pymupdf.FileNotFoundError` migrates to it. `StaleObjectError(PdfError)` is
the base for stale handles, with `StalePageError` as its page subclass.
`LimitError` and its `code`, `PasswordError`, `DocumentClosedError`,
`EncryptedDocumentError`, and `OcrError` keep their meaning. Warnings go
through the `warnings` module as `PylopdfWarning`; there is no warnings store.

#### G4 — Limits

Untrusted-input caps are keyword-only `max_*` extensions with bounded
defaults and `None` as the trusted opt-out, plus `DocumentLimits` at open
time. Exceeding one raises `LimitError` with a stable code and returns no
partial result.

#### G5 — Keyword-only options

See R2.3. Options never bind positionally.

#### G6 — Concurrency

One `Document`, and every handle derived from it, is used by one thread at a
time; distinct documents run in parallel; `render_pages` parallelizes inside
one call. There is no process-global state.

#### G7 — Result quality

Reading order, word and line segmentation, table detection, bounding-box
tightness, and rasterization details are pylopdf's own. pylopdf aims to be
better than pymupdf there and documents its algorithms, but these properties
are not part of a name's meaning. Where pymupdf exposes a quality knob that
pylopdf does not implement, the knob raises instead of being ignored.

#### G8 — No shared page objects

Each page-tree entry has its own page object, as the PDF page tree's single
`/Parent` per page requires; pylopdf's page-tree walker rejects reused page
references. Where pymupdf repeats a reference to one page object — `copy_page`
and repeated numbers in `select` — pylopdf makes an independent copy. pylopdf
therefore provides pymupdf's `fullcopy_page`, which already has that meaning,
and replaces `copy_page`; repeated `select` numbers keep pymupdf's name and
produce independent copies under this convention.

### R5 — Naming new APIs

New capabilities without a pymupdf counterpart follow pymupdf's patterns, so
the vocabulary stays uniform:

- `get_*` for computed results (`get_outline`), plain properties for cheap
  state (`page.rotation`), and `set_*` methods where pymupdf changes state
  through methods (`set_rotation`);
- `insert_*` for content added to a page, `add_*` for annotations and widgets,
  `delete_*` for removal, and `extract_*` for raw data;
- `is_*` and `has_*` for booleans;
- collections as properties that return live views (`doc.attachments`,
  `doc.form`);
- full words in new names, except established PDF terms such as `xref`, `pno`,
  `dpi`, and `bbox`.

### R6 — Types

| Kind | Use | Rule |
| --- | --- | --- |
| Handle | Document-backed objects: `Page`, `Annot`, `Widget`, `Link`, `Attachment`, outline items, low-level objects | A view of a document, a generation, and an object number. Equality compares those, and a stale handle raises `StaleObjectError`. |
| NamedTuple | pymupdf's positional records: words, blocks, image inventory | Keeps pymupdf's order and length and adds field names. |
| TypedDict | pymupdf's dictionary formats: `dict` and `rawdict` text, drawings, links, metadata, image info | A plain `dict` at runtime with pymupdf's keys. A TypedDict never takes a pymupdf class name: today's `TextPage` and `RawTextPage` become `TextPageDict` and `RawTextPageDict` in M0, because M1 gives `TextPage` to pymupdf's handle. |
| Frozen dataclass | Richer records without a pymupdf counterpart | `@dataclass(frozen=True, slots=True, kw_only=True)`. |
| IntEnum and IntFlag | Codes and flags | pymupdf's module constants are members, as `re.IGNORECASE` is a member of `re.RegexFlag`; values equal pymupdf's. |
| Geometry | `Point`, `Rect`, `IRect`, `Quad`, `Matrix` | Immutable values with pymupdf's value-returning members and operators (roadmap F4). They remain NamedTuples for indexing, length, unpacking, and hashing, but `+`, `-`, `*`, `/`, `\|`, `&`, `in`, `abs()`, and truth testing take pymupdf's geometric meaning instead of the tuple operations, and `Rect.width` and `height` clamp at 0 as pymupdf's do. pymupdf's in-place mutators map to operators; `normalize()`, which no operator expresses, becomes `normalized()`, which returns a new value. |
| `Literal[...]` | String options such as `"words"` or `output="png"` | One `@overload` per value when the value changes the return type. |

Every public API is fully typed, so `page.get_text("words")` type-checks as
`list[Word]`.

### R7 — Mutation

- **Immediate and atomic.** A method that changes the document plans all
  fallible work, then commits; on refusal nothing changes. Setters on handles
  apply immediately and regenerate appearances in the same operation, so
  handles have no `update()`.
- **Builders.** `Shape` keeps pymupdf's `draw_*`, `finish()`, and `commit()`.
  It is also a context manager that commits on a clean exit and writes nothing
  after an exception, and an uncommitted `Shape` emits a `ResourceWarning`
  when it is collected.
- **Transactions** (M7). `with doc.transaction(name):` makes a group of edits
  atomic and records one journal step. It replaces pymupdf's
  `journal_start_op()` and `journal_stop_op()` pair; `journal_enable()`,
  `journal_undo()`, `journal_redo()`, and the other journal queries keep
  pymupdf's names and meaning.
- **Staleness.** A structural change bumps the page generation. `Page`
  handles then raise `StalePageError`, and annotation, widget, and link
  handles raise `StaleObjectError` when their page changed or their object was
  deleted.

### R8 — Python 3.11 floor

Python 3.11 is the oldest supported version, as it is for pymupdf 2.0. Its
stable ABI includes `Py_buffer`, so every wheel exposes the Pixmap buffer.
`typing.Self` may be imported at runtime, dataclasses use `slots=True` and
`kw_only=True`, and no syntax newer than 3.11 appears. Public type aliases
stay `TypeAlias` assignments: a PEP 695 `type` statement needs 3.12 and
changes what `typing.get_args` returns for the alias at runtime.

String options stay `Literal` strings rather than `StrEnum` members. This is a
design choice, not a version limit, since 3.11 has `StrEnum`: an enum member
that compares equal to its string would be a second spelling of every option,
which [R1](#r1--one-api) excludes, while `Literal` keeps pymupdf's string
arguments as they are and type-checks each value. Codes and flags remain
IntEnum and IntFlag classes ([R6](#r6--types)).

## 2. Object model

```
pylopdf.open() / Document()
└─ Document ─────────── metadata, outline, labels, attachments, form, layers*
   ├─ Page (handle) ─── text, search, tables, images, drawings, links, pixmaps
   │   ├─ Annot (handle)
   │   ├─ Widget (handle)*
   │   └─ Shape (builder)*
   ├─ Attachment (handle)
   ├─ low-level objects (handles)*
   └─ render_pages() → bytes      (parallel batch)
Values:  Point, Rect, IRect, Quad, Matrix, Colorspace, Pixmap, Font*
Records: Word, Block, ImageItem, TypedDict formats, frozen dataclasses
* planned after M0
```

## 3. Opening, saving, and document state

| Target | pymupdf | Relation | Notes |
| --- | --- | --- | --- |
| `Document(filename=None, stream=None, filetype=None, *, password=None, limits=None, max_decompressed_size=None)`, with `open` as the same callable | `Document(...)`, `open = Document` | same | M0 moves `password` and the limits behind `*` in both, so pymupdf's third positional `filetype` cannot bind to `password`; today `Document()` and `open()` both take `password` third. With no source, `Document()` creates an empty document. `filetype` accepts `"pdf"`; images and CBZ follow in M2. |
| `save(filename, *, garbage=0, deflate=False, use_objstms=False, encryption=PDF_ENCRYPT_NONE, user_pw=None, owner_pw=None, permissions=..., ...)` | `save(...)` | same | M0 renames `object_streams=` to `use_objstms=` and adds `encryption=` with pymupdf's constants and default: `PDF_ENCRYPT_NONE` writes plaintext and `PDF_ENCRYPT_AES_256` encrypts; `PDF_ENCRYPT_AES_128` and `PDF_ENCRYPT_KEEP` follow in M7, and the RC4 values raise. A password under `PDF_ENCRYPT_NONE` raises (R2.7). `garbage` takes pymupdf's levels 0–4 as plain integers (F6), and M7 adds the remaining options. pylopdf can save over the open file through atomic replacement, which pymupdf refuses without `incremental=True`; removing an error changes no meaning. |
| `tobytes(*, ...)`, `write(*, ...)` | `tobytes`, `write` | same | The same options as `save`; `write` arrives with M7. |
| `ez_save(filename, ...)` | `ez_save` | same | M7. |
| `authenticate`, `needs_pass`, `is_encrypted`, `is_repaired`, `page_count`, `metadata`, `close`, the context manager | same names | same | `metadata` gains pymupdf's `trapped` and `encryption` keys in M0. `authenticate` returns pymupdf's codes in M0: today, a document that opened with an empty user password returns 1 for any password, where pymupdf still checks it and returns 0, 2, 4, or 6. |
| `set_metadata(m)` | same | same | M0 adopts pymupdf's meaning: unknown keys raise, the read-only `format` and `encryption` keys are ignored so a `metadata` round trip works, `trapped` is written, a false or `"none"` value removes its key, and an empty dictionary removes the Info dictionary. Today the read-only keys raise and an empty dictionary changes nothing. |
| `limits`, `complexity`, `get_pdfa_claim()`, `peek_metadata()`, `set_fallback_font()` | — | pylopdf | No pymupdf counterpart. |

## 4. Pages and page structure

| Target | pymupdf | Relation | Notes |
| --- | --- | --- | --- |
| `doc[i]`, `len(doc)`, iteration, `load_page(pno)`, `pages(start, stop, step)` | same | same | `pages()` arrives with M2. |
| `new_page(pno=-1, *, width=595, height=842)` | same | same | M0 makes `width` and `height` keyword-only. |
| `insert_pdf(docsrc, *, from_page=-1, to_page=-1, start_at=-1, ...)` | same | same | M0 adopts pymupdf's range rules: a negative `from_page` means the first page, a negative `to_page` the last, and both clamp to the source. pylopdf counts a negative `from_page` from the end today, which is the conflict M0 removes. `links=`, `annots=`, `widgets=`, and `rotate=` follow pymupdf's meaning as they land. |
| `delete_page(pno=-1)`, `delete_pages(...)` | same | same | M0 adds pymupdf's default and argument forms: `(from_page, to_page)`, the `from_page=`/`to_page=` keywords, and an iterable. |
| `select(page_numbers, /)` | same | same | Repeated numbers make independent copies ([G8](#g8--no-shared-page-objects)). |
| `fullcopy_page(pno, to=-1)` | `fullcopy_page` | same | M0 renames today's `copy_page`, whose independent copy is `fullcopy_page`'s meaning. |
| — | `copy_page(pno, to=-1)` | replaced | pymupdf's shared page reference violates G8. The hint reads: "pylopdf never shares page objects; use fullcopy_page". |
| `move_page`, `insert_page` | same | same | M2. |
| `Page.rect`, `rotation`, `set_rotation` | same | same | |
| `Page.mediabox`, `set_mediabox(rect)` | same | same | PDF coordinates ([G1](#g1--display-coordinates)). M0 adopts pymupdf's side effect: setting the MediaBox removes the CropBox, ArtBox, BleedBox, and TrimBox. |
| `Page.cropbox`, `set_cropbox(rect)` | same | same | M0 adopts pymupdf's box convention: unrotated coordinates with the y axis flipped against the MediaBox top, required to lie inside the MediaBox. Today they use PDF coordinates with a bottom-left origin. Art, Bleed, and TrimBox follow in M2 under the same convention. |

## 5. Extraction

### 5.1 Text and search

| Target | pymupdf | Relation | Notes |
| --- | --- | --- | --- |
| `Page.get_text(option="text", *, clip=None, flags=None, textpage=None, sort=True, delimiters=None)` | same | same | Options `text`, `words`, `blocks`, `dict`, and `rawdict` now; `html`, `xhtml`, `xml`, `json`, and `rawjson` in M1, with `clip=`, `flags=`, and `delimiters=`. `sort=False` raises ([G7](#g7--result-quality)). `textpage=` accepts the object from `get_textpage()` and reuses its interpretation. |
| `Page.get_textpage(*, clip=None, flags=0)` | same | same | M1. A handle on pylopdf's cached interpretation. |
| `Document.get_page_text(pno, option="text", *, ...)` | same | same | A convenience (R2.6). |
| `Document.get_text(pages=None)` | — | pylopdf | Batched plain text with one shared interpreter cache. pymupdf's module-level `get_text(path, ...)` is a different object. |
| `Page.search_for(text, *, clip=None, quads=False, flags=None, textpage=None, max_hits=4096)` | same | same | M0 renames the parameter `needle` to pymupdf's `text`; `clip=` and `quads=` arrive in M1. |
| `Word` and `Block` NamedTuples | the `words` and `blocks` tuples | same | `(x0, y0, x1, y1, word, block_no, line_no, word_no)` and `(x0, y0, x1, y1, text, block_no, block_type)`. |
| `TEXT_*` flags | same | same | IntFlag members, implemented in M1. |

### 5.2 Tables and Markdown

| Target | pymupdf | Relation | Notes |
| --- | --- | --- | --- |
| `Page.find_tables(*, clip=None, strategy=None, ...)` | same | same | M0 makes `strategy` keyword-only, as pymupdf code passes it. pymupdf's tuning keywords arrive in M6 and raise until then. |
| `TableFinder.tables`, `Table.extract()`, `Table.to_markdown(*, clean=False, fill_empty=True)`, `Table.to_pandas()` | same | same | `to_pandas()` needs optional pandas (M6). |
| `Document.to_markdown(pages=None, *, table_strategy=..., ...)`, `Page.to_markdown(...)` | pymupdf4llm `to_markdown` | pylopdf | Keyword names follow pymupdf4llm (`page_chunks`, `write_images`, `margins`, and the rest in M6). |
| `Page.get_layout()` | pymupdf-layout's `use_layout()` mode | replaced | M6, with the `[layout]` extra. R3: a process-global mode becomes a per-page result. |

### 5.3 Images, drawings, and fonts

| Target | pymupdf | Relation | Notes |
| --- | --- | --- | --- |
| `Page.get_images(*, full=False)` → `list[ImageItem]` | same | same | M0. NamedTuples in pymupdf's order — `xref, smask, width, height, bpc, colorspace, alt_colorspace, name, filter` — plus `referencer` with `full=True` (`ImageItemFull`). This replaces today's decoded-placement result. |
| `Page.get_image_info(*, hashes=False, xrefs=False)` | same | same | M0 for placements and `xrefs=`; `hashes=` in M1. A TypedDict with pymupdf's keys. |
| `Document.extract_image(xref)` | same | same | M0. JPEG passthrough or PNG, under the existing image limits. |
| `Document.get_page_images(pno, *, full=False)` | same | same | A convenience (R2.6). |
| `Page.get_drawings(*, extended=False)` | same | same | M0 adds pymupdf's `seqno` and `layer` keys; `extended=True` arrives in M1. The camelCase keys stay (R3). |
| `Page.get_fonts`, `get_xobjects`, `Document.extract_font`, and the related inventory | same | same | M1. |

## 6. Rendering and Pixmap

| Target | pymupdf | Relation | Notes |
| --- | --- | --- | --- |
| `Page.get_pixmap(*, matrix=None, dpi=None, colorspace=None, clip=None, alpha=False, annots=True, background=None)` | same | same | M0: the default becomes pymupdf's opaque RGB output (`n == 3`, white background), `alpha=True` keeps transparency, and `scale=` gives way to `matrix=` and `dpi=`. `background=` is an extension. Arbitrary matrices, `colorspace=`, and `annots=` land in M1. |
| `Document.get_page_pixmap(pno, *, ...)` | same | same | A convenience (R2.6); it replaces `render_page`. |
| `Document.render_pages(pages=None, *, matrix=None, dpi=None, colorspace=None, alpha=False, clip=None, annots=True, background=None, output="png", jpg_quality=95, workers=None, max_size=...)` → `list[bytes]` | — | pylopdf | The parallel batch, aligned with the `get_pixmap` options; encoding runs inside the workers. |
| `Page.get_svg_image(*, matrix=None, text_as_path=True)` | same | same | M0 renames `render_svg`. `text_as_path=False` raises until M1 can honor it. |
| `Pixmap`: `width` and `w`, `height` and `h`, `n`, `alpha`, `stride`, `samples`, `samples_mv`, `colorspace`, `irect`, `size`, `xres`, `yres` | same | same | `w` and `h` are pymupdf's own aliases. `colorspace` returns a `Colorspace(name, n)` value, with `csGRAY`, `csRGB`, and `csCMYK` as constants. `samples_mv` is a read-only `memoryview` of the pixel storage on every wheel, without a copy. |
| `Pixmap.tobytes(output="png", jpg_quality=95, *, max_size=...)`, `Pixmap.save(filename, output=None, jpg_quality=95)` | same | same | M0 renames `save`'s parameter `path` to pymupdf's `filename`. JPEG and PNM land in M1. |
| `Pixmap` constructors and analysis (`pixel`, `color_count`, `is_unicolor`, `digest`, `copy`, `shrink`, `invert_irect`, and the rest) | same | same | M1. Constructors follow pymupdf's argument forms only where they are unambiguous ([open questions](#16-open-questions)). |

## 7. Generation

| Target | pymupdf | Relation | Notes |
| --- | --- | --- | --- |
| `Page.insert_text(point, text, *, fontsize=11, lineheight=None, fontname="helv", fontfile=None, color=None, ...)` → `int` | same | same | M0 returns pymupdf's count of inserted lines instead of `None`, splits a string with `str.splitlines()` and uses a list of lines as given, leaves out lines whose baseline falls below the page ([G1](#g1--display-coordinates)), inserts nothing for empty text instead of raising, and places lines at pymupdf's line height. `fontbuffer=`, `fontindex=`, and `font_language=` are extensions. `rotate=`, `morph=`, `render_mode=`, and opacities land in M2. |
| `fontname=` on `insert_text` and `insert_textbox` | same | same | M0 accepts pymupdf's full Standard 14 names (`Helvetica`, `Times-Roman`, `Courier-Bold`, and the rest) besides the short aliases (`helv`, `tiro`, `cobo`, ...) that work today, and exports `Base14_fontnames` and `Base14_fontdict`. |
| `Page.insert_textbox(rect, text, /, *, ..., expandtabs=1, lineheight=None, ...)` | same | same | M0 changes the `expandtabs` default from 8 to pymupdf's 1 and places lines at pymupdf's line height. The second parameter is positional-only because pymupdf names it `buffer`. |
| `Page.insert_image(rect, *, filename=None, stream=None, pixmap=None, rotate=0, keep_proportion=True, overlay=True, ...)` → `int` | same | same | M0 returns the image xref, as pymupdf does, instead of `None`. `xref=`, `mask=`, `alpha=`, and `oc=` land in M2. |
| `Page.show_pdf_page(rect, docsrc, pno=0, *, keep_proportion=True, overlay=True, ...)` → `int` | same | same | M0 renames the parameter `src` to pymupdf's `docsrc` and returns the Form XObject's xref, as pymupdf does; `clip=`, `rotate=`, and `oc=` land in M2. |
| `Page.new_shape()` → `Shape` | same | same | M2, under the builder rules in R7. |
| `Font`, `get_text_length()`, `Page.insert_font()` | same | same | M2. |
| `Page.replace_text()`, `Page.insert_ocr_text_layer()` | — | pylopdf | No pymupdf counterpart. |

## 8. Metadata, labels, outline, and attachments

| Target | pymupdf | Relation | Notes |
| --- | --- | --- | --- |
| `metadata`, `set_metadata(m)` | same | same | |
| `get_page_labels()`, `set_page_labels(labels)`, `Page.get_label()` | same | same | |
| `Document.get_outline()` → `list[OutlineEntry]`, `Document.set_outline(entries)` | `get_toc()`, `set_toc()` | replaced | M0. `OutlineEntry(level, title, page)` is a NamedTuple with a zero-based `page`; M5 adds destinations, styles, and collapse state to it, so `get_outline()` and `set_outline()` round-trip everything an outline item holds. Editing that list replaces pymupdf's `set_toc_item` and `del_toc_item` (R1), while pymupdf's `Document.outline` and `Outline` items keep pymupdf's names and meaning in M5. The hint reads: "pylopdf's get_outline uses zero-based pages; get_toc used one-based pages". |
| `Document.attachments` → `Attachments` | `embfile_*` | replaced | M0. A `Mapping[str, Attachment]` with `add(name, data, /, *, filename=None, description=None, max_size=...)` and `del attachments[name]`. An `Attachment` has `name`, `filename`, `description`, `size`, `creation_date`, `mod_date`, `read(*, max_size=...)`, and `write(data, /, *, max_size=...)`. |
| `get_xml_metadata()`, `set_xml_metadata()`, `del_xml_metadata()` | same | same | M5. |
| Layers: `get_ocgs`, `add_ocg`, `set_layer`, `get_layer`, and the rest | same | same | M5, followed by `oc=` on the insertion APIs. |

## 9. Annotations and links

| Target | pymupdf | Relation | Notes |
| --- | --- | --- | --- |
| `Page.annots(*, types=None)` → `list[Annot]` | same | same | M0 returns read-only `Annot` handles (`type`, `rect`, `info`, `colors`, `flags`, `xref`); M3 adds setters and creators. A list is a superset of pymupdf's generator. |
| `Annot.set_*` setters | same | same | M3. They apply immediately, and there is no `update()` (R7). The hint reads: "pylopdf applies annotation changes immediately; remove the update() call". |
| `Page.add_highlight_annot(quads=None, *, start=None, stop=None, clip=None, color=..., opacity=..., content=None)` → `Annot` | same | same | M0 renames the first parameter to pymupdf's `quads`, returns the new handle, and keeps `color=`, `opacity=`, and `content=` as extensions. |
| The other `add_*_annot` creators, `delete_annot`, `load_annot` | same | same | M3. |
| `Page.get_links()` | same | same | M0 adds pymupdf's `xref` and `id` keys. |
| `Page.insert_link(link, /)` | same | same | M0 replaces pylopdf's `add_link_annot(rect, uri)` for URI links; other kinds, `update_link`, and `delete_link` follow in M3. |

## 10. Forms

| Target | pymupdf | Relation | Notes |
| --- | --- | --- | --- |
| `Page.widgets(*, types=None)` → `list[Widget]` | same | same | M3. pymupdf's attributes; assignment applies immediately and regenerates the appearance, so there is no `update()`. |
| `Document.form` → `Form` | — | pylopdf | M3. A `Mapping[str, Field]` keyed by fully qualified name. `Field.value` reads and writes the field value, and `Field.widgets` lists its widgets. It replaces `get_form_fields()` and `set_form_field()`. The overlap with `Widget.field_value` mirrors the PDF model, in which a field and its widgets share one value (R1). |
| `Document.bake(*, annots=True, widgets=True)`, `is_form_pdf`, `need_appearances()`, `get_sigflags()` | same | same | M3. |

## 11. Redaction and sanitization

`Page.add_redact_annot`, `Page.apply_redactions(*, images=, graphics=, text=)`
with pymupdf's `PDF_REDACT_*` constants, `Document.scrub(...)`,
`clean_contents`, and `recolor` keep pymupdf's names and meaning (M4).
`apply_redactions` also returns a verification report; the report is an
extension whose truth value is pymupdf's boolean result.

## 12. OCR

| Target | pymupdf | Relation | Notes |
| --- | --- | --- | --- |
| `Page.get_text_ocr(*, engine=None, dpi=300, clip=None, ...)` → `list[OcrWord]` | — | pylopdf | Positioned words. |
| `Page.apply_ocr(*, engine=None, ...)` | — | pylopdf | Inserts an invisible text layer; M6 adds the hybrid mode. |
| `Page.get_textpage_ocr(*, flags=0, dpi=72, full=False)` | same | same | M6. It returns a text page that `get_text(textpage=)` accepts. Tesseract's `language=` and `tessdata=` raise. |
| `OcrEngine(...)`, `OcrEngine.recognize(pixmap, *, ...)` | — | pylopdf | M0 changes `recognize` to take a `Pixmap`, so it recognizes any image instead of duplicating `Page.get_text_ocr`. |

## 13. Low-level objects

The typed object model replaces pymupdf's string-based `xref_*` methods
(roadmap F5). Its first release implements this design:

- `Document.objects` is an `Objects` mapping from object numbers to indirect
  objects, with `add(value)` and `add_stream(data, dictionary=None, *,
  filter="FlateDecode")` returning the new object number, assignment
  replacing an existing object, and `len()` counting the numbers in use.
  `Document.trailer` and `Document.catalog` are dictionary handles,
  `Page.obj` is the page dictionary, and `Document.page_xref(pno)` and
  `Page.xref` return its object number.
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
  `add()` is copied, which replaces `xref_copy`. A handle from another
  document is refused; `insert_pdf` and `show_pdf_page` import across
  documents.
- Every write invalidates derived caches and remembered placements. Any
  write can retype an object that the page tree reaches, so each write
  compares the page order with a fresh walk and bumps the page generation
  when it changed (R7).
- Generation numbers are hidden from the mapping: the number is the key, and
  a number stored under several generations resolves to the highest.
  Handles report `xref` and `generation`.
- A handle raises `StaleObjectError` when its object no longer exists, when
  its path no longer leads to a container, or after `garbage=2` and higher
  or a reopening `authenticate()` renumbered the objects.
- The manifest maps each `xref_*` method to its object-model expression for
  the codemod, for example `doc.xref_get_key(xref, "Rotate")` to
  `doc.objects[xref].get("Rotate")`.

## 14. Constants

pymupdf's constant groups become IntEnum or IntFlag classes whose members keep
pymupdf's module-level names and values.

| Group | Class | Module names |
| --- | --- | --- |
| Link kinds | `LinkKind` | `LINK_NONE`, `LINK_GOTO`, `LINK_URI`, `LINK_LAUNCH`, `LINK_NAMED`, `LINK_GOTOR` |
| Text alignment | `TextAlign` | `TEXT_ALIGN_LEFT`, `TEXT_ALIGN_CENTER`, `TEXT_ALIGN_RIGHT`, `TEXT_ALIGN_JUSTIFY` |
| Text flags | `TextFlags` | `TEXT_PRESERVE_LIGATURES`, `TEXT_DEHYPHENATE`, and the rest (M1) |
| Permissions | `Permissions` | `PDF_PERM_PRINT`, `PDF_PERM_MODIFY`, `PDF_PERM_COPY`, `PDF_PERM_ANNOTATE`, `PDF_PERM_FORM`, `PDF_PERM_ACCESSIBILITY`, `PDF_PERM_ASSEMBLE`, `PDF_PERM_PRINT_HQ`; M0 renames today's members to these suffixes |
| Encryption methods | `EncryptionMethod` | `PDF_ENCRYPT_KEEP`, `PDF_ENCRYPT_NONE`, `PDF_ENCRYPT_RC4_40`, `PDF_ENCRYPT_RC4_128`, `PDF_ENCRYPT_AES_128`, `PDF_ENCRYPT_AES_256`, `PDF_ENCRYPT_UNKNOWN`, with MuPDF's values 0–6, in M0 |
| Colorspaces | `Colorspace` values | `csGRAY`, `csRGB`, `csCMYK`, and the `CS_*` integers, in M0 with `Pixmap.colorspace` |
| Standard 14 fonts | tuple and dict | `Base14_fontnames` and `Base14_fontdict`, in M0 |
| Annotation types and flags, line ends, borders, field types, redaction | `AnnotType`, `AnnotFlags`, and others | `PDF_ANNOT_*`, `PDF_REDACT_*`, and the rest, as their milestones land; `PDF_ANNOT_IS_*` arrives with `Annot.flags` in M0 |

## 15. API reset (M0)

Every change below removes a pymupdf-name conflict (R2), a pylopdf synonym
(R1), or a design replaced under R3, or applies a global convention (R4). Each
change ships with a codemod rule and a migration hint (roadmap F9), and all of
them land in one breaking release. The parity manifest's `reset` entries map
one-to-one onto these rows.

| Current pylopdf API | Target | Rule |
| --- | --- | --- |
| `open(filename, stream, password, max_decompressed_size, *, limits)` and the same `Document(...)` constructor | `Document(filename=None, stream=None, filetype=None, *, password=None, limits=None, max_decompressed_size=None)`, with `open = Document` | R2: the third positional parameter is `filetype`; R2.6: `open` is pymupdf's alias; done |
| `Document.authenticate(password)` | pymupdf's return codes even after an empty user password opened the document | R2; done |
| `Document.set_metadata(m)` | pymupdf's round trip, `trapped`, and empty-dictionary semantics | R2; done |
| `Document.insert_pdf(other, from_page=0, to_page=-1, start_at=-1)` | pymupdf's negative-index and clamping rules, with keyword-only ranges | R2; done |
| `Document.delete_page(pno)`, `delete_pages(page_numbers)` | pymupdf's default and argument forms | R2; done |
| `Document.new_page(pno, width, height)` | keyword-only `width` and `height` | R2.3; done |
| `Document.copy_page(pno, to=-1)` (an independent copy) | `fullcopy_page(pno, to=-1)`; `copy_page` is not provided | R2, G8; done |
| `Document.save(..., object_streams=)`, `tobytes(..., object_streams=)` | `use_objstms=` | R2; done |
| `Document.save(user_pw=...)`, `tobytes(user_pw=...)` encrypting implicitly with AES-256 | `encryption=` with pymupdf's constants and `PDF_ENCRYPT_NONE` default; a password under `PDF_ENCRYPT_NONE` raises | R2, R2.7; done |
| `Document.metadata` keys | adds `trapped` and `encryption` | R2; done |
| `Document.compress_images(*, dpi, quality)` | `rewrite_images(*, dpi_threshold=, dpi_target=, quality=, ...)` with pymupdf's meaning; unsupported parameters raise | R2 |
| `Document.render_page(pno, ...)` | `get_page_pixmap(pno, ...)` with `Pixmap.tobytes()` | R1 |
| `Document.render_page_svg(pno)`, `Page.render_svg()` | `Page.get_svg_image()` | R1, R2 |
| `Page.render(...)` | `get_pixmap(...).tobytes()` | R1 |
| `Page.get_pixmap(scale=, *, dpi, background, clip)` | pymupdf's defaults (opaque RGB) and `matrix=`, without `scale=` | R1, R2 |
| `Document.render_pages(pages, scale=, ...)` | the `get_pixmap` options, without `scale=` | R1 |
| `Page.get_images()` (decoded placements) | pymupdf's `get_images(full=)` inventory, `get_image_info()`, and `extract_image(xref)` | R2 |
| `Page.get_drawings()` keys | adds `seqno` and `layer` | R2 |
| `Page.cropbox`, `set_cropbox(rect)` in bottom-left PDF coordinates | pymupdf's box convention, with the MediaBox containment check | R2, G1 |
| `Page.set_mediabox(rect)` keeping the other boxes | removes the CropBox, ArtBox, BleedBox, and TrimBox, as pymupdf does | R2 |
| `Page.insert_text(...)`, `insert_image(...)`, `show_pdf_page(...)` returning `None` | the inserted line count, the image xref, and the Form xref | R2; `insert_text` done |
| `fontname=` accepting only the short Standard 14 aliases | also the full names, with `Base14_fontnames` and `Base14_fontdict` | R2 |
| `Pixmap.save(path)` | `Pixmap.save(filename, ...)` | R2; done |
| `TextPage` and `RawTextPage` TypedDicts | `TextPageDict` and `RawTextPageDict`; M1 gives `TextPage` to pymupdf's handle | R2 |
| `Rect.width` and `height` going negative; tuple `+`, `*`, `in`, and truth testing on `Point` and `Rect` | clamped widths and pymupdf's geometric operators (roadmap F4) | R2, R6 |
| `Page.find_tables(strategy="lines", *, clip=None)` | `find_tables(*, clip=None, strategy=None, ...)` | R2.3; done |
| `Page.search_for(needle, ...)` | `search_for(text, ...)` | R2; done |
| `Page.insert_textbox(..., expandtabs=8)` | `expandtabs=1` | R2; done |
| `Page.insert_text(...)` and `insert_textbox(...)` placing lines 1.2 times `fontsize` apart, and `insert_text(...)` splitting text only at `\n`, drawing every line, and raising for empty text | pymupdf's lines: `lineheight=` on `insert_text`, a default line height of the font's ascender minus its descender (1.2 when that is at most 1), `insert_textbox` baselines that start one ascender below the top and follow `lineheight` times the ascender apart, and `insert_text` text split with `str.splitlines()`, in which a trailing line break adds no line and empty text inserts nothing, without the lines whose baseline falls below the page | R2, G1; done |
| `Page.show_pdf_page(rect, src, pno=0, ...)` | the parameter name `docsrc` | R2; done |
| `Page.annots()` (dictionaries) | read-only `Annot` handles | R2 |
| `Page.add_highlight_annot(rects, ...)` returning `None` | `add_highlight_annot(quads, ...)` returning an `Annot` | R2 |
| `Page.add_link_annot(rect, uri)` | `Page.insert_link(link)` | R2 |
| `Page.get_links()` keys | adds `xref` and `id` | R2 |
| `Document.get_toc()`, `set_toc(toc)` | `get_outline()`, `set_outline(entries)`, zero-based | R3: one-based numbering |
| `Document.embfile_add`, `embfile_get`, `embfile_del`, `embfile_names` | `Document.attachments` | R3: the mapping protocol |
| `OcrEngine.recognize(page, ...)` | `OcrEngine.recognize(pixmap, ...)` | R1 |
| `Permissions` members | pymupdf's `PDF_PERM_*` suffixes, with module constants | R2; done |
| `PdfError` for missing and malformed files | the built-in `FileNotFoundError`, `FileDataError`, and `EmptyFileError` | G3; done |
| `StalePageError(PdfError)` | `StalePageError(StaleObjectError)` | G3; done with F5 |
| `LINK_*` and `TEXT_ALIGN_*` as plain integers | IntEnum members with the same names and values | R6; done |
| `get_text("words")` and `get_text("blocks")` tuples | `Word` and `Block` NamedTuples | R6 |

Unchanged in M0: `get_form_fields()` and `set_form_field()` stay until M3
provides `Document.form` and widget handles. `get_text_ocr()`, `apply_ocr()`,
`insert_ocr_text_layer()`, `replace_text()`, `to_markdown()`,
`Document.get_text(pages)`, `peek_metadata()`, `set_fallback_font()`,
`get_pdfa_claim()`, and the limit APIs have no pymupdf counterpart and keep
their names.

## 16. Open questions

- Whether `Pixmap` should accept pymupdf's positional constructor forms at
  all, or offer only classmethods, given how ambiguous the forms are (M1).
- How far `insert_htmlbox` and `TextWriter` can follow pymupdf in M8 before an
  HTML subset needs a different name.
