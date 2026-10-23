# pylopdf roadmap

This is the canonical medium-term plan. It was rewritten on 2026-10-22 around
one direction: **the best PDF library for Python, built as the Python face of
the Rust PDF engines**, with lopdf for objects and persistence, hayro for
interpretation and rendering, and krilla for writing.
[api/DESIGN.md](api/DESIGN.md) holds the API design that every milestone
implements, and [AGENTS.md](AGENTS.md) the day-to-day development context.

## Goal

pylopdf is complete, essential, engine-faithful, trustworthy, and available
everywhere Python runs.

- **Complete.** pylopdf does everything a program needs to do with PDF files,
  and at least everything pymupdf can do with them. A PDF capability that
  pymupdf has and pylopdf lacks is a gap on this roadmap, never a scope
  decision. The [capability map](#capability-map) measures it.
- **Essential.** A small set of concepts follows the PDF model and the
  engines: a document of pages and objects, one content model that reading
  and writing share, rendering, and the interactive and navigational
  structures. Each task has one obvious way to do it, with typed values and
  standard Python protocols. Names and shapes are pylopdf's own; pymupdf is a
  source of capabilities, not of design.
- **Engine-faithful.** A capability belongs to the engine whose domain it is
  and is built there. The maintainers of lopdf, hayro, and krilla accept
  well-made changes quickly, so pylopdf contributes to them instead of working
  around them. pylopdf itself builds what only a Python library that combines
  the three can: the binding, the one-way data flow between the engines,
  resource policy, and document understanding (layout, tables, Markdown, and
  OCR).
- **Trustworthy.** Hostile input meets typed, bounded refusal. Results can be
  verified: redaction proves removal, extraction carries its provenance, and
  benchmarks publish losses with wins.
- **Everywhere.** GIL-enabled and free-threaded CPython, Pyodide and
  Cloudflare Python Workers, small wheels, and the MIT license for every
  feature, layout analysis and OCR included.

## Baseline: 2026-10-22

Main is `61ed29c`: release 0.15.0 plus unreleased changes that renamed parts
of the API after pymupdf for a plan that this rewrite replaces.

**What works.** Lenient opening with a bounded resource policy, the typed
object model, page organization, metadata, XMP, page labels, the outline,
attachments, text in reading order (words, blocks, spans, and characters),
search, tables, Markdown, vector-path and image inventories, raster and SVG
rendering with parallel batches, shaped and subset-embedded text, textbox
layout, vector shapes, AcroForm filling, Highlight and URI link creation,
AES-256 encryption, image recompression, native offline OCR, a concurrency
contract, and WebAssembly.

**Capability coverage.** The capability map lists 962 public names of
pymupdf 2.0: 365 available today (306 of them under pymupdf's own name), 32
covered by ecosystem tools, 408 planned, and 157 outside scope, so 49.3% of
the names in scope are covered. The largest gaps are annotations and forms
(`Annot` 47 planned names, `Widget` 36, `Link` 16), extraction depth and text
pages, rendering options and Pixmap conversions (`Pixmap` 39), rich text
(`TextWriter` 14), redaction, optional content, and incremental save.

**Engine workarounds.** About 5,500 lines of Rust exist because an engine
lacks a bound, an API, or linear-time behavior: nesting guards and the
scanners behind them for hayro (`content_guard.rs`, `content_scan.rs`,
`graph_guard.rs`, `postscript_scan.rs`, and `read_depth.rs`), the choice of
snapshot for large object streams (`object_stream_work.rs`), reconciling how
lopdf and hayro read malformed files (`object_recovery.rs`), capturing lopdf's
recovery reports from its log (`recovery.rs`), linear replacements for lopdf's
quadratic graph helpers (`prune.rs` and `renumber.rs`), object-number headroom
(`unallocated.rs`), and a calling-thread parser for krilla's output
(`generated_pdf.rs`). [Engine work](#engine-work) moves each one upstream.

**API surface.** pylopdf exports 160 names, 80 of them module-level constants
in MuPDF's style. `Document` has 64 public members and `Page` 45, among them
pymupdf's conveniences and pairs of methods that the design replaces with
properties and collections.

## Principles

1. **Capabilities, not names.** Completeness is measured per capability in
   the capability map. pylopdf's names follow [api/DESIGN.md](api/DESIGN.md).
   A pymupdf name stays only where it is also the clearest name in pylopdf's
   vocabulary, and then carries no obligation to pymupdf's semantics or to
   MuPDF's numerical behavior.
2. **Engine-first.** Before building a capability, decide which layer owns it
   ([Architecture](#architecture)). Work that an engine owns lands upstream
   first, and pylopdf adopts the released engine. A workaround in pylopdf is
   allowed only while an upstream change is under way: it links the upstream
   pull request, is listed under [Engine work](#engine-work), and goes away in
   the pull request that adopts the fix. pylopdf releases depend only on
   published engine versions.
3. **One graphics model.** hayro's `Device` and krilla's `Surface` share one
   vocabulary: paths with fills and strokes, glyph runs, images, clips,
   transparency groups, and marked content. pylopdf exposes one set of types
   for both directions, so a program can read a page's content, filter or
   transform it, and write it back.
4. **One coordinate space.** Every position on a page, read or written, is in
   display space: points from the top-left corner of the visible page, with
   `/Rotate` and the CropBox applied. Page boxes, which define that space, are
   the only values in PDF user space.
5. **One-way data flow.** lopdf's object graph is the single editable source
   of truth. hayro interprets an immutable snapshot of it, and krilla produces
   content that is imported into it. The engines never share mutable state,
   and every edit invalidates the snapshot.
6. **Bounded by default.** Every caller input and every decoded amplification
   has a documented cap, checked before Python copies and again in Rust.
   Trusted workloads opt out explicitly with `None`. Refusal raises
   `LimitError` with a stable code and returns no partial result.
7. **Prepare, then commit.** A mutation plans all fallible work before its
   first change and leaves the document unchanged on refusal. Handles are
   views that read the document on every access, apply setters immediately,
   and raise when stale, so there is no hidden dirty state.
8. **Provenance.** Every derived result (a word, a table cell, a Markdown
   chunk, an OCR word) carries its page and its display-space geometry, and,
   where the PDF has one, the object or structure element it came from.
9. **Evidence over claims.** Features land with real-document and adversarial
   regressions, save-and-reopen and rendering checks, and published
   benchmarks for performance-relevant paths, wins and losses together.
10. **Small core, optional data.** The core abi3 wheel stays at or below
    9.0 MiB on every platform. Fonts, OCR models, and layout models ship as
    separately versioned data packages. A pull request that grows the wheel by
    more than 0.25 MiB justifies it in writing.
11. **Break before 1.0, once per design change.** Redesigns land without
    deprecation windows, and each removed or renamed API gets a codemod rule
    and a migration hint. From 1.0 on, the
    [stability policy](docs/en/stability.md) applies.

## Architecture

| Layer | Engine | pylopdf surface | What pylopdf builds |
| --- | --- | --- | --- |
| Objects and persistence | lopdf | `open`, `save`, `Document.objects`, encryption, incremental save, recovery | resource policy, atomic file replacement |
| Document structure | lopdf | `doc.pages`, outline, page labels, attachments, metadata, optional content | page-tree, name-tree, and outline semantics with bounds |
| Content model | hayro reads, krilla writes | `Page.content()` items, the `Page.draw()` canvas, new documents | the shared types and the conversion between engines |
| Understanding | — | text, words, layout, search, tables, Markdown, tagged structure, OCR | reading order, tables, Markdown, OCR on RTen |
| Rendering | hayro, vello_cpu, hayro-svg | `Page.pixmap()`, `Page.svg()`, `Document.render()`, `Pixmap` | encoders, parallel batches, cache reuse |
| Interaction | lopdf; appearances through krilla | annotations, links, form fields, widgets, flattening | appearance planning, AcroForm semantics |
| Rewriting | pylopdf's content engine over lopdf; hayro verifies | redaction, sanitization, content cleaning, recoloring, image rewriting | content rewriting and its verification |

## Capability map

`api/pymupdf-parity.json` records, for every public name of pymupdf 2.0, how
pylopdf covers the capability behind it:

- `available`: pylopdf provides it, under the same name (`same`) or through a
  different API (`replaced`) that the entry names, with a one-sentence hint
  for migrating code.
- `planned`: a milestone below adds it. The entry names the milestone; the
  pylopdf API is designed when the milestone starts.
- `integration`: an ecosystem tool provides it through a recipe that a test
  covers.
- `non-goal`: outside the [scope](#non-goals), or an internal of pymupdf or
  MuPDF without a capability at the PDF level.

`tools/check_parity.py` checks the map in CI and reports coverage. The same
data drives the migration tools: hinted `AttributeError`s, the codemod
(`python -m pylopdf.migrate`), and the name map in `docs/en/llms.txt`. Each
pymupdf release adds its new names to the map. Record names and statuses only;
never copy pymupdf code or documentation, which is AGPL-licensed.

## Definition of done

Every feature pull request satisfies all of the following before merge.

- **Layer.** The pull request names the layer that owns the capability. Work
  that an engine owns is merged and released upstream, or the pull request
  links the upstream pull request and lists its workaround under
  [Engine work](#engine-work).
- **API.** It follows [api/DESIGN.md](api/DESIGN.md), with complete type hints
  and docstrings, an updated `pylopdf_core.pyi`, and `api/public-api.json`
  refreshed after reviewing runtime, typing, documentation, and SemVer impact.
  The capability-map entries it covers are updated, with a hint for every
  pymupdf name that pylopdf covers under another name.
- **Safety.** Caller input is capped before PyO3 copies it and again in Rust;
  new `LimitError` codes are documented in the security guide; input-derived
  collections grow fallibly; no partial results; prepare-then-commit; and the
  cache and generation rules in AGENTS.md hold.
- **Tests.** Python tests only, with no Rust unit tests: success, boundary,
  refusal, and malformed-input cases; a save-and-reopen round trip; rendered
  or extracted verification; and a redistributable real-world fixture when the
  behavior depends on producers. pymupdf may serve as a development-only
  oracle where a comparison is meaningful; it is never a runtime or required
  test dependency.
- **Portability.** The free-threaded lanes pass, and the native/PyEmscripten
  contract in `tools/pyodide_compat.py` covers the feature unless it depends
  on OCR or threads.
- **Documentation.** The English API reference and guide change in the same
  pull request. Japanese, Simplified Chinese, and Korean follow before the
  release tag, as LANGUAGES.md requires, and the migration guide gains a row
  for each pymupdf name.
- **Measurement.** The wheel size delta, a benchmark entry for
  performance-relevant paths, and fuzz seeds for every new parser.
- **Changelog.** One changelog fragment, as `changelog.d/README.md`
  describes.

## Workstreams

| Workstream | Scope | Engine area |
| --- | --- | --- |
| Objects | loading, recovery, the object model, saving, incremental save, encryption, garbage collection | lopdf |
| Structure | the page tree, outline, destinations, page labels, attachments, metadata and XMP, optional content, structure-tree maintenance | lopdf |
| Content | the content model, interpretation guards, image and drawing inventories | hayro-interpret, hayro-syntax |
| Understanding | text and layout, search, tables, Markdown, tagged structure, OCR, layout models | RTen |
| Rendering | rasterization, SVG, `Pixmap`, render caches, parallel batches | hayro, vello_cpu, hayro-svg |
| Writing | the canvas, fonts and text layout, images, page placement, new documents, conversion | krilla, HarfRust, parley |
| Interaction | annotations, links, form fields and widgets, flattening | lopdf, krilla |
| Rewriting | the content engine, redaction, sanitization, text replacement, image rewriting | lopdf, hayro for verification |

Each workstream also owns the upstream work in its engine area, and AGENTS.md
records its modules. Concurrent pull requests stay inside one workstream; a
change to another workstream's module is its own small pull request. Shared
files (`src/pylopdf/__init__.py`, `rust/src/lib.rs`,
`rust/src/document/mod.rs`, `api/public-api.json`, and the capability map)
take append-only registrations and are regenerated after each rebase. Each
pull request names its milestone.

## Phases

Phases define order, not dates. A release ships when its milestones meet
their acceptance criteria.

| Phase | Work that runs in parallel | Gate |
| --- | --- | --- |
| 1 — foundations (current) | [Content model](#content-model), [Canvas](#canvas), [API reset](#api-reset), and the first [engine work](#engine-work) | The three milestones are accepted; the next release ships them together |
| 2 — capabilities | [Reading](#reading), [Rendering](#rendering), [Writing](#writing), [Interaction](#interaction), [Rewriting](#rewriting), [Structure](#structure), [Persistence](#persistence), and [Understanding](#understanding) | Each milestone's acceptance |
| 3 — 1.0 | [v1.0 criteria](#v10) | Every criterion |

The pymupdf-aligned renames on main since 0.15.0 are not released on their
own. The next release carries the API reset, so users of pylopdf 0.15 and of
pymupdf migrate once, with one codemod.

## Milestones

### Content model

Workstream Content, with Writing for the shared types.

- `Page.content(*, clip=None, annotations=True, max_elements=262_144)`
  returns the painted items of a page in paint order and display space, as
  immutable values (available on main):
  - `TextRun`: glyphs with glyph ID, Unicode, matrix, advance, and the exact
    box of their outline or Type 3 procedure; a font reference; render mode;
    and fill and stroke paint.
  - `Path`: segments, a fill paint with its rule, and a stroke with its line
    style. A shading painted with `sh` is a path filled with a
    `ShadingPattern`.
  - `Image`: the image object or inline image, size, bits per component,
    color space, stencil flag, mask kind, and matrix.

  Every item carries its blend mode, soft mask, and graphics context: the
  clips, transparency groups, and marked content around it, shared by the
  items painted in the same context.
- Text, words, layout, search, drawings, and image placements derive from the
  same interpretation, so they agree by construction. Text and table
  extraction read the content model's device through its sink (done, which
  also reads fill-and-stroke text once instead of twice), and
  `content().paths` and `content().images` replaced `get_drawings` and
  `get_image_info` and their devices in the [API reset](#api-reset) (done).
  One cache shared by the views follows when a measurement shows that
  interpreting a page once for all of them pays for its memory.
- pylopdf exposes what hayro computes instead of recomputing it: glyph
  outlines for exact glyph boxes (done), font identity (object, name, type,
  embedding), and image object numbers, masks included.
- Upstream in hayro: marked-content properties and `/ActualText` in a release
  (`main` exposes `/ActualText` since LaurenzV/hayro#1513); font object
  identity, Type 3 font identity, and writing mode on glyph runs; the color
  space and components of a color paint; the clipping text render modes;
  begin and end callbacks for Form XObjects and annotation appearances, so
  that items name their source; a callback for `sh`; the stream, object
  number, and paint of image masks; Unicode for Type 3 glyphs without a
  ToUnicode CMap (LaurenzV/hayro#1331); and an error instead of silent
  truncation beyond its nesting bounds.
- It covers pymupdf's `DisplayList`, `Page.get_displaylist`, `Page.run`,
  `get_texttrace`, `get_bboxlog`, `get_cdrawings`, `get_image_rects`,
  `get_image_bbox`, and `get_drawings(extended=True)`.

Acceptance: on the bundled corpus, text, words, drawings, and image
placements derived from the content model equal today's extraction, except
for documented improvements; glyph boxes come from outlines; and the round
trip under [Canvas](#canvas) passes.

### Canvas

Workstream Writing.

- `Page.draw(*, underlay=False)` returns a `Canvas`, a context manager that
  commits once on a clean exit and writes nothing after an exception (done).
  It draws with the content model's vocabulary: `path` with `PathBuilder`
  geometry, `rect`, `line`, `circle`, `ellipse`, `polygon`, and `bezier`;
  `text` and `textbox` with shaping, wrapping, alignment, and subset-embedded
  fonts; `image`; `page`, which places another page; the scoped states
  `clip`, `transform`, `opacity`, and `blend` (all done), then `mask`,
  `layer`, and `tag`; and `draw(item)`, which paints a content item again
  (done for paths). `textbox` places lines as `text` does, and
  `Font.text_height` measures the height it needs before drawing (done).
  Still to come: `rotate` for text, and `clip` and `rotate` for placed
  pages.
- One commit places one Form XObject whose content pylopdf composes in
  display space (done). Paths, clips, transforms, and blend modes are
  operators; an opacity scope is a transparency group Form; text in an
  OpenType face is a krilla page, one per run of text items, imported as a
  Form; and Standard 14 text, images, and placed pages are drawn as
  `insert_text`, `insert_image`, and `show_pdf_page` draw them. Scopes must
  enclose images and pages that already live in the target document, which a
  krilla page cannot reference, so the scopes are pylopdf's operators.
- `draw(item)` for text runs and images needs the font and image identity
  listed under [Content model](#content-model): a run must name the font
  program to embed again, and an image the document it comes from.
- New documents (done): `pylopdf.create()` returns a builder whose pages
  draw through the same canvas and which krilla writes as a whole, with
  metadata, an outline from `bookmark`, a structure tree from `tag` scopes,
  and the validators of PDF/A-1 to PDF/A-4 and PDF/UA-1. A recording is a
  list of structured operations, which `Page.draw()` composes into lopdf
  content and `create()` replays onto a krilla surface. Still to come: links
  and other annotations, placed pages, and the remaining tag attributes
  (table header scopes, list numbering).
- Upstream in krilla: the Standard 14 fonts without embedding, which krilla
  cannot draw today, so pylopdf drops its own operator writer for them;
  optional content; annotation and widget types beyond links for new
  documents; embedded pages in validated output; subset fonts named after the
  variable-font instance they embed, so pylopdf no longer renames them after
  import; and emitting a page as a Form XObject that pylopdf adds to lopdf's
  graph directly, so `generated_pdf.rs` and the import parse go away.
- In the [API reset](#api-reset), the canvas replaces `Shape`,
  `Page.draw_*`, `insert_text`, `insert_textbox`, `insert_image`, and
  `show_pdf_page`.

Acceptance: drawing every item of `page.content()` onto a blank page of the
same size renders, for every page of the bundled corpus, within a published
pixel tolerance of the original page, and a documented list names what does
not round-trip yet. Every existing drawing test passes through the canvas.

### API reset

All workstreams. It ships in the next release, with the content model and
the canvas.

- Apply [api/DESIGN.md](api/DESIGN.md) to the existing surface; its API reset
  table is the work list. Constants become enum members only, collections
  replace families of methods (`doc.pages`, `page.annotations`, `page.links`,
  `doc.outline`, and `doc.form`), computed results take noun names
  (`page.text()`, `page.pixmap()`), and pymupdf's duplicates and conveniences
  go.
- The codemod and the hints migrate pymupdf code and pylopdf 0.15 code alike;
  `api/pylopdf-0.15.json` lists the 0.15 names each step removes.
- Done: documents and pages. `pylopdf.open(source)`, `Document()`,
  `pylopdf.probe()`, `doc.pages` with `new`, `insert`, `extend`, `select`,
  `move`, `labeled`, and `del`, `page.index` and `page.document`, `doc.path`,
  and `MalformedPdfError`.
- Done: saving and encryption. `save(path, /, *, compress, prune, dedupe,
  renumber, object_streams, encryption)` and `to_bytes(...)` with
  `Encryption(...)`, `unlock(password)` returning `Access`,
  `doc.needs_password` until unlocking, and `doc.permissions` as
  `Permissions` flags.
- Done: reading text. `page.text()`, `words()`, `layout()`, `search()`, and
  `markdown()`, `doc.text()` and `doc.markdown()`, and the `Word`, `Layout`,
  `Block`, `Line`, `Span`, and `Char` records, with `Layout.to_dict()` for
  pymupdf's dictionary shape.
- Done: tables and OCR. `page.tables()` returning `Table` records with
  `rows`, `cells`, and `TableDiagnostics`, and `page.ocr()` and
  `page.apply_ocr(words=...)` with `OcrWord` records.
- Done: drawings and image placements. `page.content()` selects its item
  kinds, and `content(text_runs=False, images=False).paths` and
  `content(text_runs=False, paths=False).images` replace `get_drawings()` and
  `get_image_info()`.
- Done: the image inventory. `doc.images` maps object numbers to
  `ImageObject` handles with the image's dictionary facts, `extract()`
  returning an `EncodedImage`, and `to_pixmap()`, and `page.resources.images`
  replaces `get_images()`; the fonts, Form XObjects, color spaces, and
  graphics states of `page.resources` follow with the content model's font
  identity.
- Done: rendering entry points. `page.pixmap()`, `page.svg()`, and
  `doc.render()` replace `get_pixmap()`, `get_svg_image()`, and
  `render_pages()`, with `dpi` and `matrix` exclusive and an `annotations`
  option. `pixmap()` is named after its result instead of DESIGN.md's first
  `render()`, which 0.15 used for a method that returned PNG bytes.
- Done: the `Pixmap` value. Gray and RGB pixels with optional alpha,
  `channels`, the `Colorspace` enum, a buffer shaped `(height, width,
  channels)` for `numpy.asarray`, `Pixmap.from_array`, and PNG and JPEG
  through `to_bytes`, `save`, and `doc.render(format=...)`. CMYK waits for a
  renderer that produces it.
- Done: document properties. `doc.metadata` is a live `Metadata` mapping of
  the Info entries present, `doc.xmp`, `doc.language`, `doc.page_mode`,
  `doc.page_layout`, and `doc.mark_info` are assigned and deleted as
  attributes, with the `PageMode` and `PageLayout` enums and the `MarkInfo`
  record, and `doc.version`, `doc.encryption_method`, and `doc.pdfa_claim`
  (a `PdfaClaim` record) replace the `format` and `encryption` entries and
  `get_pdfa_claim()`.
- Done: the outline and page labels. `doc.outline` is a tree of
  `OutlineItem` handles whose title, destination, color, bold and italic
  style, and open state are edited in place, and assigning a list of
  `OutlineEntry` records replaces it; destinations are `Destination`,
  `RemoteDestination`, `Launch`, `NamedAction`, or a URI string, the targets
  that links will share. `doc.page_labels` holds `PageLabelRange` records with
  the `PageLabelStyle` enum, and `page.label` reads one page's label.
- Done: page geometry. `page.rotation` is assigned, `page.boxes` holds the
  MediaBox, CropBox, BleedBox, TrimBox, and ArtBox in PDF user space, each
  assignable and deletable, and `page.user_to_display` maps user space to
  display space in place of pymupdf's transformation, rotation, and
  derotation matrices.
- Done: fallback fonts. `doc.fallback_fonts` maps each CJK language to a
  `FallbackFont(sans, serif)` record of `Font` values, shared rather than
  copied, lists the installed font extras until it is edited, and keeps its
  configuration across `unlock()`.
- Done: links. `page.links` is a sequence of `Link` handles whose `rect`
  and `target` are assigned in place, with `add(rect, target)` and `del`;
  links take the outline's targets, so they lead to pages, other files,
  files to open, and viewer actions as well as URIs, and `LinkInfo`,
  `LinkKind`, and the `LINK_*` constants are gone.
- Done: annotations. `page.annotations` is a sequence of `Annotation`
  handles with typed properties for every annotation but links, widgets,
  and popups, `add(Highlight(...))` adds a highlight, and `del` removes an
  annotation with its popup; `AnnotationType` and `AnnotationFlags` replace
  `AnnotType` and `AnnotFlags`. The other specifications and editable
  properties come with the Interaction milestone.
- Done: fonts. `Font.standard()`, `Font.from_file()`, `Font.from_bytes()`,
  and `Font.installed()` replace the `Font(...)` constructor,
  `text_width()` replaces `text_length()`, `text_height()` measures wrapped
  text as the canvas's `textbox` places it, `has_glyph()` returns a bool,
  and `StandardFontName` replaces `Base14_fontnames` and `Base14_fontdict`.
- Still to come, each with its codemod rules: forms and widgets, the
  canvas in place of the page's drawing methods and `Shape`, and the
  remaining constants and records.

Acceptance: every row of the reset table is done; the capability map's
available entries name the new APIs; the codemod turns the example programs
in `tests/assets/migration/` into programs that pass; and the documentation
in four languages describes the new surface.

### Engine work

Every workstream contributes in its engine area. The first goal is the
upstream work that the content model and the canvas need; the rest removes
pylopdf's workarounds. Before each item, agree on the design with the
engine's maintainer.

| Engine | Change | What it unlocks or removes in pylopdf |
| --- | --- | --- |
| hayro | The items listed under [Content model](#content-model): marked-content properties, font and Type 3 font identity, color spaces and components, clipping render modes, source callbacks, a `sh` callback, and image-mask streams | the gaps that `Page.content()` documents today |
| hayro | Bounded construction of functions, color spaces, and Type 4 programs, refusing deeper input with an error | `graph_guard.rs`, `postscript_scan.rs`, and their walks |
| hayro | An error, instead of silent truncation, for content and objects nested beyond its bound | `content_scan.rs`, the nesting walk in `content_guard.rs`, and `read_depth.rs` |
| hayro | Parse an object stream's member table once (LaurenzV/hayro#1545) | `object_stream_work.rs` and the serialized-snapshot choice |
| hayro | A cache of parsed objects, so that repeated lookups do not parse an object again | the read-once bookkeeping of the guards |
| hayro | A linear search for the end of inline image data, and an operand stack that holds the 33 operands of a DeviceN `scn` | quadratic content parsing, and content that stops early |
| hayro | No panic on mismatched color components (LaurenzV/hayro#1546) and on the remaining malformed-input panics | reliance on `contain_renderer_panics` |
| hayro | Interpretation over an object source that is not file bytes, such as lopdf's graph | `object_recovery.rs`, the serialized snapshots, and the cost of reserializing after edits |
| hayro | Thread-safe render caches, a thread count (LaurenzV/hayro#1317), and faster stencil masks (LaurenzV/hayro#1315) | the per-thread cache slots in `render_cache.rs`; shared caches across `Document.render` workers |
| hayro, vello_cpu | Gray and CMYK output and anti-aliasing levels | gray converted from the RGB raster in `raster_samples`; no CMYK |
| hayro-svg | Text kept as text, and a transform | `svg()` takes neither `text_as_path` nor `matrix` |
| lopdf | A public object parser and serializer | pylopdf's own value conversion |
| lopdf | Recovery reports through the API instead of the `log` crate; a requested rebuild of the cross-reference table; rebuilding when the catalog or page tree is unreadable, and for damaged cross-reference streams and truncated files | the log capture in `recovery.rs`, the forced rebuild, and inputs rejected today |
| lopdf | A linear scan for unterminated streams during recovery | the `recovery_scan_size` pre-scan |
| lopdf | Linear graph traversal in pruning, renumbering, and deletion | `prune.rs` and `renumber.rs` |
| lopdf | Checked object-number allocation | most of `unallocated.rs` |
| lopdf | Parsing small documents on the calling thread instead of the rayon pool | `generated_pdf.rs`, if krilla's Form output does not remove it first |
| lopdf | A bounded, iterative page-tree walk with inherited attributes | pylopdf's own walker |
| lopdf | Release of the rebuilt-trailer cleanup merged as J-F-Liu/lopdf#609 | the `/Prev` and `/XRefStm` removal after reconstruction |
| lopdf | Linearization facts and the number of revisions | pymupdf's `is_fast_webaccess` and `version_count` |
| krilla | The items listed under [Canvas](#canvas) | the Standard 14 writer, the font renaming, and the import parse |

Acceptance, per item: the engine change is released, pylopdf adopts it, the
workaround and its tests' special cases are removed, and the corpus results
and benchmarks stay unchanged or improve.

### Reading

Workstreams Understanding and Content. Depends on the content model.

- Span attributes: color, opacity, ascender, descender, and flags for fill,
  stroke, clip, invisible, and synthetic text.
- Options: ligatures kept or expanded, whitespace kept, dehyphenation,
  placeholders for glyphs without Unicode, clipping to the MediaBox, and exact
  glyph boxes; search with quads, a clip, and the same options; the text in a
  rectangle; the selection between two points.
- Serializations of the layout as JSON, HTML, and XML.
- Inventories by page and document: fonts, Form XObjects, and images;
  extraction of font programs; glyph widths.

Acceptance: span colors equal pymupdf's on the corpus; ascender-based boxes
meet a published IoU threshold; quads cover rotated text.

### Rendering

Workstream Rendering.

- Arbitrary affine transforms through hayro's `render_into`; CMYK output
  (gray, RGB, and alpha are done); anti-aliasing levels; resolution metadata
  in PNG and JPEG (annotations on or off, PNG, and JPEG are done).
- `Pixmap` stays an immutable value with zero-copy NumPy interchange in both
  directions (done), plus decoding of PNG and JPEG files (image objects with
  their masks are done through `ImageObject.to_pixmap`).
  Image processing (tinting, gamma, inversion, color statistics, warping,
  scaling) is a tested NumPy, Pillow, or OpenCV recipe rather than `Pixmap`
  methods, recorded in the capability map as `integration`.
- Closing MuPDF's 2–3× lead in warm rasterization through upstream glyph and
  image caches and thread-safe caches.

Acceptance: a diagonal transform renders pixel-identically to the equivalent
resolution; every recipe has a test; benchmarks are published.

### Writing

Workstream Writing. Depends on the canvas.

- Text: rotation, render modes (fill, stroke, invisible, clip), opacity, and
  outlines; per-glyph fallback across the installed font extras; styled runs
  (font, size, color, weight, style, and link) with bidirectional ordering and
  justification, with parley evaluated for the layout; and an HTML and CSS
  subset for text boxes.
- Fonts: metrics, glyph coverage, advances, bounding boxes, and style
  information for the Standard 14 fonts and OpenType faces.
- Images: JPEG 2000 passthrough, masks and alpha, and replacing or removing an
  image object.
- Placement of pages with a clip and rotation, and optional-content layers on
  everything the canvas draws.
- Conversion of images (JPEG, PNG, and JPEG 2000 directly, other formats
  through Pillow) and CBZ archives to PDF, and of scans to searchable PDF with
  OCR.

Acceptance: text effects are verified by rendering and extraction; a diagonal
translucent watermark and mixed CJK and right-to-left runs are covered by
tests; validated PDF/A-2b and PDF/UA-1 output passes veraPDF in the interop
group.

### Interaction

Workstream Interaction. Depends on the canvas and the API reset.

- Annotations as handles with typed properties, created from typed
  specifications for every annotation type that pymupdf creates (text notes,
  free text with callouts, lines, squares, circles, polygons, polylines, ink,
  stamps, file attachments, carets, text markup, and redactions), edited and
  deleted, with appearances drawn through the canvas. Rendering synthesizes
  missing appearances without changing the document.
- Links of every kind (to a page, to another file, to a URI, to launch a
  file, and named actions), created, edited, and deleted, and destination
  resolution.
- Form fields in `doc.form`: creation, properties, deletion, and reset;
  widget geometry and styling, choice values, and button states; JavaScript
  actions authored and removed, never run; and signature fields with their
  signed state, interoperating with pyHanko.
- Flattening annotations and widgets into page content.

Acceptance: every created annotation renders within tolerance in pylopdf and
pymupdf and survives save and reopen; flattening preserves rendering and
extracted text; fields that pylopdf creates can be filled by pymupdf and by
pylopdf.

### Rewriting

Workstream Rewriting. Depends on the content model and on redaction
annotations from Interaction.

- Redaction removes text (with compensating `TJ` adjustments, in simple, CID,
  and Type 3 fonts, in both writing modes, and in invisible OCR layers),
  images (blanked pixels or removed placements, inline images and masks
  included), and vector graphics that a redaction covers or touches. Shared
  Form XObjects are copied before editing, overlapping annotations go, and the
  redaction annotations become their fills. Re-interpretation with hayro must
  find nothing inside a redacted area, and the call returns a report.
- Sanitization removes JavaScript, actions, embedded files, metadata, hidden
  text, comments, and unused objects, and writes the file without
  incremental history, with a report.
- Content cleaning, recoloring to gray or another color space, and the rest of
  image rewriting: lossless, grayscale, and bitonal targets.

Acceptance: an adversarial redaction corpus (kerned `TJ` arrays, shared
Forms, rotated pages, CJK CID fonts, Type 3 fonts, overlapping layers, inline
images, and masks) shows no leak, cross-checked by pymupdf's extraction of the
redacted areas.

### Structure

Workstream Structure.

- Page operations on `doc.pages`: moving, copying, and inserting pages from
  any document together with their links, annotations, and form fields.
- The ArtBox, BleedBox, and TrimBox (done).
- Outline items as handles with destinations, color, style, and collapse
  state (done).
- Optional content: groups, configurations, membership dictionaries,
  visibility in rendering and extraction, and layers on written content.
- Viewer preferences, linearization facts, and revision counts.
- Attachment dates and in-place updates, shared with file attachment
  annotations.

Acceptance: changing layer visibility changes rendering and extraction; the
outline round-trips; imported pages keep working forms and links in pymupdf
and pylopdf.

### Persistence

Workstream Objects.

- Incremental save through lopdf's `IncrementalDocument`, with central
  tracking of changed objects. Existing signatures stay valid, which a
  pyHanko validation test proves; repaired inputs and encryption changes
  refuse incremental save.
- Subsetting the fonts of existing documents: glyph use from interpretation,
  `subsetter` for the programs, and remapped content strings.
- Recovery of damaged cross-reference streams and truncated files, built in
  lopdf.
- Encryption: AES-128 output, keeping a supported input handler, and verified
  reading of every standard handler. Writing RC4 raises.
- Transactions and undo: `with doc.transaction():` makes a group of edits
  atomic and records one undo step, under a memory budget.
- Save options for compression effort, ASCII output, pretty printing, and
  keeping the document ID.

Acceptance: pyHanko signatures stay valid after incremental edits; pikepdf in
the interop group accepts every output; subsetting reduces size without
changing extracted text or rendering.

### Understanding

Workstream Understanding. Depends on the content model.

- Tagged PDF: the structure tree, role maps, and marked-content IDs on
  content items. Markdown follows headings, lists, tables, figure alternate
  text, and structure order when the tree is trustworthy, and honors `/Lang`
  and `/ActualText`.
- Markdown for retrieval: chunks with page and display-space provenance,
  image export, margins, removal of repeated headers and footers, and limits
  on graphics.
- Hybrid OCR that recognizes only regions without legible text.
- Tables: explicit lines, tolerances, minimum words, header detection, rows
  and cells, and pandas export.
- A layout model: an Apache-2.0 PP-DocLayout model on RTen in a separately
  versioned `[layout]` extra.

Acceptance: human-verified ground truth on representative business
documents, and published comparisons with pymupdf4llm with and without
pymupdf-layout.

## v1.0

- The capability map has no planned entries.
- Every engine workaround is removed, or recorded in AGENTS.md as pylopdf's
  own responsibility with the reason.
- Each canonical workflow has a guide and an end-to-end test: extraction for
  retrieval with provenance; page images and thumbnails; organizing pages;
  stamps and watermarks; filling and flattening forms; redaction and
  sanitization; searchable scans; accessible PDF/A and PDF/UA documents;
  inspecting and repairing objects; and processing untrusted uploads in a
  server or a Cloudflare Worker.
- Independent field reports cover the new surfaces, the documentation is
  complete in four languages, every artifact is verified on its target, and
  the stability policy is frozen. Adoption signals, with roughly 1,000 GitHub
  stars as an indication, inform the decision without replacing these gates.

## Continuous tracks

- **Real-document quality.** Human-verified ground truth for text, reading
  order, table cells, and OCR on Japanese business forms, multicolumn prose,
  mixed scripts, and scans. Similarity to pymupdf is a signal, not the
  definition of correctness.
- **Performance.** Upstream improvements first; publish wins and losses.
- **Security.** Fuzz lanes for every new parser and rewriter, fuzz targets
  contributed to the engines, and `DocumentLimits.web()` extended to every new
  surface.
- **Distribution.** New CPython releases on the day they ship, PyEmscripten
  parity, and the wheel budget.

## Non-goals

- **Input formats other than PDF and images**: XPS and OpenXPS, EPUB, MOBI,
  FB2, HTML, and text documents, with pymupdf's reflow APIs (`layout`,
  chapters, and locations). Convert them to PDF with their own tools.
- **Running JavaScript or XFA.** pylopdf authors and removes scripts but
  never runs them. XFA is deprecated in PDF 2.0.
- **RC4 output.** It is insecure; reading RC4-encrypted files stays
  supported.
- **MuPDF's runtime internals**: its store, message stream, memory pools,
  bindings, and helpers, which have no capability at the PDF level.
- **Compatibility layers**: no `fitz` module, no aliases, no reproduction of
  MuPDF's numerical quirks, and no byte-identical output.

Three capabilities stay with ecosystem tools, through tested recipes:

- **Cryptographic signing**: pyHanko, with incremental save keeping
  signatures valid. Watch underskrift, a BSD-2-Clause PAdES implementation
  over lopdf.
- **HTML and CSS pagination** (pymupdf's `Story`, `DocumentWriter`, and
  `Archive`): typst. Reassess fulgur, which pairs Blitz with krilla, around
  2027-01 as an engine path; the HTML subset for text boxes is in
  [Writing](#writing).
- **PDF/A validation of existing files**: veraPDF.

## Watchlist

- **parley** (linebender text layout): the candidate for styled runs and
  per-glyph fallback in [Writing](#writing).
- **fulgur and Blitz**: survival, API stability, and a stable Blitz 0.3.
- **underskrift**: an optional signature backend once it matures.
- **PP-DocLayout** (Apache-2.0) and later PP-OCR generations: candidates for
  the `[layout]` extra and for updates of the `[ocr]` models.
- **pymupdf, pymupdf4llm, and pymupdf-layout releases**: their new public
  names join the capability map.
- **Competitors**: pdf_oxide, oxidize-pdf, pypdfium2, kreuzberg, and docling.
- **abi3t (PEP 803)**: one `cp315-abi3.abi3t` wheel per platform could serve
  free-threaded 3.15 and every later free-threaded release, but GIL-enabled
  3.15 and later would prefer it to `cp311-abi3` (AGENTS.md has the
  evaluation). Reconsider before adding `cp316t`.

## Decisions

- **D1** (2026-10-22): this plan replaces the pymupdf parity program.
  pymupdf defines the capabilities that pylopdf must cover, not its API.
- **D2** (2026-10-22): engine-first. The engines' maintainers accept changes
  quickly, so capabilities that an engine owns are built upstream, and
  pylopdf's workarounds are temporary.
- **D3** (2026-10-22): one graphics model serves reading and writing.
- **D4** (2026-10-22): the parity manifest becomes the capability map. A
  planned entry names its milestone and no pylopdf API until the milestone
  designs one.
- **D5** (2026-10-22): the pymupdf-aligned renames on main since 0.15.0 ship
  only together with the API reset.
- **D6** (2026-10-22): `save()` and `to_bytes()` compress streams and prune
  unreachable objects by default, as qpdf and pikepdf write files. Both make
  smaller files without changing what a viewer shows; `compress=False` and
  `prune=False` keep the document's streams and objects as they are.
- **D7** (2026-10-22): `doc.metadata` keeps dates as the PDF date strings
  the file stores, such as `D:20260101120000+09'00'`. Producers write
  malformed dates often enough that a `datetime` would need a fallback to the
  raw string anyway, and reading and writing the same string round-trips
  exactly; a parsing helper can follow without changing the mapping.

## Evidence log

Dated facts that still drive decisions.

- **2026-07-22, market**: monthly PyPI downloads were pymupdf 106M, pypdf 116M,
  pypdfium2 68M, pdfplumber 54M, pymupdf4llm 24M, docling 20M, and pikepdf
  9.3M. Projects avoid AGPL dependencies in practice: doctr#486 removed
  pymupdf, browser-use#2610 treated a transitive dependency as a problem, and
  marker built pdftext explicitly without AGPL code.
- **2026-07-23, ecosystem**: krilla 0.8.2 produces veraPDF-validated PDF/A-1
  through PDF/A-4 and PDF/UA-1 but rejects embedded pages in validated output;
  typst-py wheels are 25.7–36.5 MB; veraPDF is effectively the only open
  PDF/A validator; pyHanko is the active MIT-licensed signing reference; XFA
  is deprecated in PDF 2.0.
- **2026-07-25, competitors**: pymupdf 1.28.0 wheels are 17.5–24.7 MiB; the
  pymupdf-layout 1.28.0 wheel is about 39.6 MiB under PolyForm Noncommercial
  plus commercial licensing; pdf_oxide has no renderer and self-reported
  benchmarks; oxidize-pdf is a separate MIT Rust competitor focused on
  structure-aware chunking.
- **2026-10-17, wheels**: thin-LTO abi3 wheels built from the 0.14.0 sources
  are 6.4–7.4 MiB, against 6.8–7.9 MiB for the published 0.14.0 wheels. Thin
  LTO sped up extraction by 6–14%; native wheels keep their symbol tables.
- **2026-10-18, rendering**: reusing the render cache across calls made
  re-renders 3–28% faster with at most 3 MiB more memory. MuPDF's 2–3× lead in
  warm rasterization comes from rasterization and its glyph and image caches,
  not from font parsing.
- **2026-10, damaged input**: over 14 corpus files with 8 deterministic damage
  types, pylopdf 0.14.0 opened 35 of 112 inputs and hayro alone rendered 103.
  lopdf 0.45 recovery raised usable openings from 33 to 57; damaged
  cross-reference streams and truncated files without a classic trailer remain
  rejected.
- **2026-10-22, engine interfaces**: hayro-interpret 0.8's `Device`
  (`draw_path`, `draw_glyph_run`, `draw_image`, `push_clip_path`,
  `push_transparency_group`, `begin_marked_content`) and krilla 0.8's
  `Surface` (`draw_path`, `draw_glyphs`, `draw_image`, `push_clip_path`,
  `push_opacity`, `push_mask`, `push_blend_mode`, `start_tagged`) cover the
  same graphics model. hayro's glyph runs carry outlines, glyph IDs, advances,
  and Unicode. krilla 0.8.2 writes only link annotations, and lopdf 0.45 has
  an `IncrementalDocument`.
