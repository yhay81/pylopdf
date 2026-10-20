# pylopdf roadmap

This is the canonical medium-term plan. It was rewritten on 2026-10-19 around
one program: **give Python developers everything pymupdf offers for PDF files,
under the MIT license, without giving up what makes pylopdf different** — a
pure-Rust core, small wheels, bounded behavior on untrusted input, a supported
concurrency contract, WebAssembly deployment, and first-class CJK and
multilingual text.

The previous roadmap's dated release narratives (v0.5–v0.14), its 2026-10-13
stabilization review, and its 2026-07 surveys remain in Git history
(`git show a233814:ROADMAP.md`) and in [CHANGELOG.md](CHANGELOG.md). Evidence
that still drives decisions is condensed in the [evidence log](#evidence-log).
[AGENTS.md](AGENTS.md) remains the day-to-day development context.

## Goal

pylopdf reaches **parity** when every capability of pymupdf 2.0's public API
that applies to PDF documents is available — under pymupdf's name with
pymupdf's meaning, or through a redesigned pylopdf API that replaces it — or is
listed as a reasoned non-goal with an ecosystem answer. Parity is about
capabilities, not about copying every name. It **exceeds** pymupdf where
pymupdf structurally cannot follow:

- One API with one way to do each task, typed results, and no hidden state.
- MIT licensing for the complete feature set, including layout analysis;
  pymupdf is AGPL and pymupdf-layout is PolyForm Noncommercial.
- Typed, bounded refusal (`LimitError`) on every untrusted-input path instead
  of best-effort behavior.
- A supported concurrency contract on GIL-enabled and free-threaded CPython.
- PyEmscripten and Cloudflare Python Workers deployment.
- CJK, vertical, right-to-left, and complex-script quality backed by a
  licensed real-world corpus.
- Verifiable results: redaction that proves removal, structure-aware
  extraction from tagged PDFs, and benchmarks that publish wins and losses.

Parity is measured, not asserted. The [parity manifest](#f3--parity-manifest)
maps each pymupdf API to its pylopdf status, CI keeps that map honest, and the
same map drives the [migration tools](#f9--migration-tooling).

## Baseline: 2026-10-19

Main is `a233814`: 0.14.0 plus 32 merged pull requests, mostly hardening,
performance, and tooling.

**Already covered.** Page organization (merge, select, insert,
rotate, MediaBox/CropBox, labels, TOC), text extraction
(`text`/`words`/`blocks`/`dict`/`rawdict`), search, bordered and borderless
tables, Markdown, vector-path and image extraction, PNG and SVG rendering with
ordered parallel batches, shaped and subset-embedded generated text, textbox
layout, AcroForm filling with regenerated appearances, attachments, AES-256
encryption, image compression, native offline OCR, resource limits, the
concurrency contract, and WebAssembly.

**Gaps by severity.** These come from comparing the pymupdf 1.28.0 source with
the pylopdf public API snapshot and implementation, with the two import
defects reproduced on the real-world corpus.

| Area | Gap | Severity |
| --- | --- | --- |
| Redaction | No content removal (`apply_redactions`) and no `scrub` | Critical |
| Annotations | Only Highlight and URI Link creation; no editing, deletion, or flattening | Critical |
| Forms | No field creation, deletion, properties, reset, or flattening; widget geometry is not returned | Critical |
| Drawing | No vector primitives (`Shape`, `draw_*`); no rotated, translucent, or outlined text | Major |
| Low-level access | No `xref_*` object API, no XMP read/write, no optional-content (OCG) API | Major |
| Extraction attributes | No span color, alpha, ascender, descender, or `char_flags`; no `clip=`; no image blocks, quads, or html/xml/json output | Major |
| Rendering | No arbitrary matrix, grayscale or RGB output, `annots=False`, or JPEG output | Major |
| LLM output | No `page_chunks`, image export, margins, or header/footer removal | Major |
| Resource reuse | Repeated `insert_image` and `show_pdf_page` duplicate objects (5 stamps of one page embedded 25 font files); `garbage` cannot merge duplicates | Major, in progress |
| Import | `insert_pdf` drops AcroForm registration (199 fields became 0, leaving 128 orphaned widgets) | Major, in progress |
| Persistence | No incremental save or font subsetting; damaged xref streams and truncated files are still rejected | Major |
| Navigation | No TOC detail or item editing; no internal, remote, or named link creation; no link editing | Minor |
| Geometry | No `Matrix`, `Quad`, `IRect`, or `Rect` algebra | Minor |
| Document properties | No Art/Bleed/TrimBox, viewer preferences, MarkInfo, language, or attachment info/update | Minor |
| Images in and out | Limited Pixmap conversion, constructors, and formats; no image or CBZ to PDF conversion | Minor |
| Rich text | No styled runs, per-glyph font fallback, or HTML subset | Minor |
| API consistency | pymupdf-named APIs whose pylopdf meaning differs (`get_images`, `get_pixmap` defaults, `annots`), and pylopdf's own synonyms (`render_page`, `Page.render`, `render_page_svg`) | Major |

## Principles

1. **One API.** Each task has one way to do it. pylopdf adds no synonyms of
   its own and ships no compatibility layer or aliases.
2. **Same name, same meaning.** When pymupdf has an API with the meaning
   pylopdf needs, pylopdf uses pymupdf's name, argument names, constants, and
   data shapes, even where a clearer name exists: familiarity across pymupdf's
   user base, and in the code that coding assistants write, outweighs local
   naming taste. A pymupdf-named API may support a subset of pymupdf's
   parameters; an unsupported parameter raises `TypeError` instead of being
   reinterpreted. Extensions that leave pymupdf's parameters and defaults
   unchanged, such as `max_*` limits or `font_language=`, are allowed.
3. **Redesign for substance, under a new name.** pylopdf replaces a pymupdf
   design only for a substantive defect — hidden state that a forgotten
   `update()` loses, positional tuples, magic numbers, objects
   encoded as strings, inconsistent numbering, unsafe defaults, or global
   state — or when a standard Python protocol such as a mapping, a sequence, or
   a context manager replaces a bespoke family of methods. The replacement
   gets a new name, and the pymupdf name is not provided. Style alone, such as
   a `get_` prefix or camelCase dictionary keys, is never a reason.
4. **Migration through tools, not shims.** The parity manifest drives a
   codemod, migration hints for pymupdf names, and an `llms.txt` guide for
   coding assistants ([F9](#f9--migration-tooling)).
5. **Break once before 1.0.** pylopdf has no established user base to protect,
   so redesigns land without deprecation windows. The
   [API reset](#m0--api-reset) gathers them into one breaking release.
6. **One-way data flow.** lopdf's `Document` is the sole editable source of
   truth; hayro is an immutable view for rendering and extraction. An
   unedited, unencrypted document may use its original input bytes only when
   every reference reachable from its trailer resolves to the same object in
   lopdf's graph and hayro reads the same pages as the page-tree walker. Every
   edit invalidates that fast path, so rendered output always reflects the
   edited and saved state. krilla returns bytes that are imported into lopdf.
   Engines never share mutable state.
7. **Clear engine ownership.** lopdf edits, hayro renders and extracts, krilla
   with HarfRust generates text and appearances, and pylopdf's content engine
   ([F8](#f8--content-stream-engine)) interprets and rewrites content streams
   inside the lopdf graph.
8. **Bounded by default.** Every caller input and decoded amplification has a
   documented cap. Python checks it before PyO3 copies and Rust checks it
   again. Trusted workloads opt out explicitly with `None`. Refusal raises
   `LimitError` with a stable code and returns no partial result.
9. **Prepare, then commit.** A mutation plans all fallible work first,
   invalidates caches immediately before its first change, and leaves the
   document unchanged on refusal. Structural changes bump the page generation,
   and stale views raise.
10. **Lightweight handles.** `Page`, `Annot`, `Widget`, `Link`, and outline
    items are views of a document, a generation, and an object ID. Setters
    apply immediately and atomically, regenerating appearances in the same
    operation, so there is no hidden dirty state and no `update()`; pymupdf
    code that calls it receives a migration hint.
11. **Small core, optional data.** The core abi3 wheel stays at or below
    9.0 MiB on every platform; the thin-LTO wheels built from the 0.14.0
    sources are 6.4–7.4 MiB. Fonts, OCR models, and layout models ship as
    independently versioned data packages. Each pull request reports its wheel
    size delta, and growth above 0.25 MiB needs a written justification.
12. **Integrate where an ecosystem leader exists.** typst for typesetting and
    new-document PDF/A, pyHanko for cryptographic signing, and veraPDF for
    PDF/A validation. Integration recipes are covered by tests.
13. **Evidence over claims.** Every feature lands with real-document or
    adversarial regressions, save/reopen and render checks, published
    benchmarks for performance-relevant paths, and upstream reports for engine
    limitations.

## Definition of done

Every feature pull request satisfies all of the following before merge.

- **API.** Follows the [API design](#f0--api-design): pymupdf's name and
  meaning where a counterpart exists, a new name where pylopdf redesigns;
  complete type hints and docstrings; an updated `pylopdf_core.pyi`;
  `api/public-api.json` refreshed after reviewing runtime, typing,
  documentation, and SemVer impact; updated parity manifest entries, including
  the migration hint for every replaced pymupdf name.
- **Safety.** Caller input caps before PyO3 copies, repeated in Rust; new
  `LimitError` codes documented in the security guide; fallible growth for
  input-derived collections; no partial results; prepare-then-commit; the cache
  and generation rules in AGENTS.md.
- **Tests.** Python tests only, with no Rust unit tests: success, boundary,
  refusal, and malformed-input cases; a save/reopen round trip; rendered or
  extracted verification; a redistributable real-world fixture when behavior
  depends on producers. pymupdf may serve as a development-only oracle where a
  comparison is meaningful; it is never a runtime dependency or a required
  test dependency.
- **Portability.** The free-threaded lanes pass, and the native/PyEmscripten
  contract in `tools/pyodide_compat.py` covers the feature unless it depends on
  OCR or threads.
- **Documentation.** The English API reference and guide change in the same
  pull request. Japanese, Simplified Chinese, and Korean follow before the
  release tag, as LANGUAGES.md requires. The migration guide gains a mapping
  row.
- **Measurement.** The wheel size delta, a benchmark entry for
  performance-relevant paths, and fuzz seeds for every new parser.
- **Changelog.** One changelog fragment ([F2](#f2--changelog-fragments)).

## Execution model

### Workstreams

After the module split ([F1](#f1--module-split)), each workstream owns a set of
modules. Concurrent pull requests stay inside one workstream. A change to
another workstream's module is coordinated as its own small pull request.

| Workstream | Scope | Owned modules after F1 (indicative) |
| --- | --- | --- |
| A — Extraction | `get_text` family, search, tables, Markdown, structure | `rust/src/extract/`, `src/pylopdf/_text.py`, `_tables.py`, `_markdown.py` |
| B — Rendering | Pixmap, rasterization options, SVG | `rust/src/render/`, `rust/src/pixmap.rs`, `src/pylopdf/_pixmap.py` |
| C — Generation | Shapes, text, images, fonts, page placement, page geometry | `rust/src/draw/`, `generate.rs`, `layout.rs`, `src/pylopdf/_shape.py`, `_fonts.py`, `_geometry.py` |
| D — Interactive | Annotations, links, AcroForm, flattening | `rust/src/annots/`, `rust/src/form/`, `src/pylopdf/_annots.py`, `_widgets.py` |
| E — Structure | Low-level objects, metadata and XMP, TOC, labels, OCG, attachments | `rust/src/lowlevel.rs`, `rust/src/navigation/`, `src/pylopdf/_lowlevel.py`, `_layers.py` |
| F — Content engine | Content interpretation and rewriting, redaction, sanitization | `rust/src/content/`, `src/pylopdf/_redact.py` |
| G — Persistence | Save options, deduplication, incremental save, recovery, encryption, subsetting | `rust/src/save/`, `recovery.rs`, `object_recovery.rs` |
| H — Intelligence | OCR modes, layout model, LLM-oriented output | `rust/src/ocr*`, `src/pylopdf/_ocr.py`, model packages |

Shared files stay cheap to merge. Changelog fragments remove `CHANGELOG.md`
conflicts. `api/public-api.json` and the parity manifest are regenerated after
each rebase. `src/pylopdf/__init__.py` and `rust/src/lib.rs` receive only
append-only re-exports and registrations. Run at most one writer per
workstream; four to six concurrent pull requests is the expected range.

Each pull request names its roadmap item, such as `F5` or `M3`, in its
description, and updates the parity manifest entries it completes. A
milestone is done when its acceptance criteria pass on main. Every workstream
implements against the F0 API design; a change to a shared design rule goes
through F0 first.

### Waves

Waves define order, not dates. A release ships when one or more milestones are
complete, and version numbers are assigned at release time.

| Wave | Work that runs in parallel | Gate for the next wave |
| --- | --- | --- |
| 0 — in flight | hayro 0.8 (#233); AcroForm registration on `insert_pdf` (#234) and field pruning on page deletion; XObject reuse (#231); save-time merging of identical objects; CPython 3.15 and 3.15t (#230); F3 parity manifest (#235), which touches only `api/`, `tools/`, and `tests/`; the F0 API design draft | Merged to main; F0 approved |
| 1 — foundations | F1 alone during a short freeze of `document.rs` and `__init__.py`; then F2, F4, and F9, one owner each | F1 merged with an unchanged API snapshot and corpus |
| 2 — API reset | M0 by workstream, released together as one breaking release; F5–F8 in parallel | M0 acceptance |
| 3 — breadth | M1 (A, B), M2 (C), M5 (E), F8 continued (F) | Milestone acceptance |
| 4 — interaction | M3 (D), then M4 (F) | Milestone acceptance |
| 5 — depth | M6 (A, H), M7 (G) | Milestone acceptance |
| 6 — rich text | M8 (C) | Milestone acceptance |
| 7 — freeze | [v1.0 readiness](#v10--api-freeze) | Parity manifest complete |

## Foundations

### F0 — API design

Write `api/DESIGN.md` before implementation fans out. It states the design
rules from [Principles](#principles) 1–5 as checkable rules; naming; result
types (NamedTuples for pymupdf's positional records, frozen dataclasses for
richer records, TypedDicts for interchange formats such as `dict` output,
handles for document-backed objects, and IntEnum/IntFlag for codes and flags);
collections; mutation, builders, and transactions; errors, limits, and
warnings; coordinates; constraints of the Python 3.10 floor; and the target
signature of every existing API and every redesigned capability. Workstreams
implement against it, and a change to a rule goes through it.

Acceptance: maintainer review; every M0 reset item resolved in the design;
the parity manifest's provisional names finalized.

### F1 — Module split

Split `rust/src/document.rs` (11,804 lines) and `src/pylopdf/__init__.py`
(4,230 lines) behind the existing import surface.

- Rust: a `rust/src/document/` module with the `_Document` class and shared
  state in `mod.rs`, plus domain submodules for page structure, rendering
  policy, text and table caches, navigation, metadata, attachments, forms,
  annotations, drawing, saving, and validation helpers. Keep one
  `#[pymethods]` block that delegates to submodule functions; do not enable
  PyO3 `multiple-pymethods`, whose `inventory` registration this project has
  not validated on PyEmscripten.
- Python: private `_errors.py`, `_types.py`, `_limits.py`, `_document.py`,
  `_page.py`, `_pixmap.py`, `_tables.py`, and `_ocr.py` modules, with
  `__init__.py` as the re-export surface. Public classes keep
  `__module__ == "pylopdf"` so reprs, documentation, and pickling stay stable.
- Record the module ownership map from [Workstreams](#workstreams) in
  AGENTS.md.

Acceptance: `api/public-api.json`, the corpus results, and the
native/PyEmscripten compatibility hashes are unchanged; benchmarks stay within
noise; and no other open pull request touches the split files during the
freeze.

### F2 — Changelog fragments

Each pull request adds `changelog.d/<slug>.<added|changed|deprecated|removed|fixed|security>.md`.
`tools/changelog.py` assembles the fragments into `CHANGELOG.md` at release
time, CI requires one fragment unless a pull request is labeled
`no-changelog`, and the release procedure in AGENTS.md gains the assembly step.

### F3 — Parity manifest

`api/pymupdf-parity.json` holds one entry per pymupdf 2.0 public API: module
functions, constants, and the members of `Document`, `Page`, `Annot`,
`Widget`, `Link`, `Outline`, `Pixmap`, `TextPage`, `Shape`, `TextWriter`,
`Font`, `Point`, `Rect`, `IRect`, `Quad`, `Matrix`, `Story`,
`DocumentWriter`, `Archive`, and `DisplayList`. Each entry records its
`status` (`available`, `planned`, `integration`, or `non-goal`), its
`relation` (`same` when pylopdf provides the pymupdf name with pymupdf's
meaning, `replaced` when a redesigned pylopdf API provides the capability and
the pymupdf name is absent), the pylopdf equivalent, a one-sentence migration
`hint` for every replaced name, a `reset` note when an existing pylopdf API
must change to honor its pymupdf name, the target milestone, and a note. The
2026-10-19 counts of public methods are Document 141, Page 115, Annot 53,
Pixmap 40, Rect 22, Font 20, Shape 17, TextPage 16, Story 16, Link 11,
Outline 11, Matrix 7, Quad 7, Widget 5 plus 30 public attributes,
TextWriter 5, Archive 4, DisplayList 4, and DocumentWriter 3.

- `tools/check_parity.py` verifies that every `available` entry names a
  symbol present in `api/public-api.json`, that `same` entries keep pymupdf's
  name, that every replaced name has a hint, and that every `planned` entry
  has a milestone. It reports coverage per class and relation and lists the
  remaining `reset` items, which form M0's work list.
- The manifest is the data source for the [migration tools](#f9--migration-tooling).
- The migration guide's parity tables are generated from the manifest in all
  four locales, with localized headings and untranslated API names. The
  current guide still lists `rawdict` as unsupported although 0.14.0 added it;
  generation removes that class of drift.
- Record API names and statuses only. Never copy pymupdf code or documentation
  text, which is AGPL-licensed.

### F4 — Geometry types

Add `Point`, `Rect`, `IRect`, `Quad`, and `Matrix` in top-left display space
with pymupdf's value-returning members: `Rect.contains`, `intersects`,
`is_empty`, `is_infinite`, `is_valid`, `round`, `morph`, `quad`, `get_area`,
`torect`, the corner properties, and the `|`, `&`, and `*` operators;
pymupdf's `Matrix` constructors, `is_rectilinear`, `*`, and `~`; and
`Quad.rect`, `morph`, `is_rectangular`, `is_convex`, `is_empty`, and `*`.
Every API accepts these types and plain tuples interchangeably.

The types stay immutable, because the values are hashable, thread-safe, and
already returned everywhere. pymupdf's in-place mutators (`include_point`,
`include_rect`, `intersect`, `normalize`, `transform`, and the `Matrix.pre*`
family) are not provided, because the same names on immutable values would
silently do nothing; the codemod rewrites them to operators.

### F5 — Low-level object API

Expose the lopdf graph as a typed object model in the style of pikepdf:
names, dictionaries, arrays, streams, and indirect references as Python
objects, reachable from the trailer, the catalog, each page's object, and
lookup by object number. This replaces pymupdf's `xref_*` methods
(`xref_object`, `xref_get_key`, `xref_set_key`, `update_stream`, and the rest),
which encode objects as PDF syntax strings; the codemod and migration hints
point to the object model.

- lopdf 0.45 keeps its object parser and writer private, so pylopdf converts
  objects itself, or upstreams a public API. Decoded streams use the existing
  bounded multi-layer decoder.
- Every write invalidates the hayro snapshot and derived caches. Writes to the
  trailer, Catalog, page-tree nodes, or pages also bump the page generation.
- pymupdf xref numbers are object numbers; pylopdf resolves them to lopdf's
  `(number, generation)` pairs from the object table.
- The API is covered by SemVer but documented as raw access whose semantic
  correctness is the caller's responsibility.

### F6 — Resource reuse and duplicate collection

- In review as #231: reuse one XObject for repeated `insert_image` input and
  one imported Form for repeated `show_pdf_page` placements of an unchanged
  source page. Five placements of one `bill-hr815.pdf` page then write one
  Form and 5 font files (39.7 KB instead of 192 KB).
- Done: save-time merging of identical objects through pymupdf's `garbage`
  levels for `save` and `tobytes`: 0 keeps every object; 1 removes
  unreferenced objects; 2 also compacts numbering; 3 also merges identical
  non-stream objects; 4 also merges identical streams. `True` and `False`
  keep their meaning because `bool` is a subclass of `int`. Merging hashes
  each object with its references replaced by the equality class of their
  targets, confirms equality before retargeting references, and visits the
  reference graph's strongly connected components children first, so chains
  of equal objects merge in one pass, in time linear in the object count.

### F7 — Content and appearance builder

Build one typed writer for content operators and use it everywhere pylopdf
emits content: paths, rectangles with corner radii, Bézier curves, Gray, RGB,
and CMYK colors, line width, cap, join, miter limit and dashes, ExtGState
opacity and blend modes, clipping, Standard 14 and krilla text runs, and
XObject placement. It also builds resource dictionaries and Form XObjects with
`/BBox` and `/Matrix`. It replaces ad hoc operator assembly in `draw.rs` and
`form.rs`, emits deterministic minimal operators, and serves M2 shapes, M3
appearances, M4 redaction fills, and widget appearances.

### F8 — Content-stream engine

Build a bounded interpreter and rewriter for content streams in the lopdf
graph.

- Interpretation over lopdf's strict content decoding: graphics state
  (`q`/`Q`/`cm`), text state (`Tf`, `Tc`, `Tw`, `Tz`, `TL`, `Ts`, `Tr`, `Tm`,
  `Td`, `TD`, `T*`, `'`, `"`), marked content, inline images, and Form XObject
  recursion with cycle and depth limits.
- Font metrics: simple-font `/Widths`, `/FirstChar`, and `/Encoding` with
  `/Differences`; Standard 14 AFM metrics; CID-font `/W`, `/DW`, `/W2`, and
  `/DW2` with CMaps through `hayro-cmap`, which is already in the dependency
  tree; Type 3 `/FontMatrix`; glyph boxes from FontDescriptor ascent,
  descent, and bounding box.
- Geometry agreement: glyph positions agree with hayro extraction within a
  documented tolerance across the corpus.
- Rewriting: drop glyphs from `Tj` and `TJ` with compensating `TJ`
  adjustments so the remaining glyphs keep their positions, drop path
  painting, replace or remove image `Do` operators, rewrite inline images,
  copy shared Form XObjects before editing them, and re-serialize.
- No public API until M4; private Python hooks drive the tests.

### F9 — Migration tooling

- `python -m pylopdf.migrate` rewrites pymupdf programs with LibCST from an
  optional `[migrate]` extra: renames, argument changes, and page-number
  shifts recorded in the manifest, plus `TODO` comments where a change of
  meaning needs a person.
- Migration hints: looking up a pymupdf name that pylopdf replaced raises
  `AttributeError` naming the replacement. The hints are generated from the
  manifest into a private module and consulted only after normal lookup fails,
  so successful lookups cost nothing.
- `docs/en/llms.txt` gains the mapping for coding assistants.

Acceptance: the codemod turns a corpus of pymupdf example programs, written
for this repository rather than copied, into passing pylopdf programs, and
lists the manual steps that remain.

## Milestones

### M0 — API reset

All workstreams. Depends on F0 approval, F1, and F4. Ships as one breaking
release.

- Resolve every manifest `reset` item so that pymupdf names carry pymupdf's
  meaning. Known items: `Page.get_images` returns pymupdf's xref inventory as
  NamedTuples, with placements moving to `get_image_info` and image data to
  `extract_image`; `Page.get_pixmap` defaults to `alpha=False` RGB output and
  takes `matrix=` and `dpi=` instead of `scale=`; `Page.annots` returns
  read-only `Annot` handles; `save(object_streams=)` becomes `use_objstms=`;
  `compress_images` takes `rewrite_images`' name and meaning; `render_svg`
  becomes `get_svg_image`; a missing file raises `FileNotFoundError`, and
  `FileDataError` and `EmptyFileError` exist as `PdfError` subclasses;
  `Permissions` members take pymupdf's names. Side-by-side runs against
  pymupdf added: the `Document` constructor's positional `filetype`,
  `authenticate` return codes, `set_metadata` round trips, pymupdf's page-box
  convention and `set_mediabox` side effects, return values of `insert_text`,
  `insert_image`, and `show_pdf_page`, full Standard 14 font names,
  `Pixmap.save(filename)`, clamped `Rect` widths, and geometric operators. The
  authoritative list is section 15 of `api/DESIGN.md`.
- Make encryption explicit: `save(encryption=)` takes pymupdf's
  `PDF_ENCRYPT_*` values with pymupdf's `PDF_ENCRYPT_NONE` default, and a
  password without an encrypting method raises instead of being ignored as
  in pymupdf or implying AES-256 as today.
- Remove pylopdf's own synonyms: `Document.render_page`, `Page.render`,
  `Document.render_page_svg`, and the duplicate OCR entry points.
- Redesign existing capabilities under new names: `embfile_*` becomes a
  `doc.attachments` mapping of attachment handles, and `get_toc`/`set_toc`
  become zero-based outline entries. Today's `copy_page`, which makes an
  independent copy, becomes pymupdf's `fullcopy_page`, because pylopdf never
  shares page objects. `get_form_fields` and `set_form_field` remain until M3
  replaces them.
- Publish codemod rules and hints for every change (F9).

Acceptance: the manifest has no `reset` entries; `api/public-api.json`, the
documentation in four languages, and the migration guide describe the new
surface; the codemod migrates the example corpus.

### M1 — Extraction and rendering depth

Workstreams A and B. Depends on hayro 0.8, M0, and F4.

Extraction:

- Span `color` (sRGB integer), `alpha`, `ascender`, `descender`, and
  `char_flags` (fill, stroke, clip, invisible, synthetic), read from hayro
  0.8 `DrawProps.paint` and `DrawMode` in the glyph-run callback. Spans split
  on color and alpha changes, as in pymupdf.
- `get_text(option, clip=, flags=, sort=, delimiters=)`; the `html`,
  `xhtml`, `xml`, `json`, and `rawjson` outputs; `get_textbox`,
  `get_text_selection`, `get_text_words`, and `get_text_blocks`.
- Flags: `TEXT_PRESERVE_LIGATURES`, `TEXT_PRESERVE_WHITESPACE`,
  `TEXT_PRESERVE_IMAGES` (type 1 image blocks), `TEXT_INHIBIT_SPACES`,
  `TEXT_DEHYPHENATE`, `TEXT_MEDIABOX_CLIP`,
  `TEXT_USE_CID_FOR_UNKNOWN_UNICODE` (plus U+FFFD placeholders for unmapped
  glyphs, which are skipped today), and `TEXT_ACCURATE_BBOXES` through hayro
  glyph outlines. Default output stays unchanged unless a flag is passed.
- `search_for(needle, clip=, quads=, flags=)` and `Document.search_page_for`.
- Inventory beyond M0's image APIs: `get_image_info(hashes=, xrefs=)`
  options, `get_image_rects`, `get_image_bbox`, `get_fonts(full=)`,
  `get_xobjects`, `Document.get_page_images`, `get_page_fonts`,
  `get_page_xobjects`, `extract_font(xref)`, and `get_char_widths`. hayro
  already reports image object IDs through `RasterImage::stream().obj_id()`,
  which `compress_images` uses today.
- Traces: `get_texttrace`, `get_bboxlog(layers=)`, `get_drawings(extended=True)`
  with the clip and group hierarchy, `get_cdrawings`, and `cluster_drawings`.

Rendering:

- `get_pixmap(matrix=, dpi=, colorspace=, clip=, alpha=, annots=)` and the
  same options on `Document.render_pages`. Arbitrary affine matrices use
  hayro 0.8 `render_into`, and `annots=` sets
  `InterpreterSettings.render_annotations` per call.
- Pixmap: `n` of 1, 3, or 4 (gray, RGB, RGBA), `colorspace`, `alpha`, `xres`,
  `yres`, `set_dpi`, `irect`, and `size`; constructors from encoded PNG or
  JPEG, raw samples, and `(document, xref)` images with soft masks;
  `tobytes(output=, jpg_quality=)` and `save(filename, output=, jpg_quality=)`
  for PNG, JPEG (through the existing `jpeg-encoder` dependency), and PNM/PAM;
  analysis with `pixel`, `color_count`, `color_topusage`, `is_unicolor`,
  `is_monochrome`, and `digest`; cheap transforms with `copy`, `shrink`,
  `set_alpha`, `invert_irect`, `clear_with`, `tint_with`, and `gamma_with`;
  `pil_image`, `pil_save`, and `pil_tobytes` when Pillow is installed.
- `get_svg_image(matrix=, text_as_path=)`. `text_as_path=False` needs an
  upstream hayro-svg option or a pylopdf SVG device; until then it raises
  instead of silently producing paths.

Acceptance: span colors match pymupdf exactly on the corpus; ascender-based
boxes meet a published IoU threshold; quads cover rotated-text search; a
diagonal matrix renders pixel-identically to the equivalent `dpi`; and every
default output is unchanged.

### M2 — Drawing and generation

Workstream C. Depends on M0, F4, and F7.

- `Page.new_shape()` returns a `Shape` with `draw_line`, `draw_rect(radius=)`,
  `draw_circle`, `draw_oval`, `draw_polyline`, `draw_bezier`, `draw_curve`,
  `draw_quad`, `draw_sector`, `draw_squiggle`, `draw_zigzag`,
  `finish(width, color, fill, lineCap, lineJoin, dashes, even_odd, morph, closePath, fill_opacity, stroke_opacity, oc)`,
  `insert_text`, `insert_textbox`, and `commit(overlay=)`, which writes one
  content stream per commit. `Page.draw_*` methods are single-shape shortcuts.
  A `Shape` is also a context manager that commits on a clean exit and writes
  nothing after an exception, and an uncommitted `Shape` warns when it is
  collected, so drawings are never lost silently.
- `insert_text` and `insert_textbox` gain `rotate=`, `morph=`,
  `render_mode=`, `border_width=`, `miter_limit=`, `fill=`,
  `stroke_opacity=`, `fill_opacity=`, `lineheight=`, and `oc=` for both
  Standard 14 and embedded fonts.
- `insert_image` gains `mask=`, `alpha=`, `xref=`, `width=`, `height=`, and
  `oc=`, plus JPX passthrough. Add `replace_image(xref, ...)` and
  `delete_image(xref)`. `show_pdf_page` gains `clip=`, `rotate=`, and `oc=`
  and returns the reusable xref.
- `Font` exposes metrics, `text_length`, `char_lengths`, `has_glyph`,
  `valid_codepoints`, `glyph_advance`, `glyph_bbox`, and style flags for
  Standard 14 and OpenType fonts. Add `get_text_length()` and `insert_font()`.
- Page geometry: `artbox`, `bleedbox`, `trimbox` and their setters,
  `mediabox_size`, `cropbox_position`, `bound()`, `transformation_matrix`,
  `rotation_matrix`, `derotation_matrix`, and `remove_rotation()`.
- Page operations: `move_page`, `fullcopy_page`, `insert_page(text=)`, and
  `Document.pages(start, stop, step)`.
- Conversion: `Document.insert_file()` and `convert_to_pdf()` for JPEG, PNG,
  and JPX (passthrough) images and CBZ archives (read with Python `zipfile`).
  Other raster formats go through optional Pillow, and `pylopdf.open()`
  converts image inputs.

Acceptance: shapes and text effects render within a published pixel
tolerance of pymupdf; opacity and rotation are verified by rendering and
extraction; a diagonal translucent watermark example is covered by tests; and
image and Form reuse hold across many pages.

### M3 — Annotations, links, and forms

Workstream D. Depends on F7 and M2.

- `Annot` handles mirror pymupdf: `type`, `rect`, `info` and `set_info`,
  `colors` and `set_colors`, `border` and `set_border`, `opacity` and
  `set_opacity`, `flags` and `set_flags`, `blendmode`, `line_ends`,
  `vertices`, `rotation`, `popup_rect` and `set_popup`, `is_open` and
  `set_open`, `irt_xref` and `set_irt_xref`, `set_name`, `file_info`,
  `get_file`, `update_file`, `get_text`, `get_textbox`, `get_pixmap`,
  `delete_responses`, `xref`, and `next`. There is no `update()`: setters
  regenerate appearances immediately. `Page` gains `annots(types=)`,
  `load_annot`, `first_annot`, `annot_names`, `annot_xrefs`, and
  `delete_annot`.
- Every pymupdf `add_*_annot` creator: text notes, FreeText with callouts,
  borders, and rotation, line, square, circle, polygon, polyline, ink, stamps
  (standard and image), file attachments, caret, and Highlight, Underline,
  StrikeOut, and Squiggly with `quads`, `start`/`stop`, and `clip`, plus
  Redact (applied in M4). Every annotation receives an `/AP /N` built with F7;
  the existing `text_markup_ap_ops` already produces Underline, StrikeOut,
  and Squiggly appearances.
- Render-only appearance synthesis for every supported annotation type and
  for widgets that rely on `/NeedAppearances`. The editable PDF stays
  unchanged; hayro draws only annotations that have an appearance stream.
- Links: `insert_link` for GOTO, GOTOR, URI, LAUNCH, and NAMED; `update_link`;
  `delete_link`; `Link` handles with `border`, `colors`, `flags`, and setters;
  `Document.resolve_link` and `resolve_names`.
- Forms: `Widget` handles with pymupdf's attributes (`field_name`,
  `field_label`, `field_value`, `field_type`, `field_flags`, `field_display`,
  `rect`, `xref`, border, fill and text styling, `text_maxlen`, `text_format`,
  `choice_values`, `button_caption`, `button_states()`, `on_state()`, the
  `script*` actions, and `is_signed`), `Page.widgets(types=)`, `add_widget`,
  `delete_widget`, `load_widget`, `Widget.reset()`, `Document.is_form_pdf`,
  `need_appearances()`, and `get_sigflags()`. Attribute assignment applies
  immediately, so `Widget.update()` is not provided. A by-name `doc.form`
  mapping replaces `get_form_fields` and `set_form_field`.
- `Document.bake(annots=, widgets=)` flattens the `/AS`-selected `/AP /N`
  into page content with the same BBox-to-Rect mapping hayro uses.
- Signature fields: create empty signature widgets and report signed state;
  an interop test signs a pylopdf-created field with pyHanko.
- JavaScript actions (widget `/AA`, document-level `/JavaScript`) can be
  authored and removed. pylopdf never executes them.
- M0 already returns read-only `Annot` handles from `Page.annots()`; M3 adds
  creation, mutation, widgets, and the form mapping.

Acceptance: every created annotation renders within tolerance in pylopdf and
pymupdf and survives save/reopen; flattening preserves rendered output and
extracted text; fields created by pylopdf can be filled by pymupdf and by
pylopdf.

### M4 — Redaction and sanitization

Workstream F. Depends on F7, F8, and the M3 annotation core.

- `Page.add_redact_annot(quad, text=, fontname=, fontsize=, align=, fill=, text_color=, cross_out=)`
  and `Page.apply_redactions(images=, graphics=, text=)` with pymupdf's
  constants: `PDF_REDACT_IMAGE_NONE`, `PDF_REDACT_IMAGE_REMOVE`,
  `PDF_REDACT_IMAGE_PIXELS`, and `PDF_REDACT_IMAGE_REMOVE_UNLESS_INVISIBLE`;
  `PDF_REDACT_LINE_ART_NONE`, `PDF_REDACT_LINE_ART_REMOVE_IF_COVERED`, and
  `PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED`; `PDF_REDACT_TEXT_REMOVE`,
  `PDF_REDACT_TEXT_REMOVE_INVISIBLE`, and `PDF_REDACT_TEXT_NONE`.
- Text: remove every glyph whose box intersects a redaction area, with
  compensating `TJ` adjustments; simple, CID (Identity and embedded CMaps),
  and Type 3 fonts; horizontal and vertical writing; invisible `Tr 3` OCR
  layers.
- Images: blank pixels and re-encode, or remove whole placements, including
  inline images, image masks, and soft masks.
- Vector graphics: remove covered or touched paths and intersecting shadings.
- Copy shared Form XObjects before editing them, remove overlapping
  annotations, and replace redaction annotations with their fill and overlay
  text.
- Verification contract: after application, re-interpretation with hayro
  finds no glyph, image pixel, or path inside a redacted area.
  `apply_redactions` returns a report, and saving rewrites the file without
  incremental history.
- `Document.scrub()` with all of pymupdf's options; `Page.clean_contents()`,
  `Annot.clean_contents()`, `wrap_contents`, `is_wrapped`, `read_contents`,
  `get_contents`, and `set_contents`; `recolor(components=)` for pages and
  documents.
- The security guide states what redaction does not remove: embedded font
  programs keep their glyphs until M7 subsetting, and metadata needs `scrub`.

Acceptance: an adversarial redaction corpus (kerned `TJ` arrays, shared
Forms, rotated pages, CJK CID fonts, Type 3 fonts, overlapping layers, inline
images, and masks) shows zero leaks, cross-checked by pymupdf extraction of the
redacted areas.

### M5 — Document structure and navigation

Workstream E. Depends on M0 and F5.

- XMP: `get_xml_metadata`, `set_xml_metadata`, and `del_xml_metadata`, with
  optional synchronization from `set_metadata`.
- Outline: the zero-based outline entries from M0 gain destinations (kind,
  target point, zoom), color, bold, italic, collapse state, item editing, and
  traversal handles. They replace pymupdf's `get_toc(simple=False)`,
  `set_toc_item`, `del_toc_item`, `get_outline_xrefs`, and `Outline`.
- Document properties: `pagemode`, `pagelayout`, `set_pagemode`,
  `set_pagelayout`, `markinfo`, `set_markinfo`, `language` and
  `set_language` for documents and pages, `is_fast_webaccess`,
  `version_count`, `is_dirty`, `has_links`, `has_annots`, `permissions`, and
  `get_page_numbers(label)`.
- Optional content: `get_ocgs`, `add_ocg`, `get_layer`, `get_layers`,
  `set_layer`, `add_layer`, `switch_layer`, `layer_ui_configs`,
  `set_layer_ui_config`, `get_oc`, `set_oc`, `get_ocmd`, `set_ocmd`, and
  `Page.get_oc_items`, plus `oc=` on insertion APIs. hayro reads
  `/OCProperties /D`, so edits to the default configuration reach rendering
  and extraction directly; other configurations render through a render-only
  snapshot.
- Attachments: file name, description, size, dates, and in-place updates on
  the `doc.attachments` handles from M0, shared with M3 file attachment
  annotations. They replace pymupdf's `embfile_info`, `embfile_upd`, and
  `embfile_count`.

Acceptance: layer visibility changes rendering and extraction; XMP round-trips
through an XML parser; TOC round-trips agree with pymupdf.

### M6 — Document intelligence

Workstreams A and H. Depends on hayro 0.8 and M1.

- Tagged PDF: track marked-content IDs through hayro 0.8
  `begin_marked_content`, resolve `/StructTreeRoot`, `/ParentTree`, and role
  maps, attach structure IDs to spans, and let Markdown use headings, lists,
  tables, figure alternate text, and structure reading order when the tree is
  trustworthy. This is `TEXT_COLLECT_STRUCTURE`.
- ActualText: contribute marked-content property access upstream to hayro,
  honor `/ActualText` by default, and add `TEXT_IGNORE_ACTUALTEXT`.
- pymupdf4llm parity on `to_markdown`: `page_chunks`, `write_images`,
  `embed_images`, `image_path`, `image_format`, `dpi`, `extract_words`,
  `margins`, `ignore_graphics`, `graphics_limit`, and header/footer removal
  from geometry repeated across pages.
- Hybrid OCR: recognize only areas without legible digital text, like
  pymupdf's `get_textpage_ocr(full=False)`, rendering just those areas through
  the hayro 0.8 clip viewport; add an images-to-searchable-PDF convenience.
- Tables: pymupdf's `find_tables` parameters (strategies, explicit lines,
  tolerances, minimum words, `add_lines`, `add_boxes`), `Table.header`,
  `row_count`, `col_count`, `rows`, `cells`, and `to_pandas()` with optional
  pandas.
- Layout model: a `[layout]` extra running an Apache-2.0 PP-DocLayout model
  through RTen, versioned separately like the OCR models, behind
  `Page.get_layout()`.

Acceptance: human-verified ground truth on representative business documents,
and published comparisons with pymupdf4llm with and without pymupdf-layout.

### M7 — Persistence and robustness

Workstream G. Depends on F5, F6, and F8.

- `save` and `tobytes` gain `clean=`, `deflate_images=`, `deflate_fonts=`,
  `ascii=`, `expand=`, `pretty=`, `no_new_id=`, `preserve_metadata=`,
  `compression_effort=`, and `incremental=`, together with `ez_save()`,
  `saveIncr()`, and `can_save_incrementally()`.
- Incremental save: retain the original bytes, track dirty objects centrally
  (F1 makes the write paths enumerable), and append through lopdf
  `IncrementalDocument`. Existing signatures stay valid, which a pyHanko
  validation test proves. Refuse repaired inputs and encryption changes.
- `subset_fonts(fallback=)`: collect glyph usage through interpretation,
  subset TrueType and CFF programs with `subsetter`, which krilla already
  links, rewrite font programs and width arrays, and remap content strings
  when glyph IDs change.
- Damaged-input recovery: add opt-in reconstruction for damaged xref streams
  and truncated files without a classic trailer, the classes that remain
  rejected after lopdf 0.45 recovery. In the 2026-10 evaluation, hayro alone
  rendered 103 of 112 damaged inputs; in this repository's reproduction,
  lopdf 0.45 recovery lets pylopdf open 57 with a usable page tree. Rebuild
  the lopdf graph itself, using an object scan or hayro-syntax's repaired view
  as the oracle, and let hayro read lopdf's serialization, so editing, saving,
  and rendering cannot disagree. Report the result through `is_repaired` and a
  warning.
- Encryption: verify reading of RC4 40/128-bit and AES-128/256, and add
  `PDF_ENCRYPT_AES_128` and `PDF_ENCRYPT_KEEP` to the `encryption=` option
  that M0 introduces; `PDF_ENCRYPT_KEEP` preserves a supported input handler.
  Writing RC4 raises, while reading stays supported.
- Journal: `journal_enable`, `journal_undo`, `journal_redo`,
  `journal_can_do`, `journal_position`, `journal_op_name`, `journal_save`, and
  `journal_load`, with `with doc.transaction(name):` replacing
  `journal_start_op` and `journal_stop_op`, built from object-level snapshots
  per operation under a memory budget.
- `rewrite_images` parity: lossless recompression, bitonal and grayscale
  targets, and `set_to_gray`, completing the `rewrite_images` that M0 aligns
  with pymupdf's meaning.

Acceptance: pyHanko signatures stay valid after incremental edits; an
independent structural check (pikepdf in the interop group) accepts every
output; subsetting reduces size without changing extracted text or rendering.

### M8 — Rich text

Workstream C. Depends on M2.

- Styled runs and per-glyph fallback: `insert_textbox` accepts runs with font,
  size, color, weight, style, and link, falls back across the installed
  locale and script providers, and keeps bidirectional ordering and
  justification on the existing UAX #14 and HarfRust layout. Evaluate parley
  (see the [watchlist](#watchlist)) before extending `layout.rs`.
- `insert_htmlbox(rect, text, css=, scale_low=, archive=, rotate=, oc=, opacity=)`
  for an HTML and CSS subset: block and inline elements, font properties,
  color, lists, line breaks, links, and images from an archive mapping. It
  returns the spare height and scale like pymupdf.
- `TextWriter` with `append`, `appendv`, `fill_textbox`, and
  `write_text(color, opacity, morph, matrix, render_mode, oc)`.

Acceptance: HTML subset conformance tests, mixed CJK and RTL runs, and
comparison with pymupdf `insert_htmlbox` on the supported subset.

### v1.0 — API freeze

- The parity manifest has no `planned` entries left, and every replaced
  pymupdf name has a migration hint.
- Independent field reports cover the new surfaces, especially redaction,
  forms, and annotations. Adoption signals, with roughly 1,000 GitHub stars as
  an indication rather than a trigger, inform the decision but do not replace
  correctness and compatibility gates.
- Deprecations are resolved, documentation is complete in all four
  languages, every artifact is verified on its target, and the stability
  policy is frozen.

## Continuous tracks

- **Real-document quality.** Human-verified ground truth for text, reading
  order, table cells, and OCR on Japanese business forms, multicolumn prose,
  mixed scripts, and scans. pymupdf similarity stays a signal, not the
  definition of correctness.
- **Performance.** Close MuPDF's 2–3× warm-raster lead through upstream glyph
  and image caching and thread-safe hayro caches; keep publishing wins and
  losses.
- **Security.** Fuzz lanes for every new parser and rewriter (object syntax,
  content engine, XMP, HTML subset, CBZ), and extend `DocumentLimits.web()` to
  every new surface.
- **Distribution.** CPython 3.15 and 3.15t (#230), PyEmscripten parity, and the
  wheel budget.

## Upstream contributions

- **hayro**: marked-content properties for ActualText (M6), Unicode for Type 3
  glyphs without ToUnicode (LaurenzV/hayro#1331), parsing an object stream's
  member table once (LaurenzV/hayro#1545, open), thread-safe render caches,
  Type 1 font metadata for span flags, a text-preserving SVG mode,
  stencil-mask performance (LaurenzV/hayro#1315), and `num_threads`
  (LaurenzV/hayro#1317). Depth parameters for `Function::new`,
  `ColorSpace::new`, and `parse_procedure_inner` would let pylopdf drop its
  guards.
- **lopdf**: a public object parser and serializer (F5), recovery reporting
  through the API instead of `log`, a linear unterminated-`stream` scan,
  xref-stream rebuilds from the catalog, consistent `compression_level`, and
  implementing or removing the dead `linearize` flag. Dropping `/Prev` and
  `/XRefStm` from rebuilt trailers merged upstream as J-F-Liu/lopdf#609 on
  2026-10-18.

## Non-goals

These boundaries remain after the 2026-10-19 rewrite. Each has an ecosystem
answer.

- **Formats that need a layout engine**: XPS and OpenXPS, EPUB, MOBI, FB2,
  HTML, and text documents, with the reflowable APIs (`layout`, chapters,
  locations, bookmarks). Images and CBZ are in scope (M2).
- **Full HTML/CSS pagination**: `Story`, `DocumentWriter`, `Archive`, `Xml`,
  and `apply_css`. Use typst, and reassess fulgur around 2027-01. The HTML
  subset in M8 covers text boxes.
- **Executing JavaScript or XFA**: authoring and removing JavaScript actions
  is in scope (M3). XFA is deprecated in PDF 2.0 and has no Rust
  implementation.
- **Native cryptographic signing**: pyHanko provides active MIT-licensed PAdES
  through B-LTA. Signature fields and signed-state inspection are in scope
  (M3); watch underskrift.
- **Converting or validating arbitrary PDFs as PDF/A**: krilla's validated
  export rejects embedded pages, and validation would duplicate veraPDF. Use
  typst for new documents; `get_pdfa_claim` stays.
- **MuPDF internals without PDF-level meaning**: `DisplayList`,
  `Page.run(device)`, store and anti-aliasing controls in `TOOLS` (reassess
  anti-aliasing if vello_cpu exposes it), colorspace management beyond
  `Colorspace(name, n)` values and the `cs*` and `CS_*` constants,
  `Pixmap.warp`, and `Pixmap.pdfocr_*`. Images-to-PDF plus native OCR covers
  the last case.
- **Compatibility layers**: no `fitz` module, no aliases for redesigned APIs,
  and no byte-identical output. The F9 tools handle migration.
- **RC4 encryption output**: insecure; reading remains supported.

## Watchlist

- **hayro 0.8 and later**: the migration is in progress. hayro 0.8.0 bounds
  nested arrays and dictionaries at 64 levels, but nested Type 4 function
  braces and deep function and color-space chains still overflow its stack, so
  `content_guard.rs`, `graph_guard.rs`, and `postscript_scan.rs` stay. The
  migration needs its own reviewed pixel diff: vello_cpu resamples large
  scans, and 6 of 20 corpus pages changed.
- **fulgur** (Blitz plus krilla, HTML to PDF): reassess around 2027-01 for
  survival, API stability, and a stable Blitz 0.3.
- **underskrift** (BSD-2-Clause PAdES over lopdf): reconsider as an optional
  signature backend after it matures.
- **parley** (linebender text layout): evaluate for M8 before extending
  `layout.rs`.
- **PP-DocLayout** (Apache-2.0) and later PP-OCR generations: candidates for the
  `[layout]` extra (M6) and for `[ocr]` model updates.
- **pymupdf, pymupdf4llm, and pymupdf-layout releases**: record new public
  APIs in the parity manifest as they appear.
- **Competitors**: pdf_oxide, oxidize-pdf, pypdfium2, kreuzberg, and docling.
- **abi3t (PEP 803)**: one `cp315-abi3.abi3t` wheel per platform could serve
  free-threaded 3.15 and every later free-threaded release, but GIL-enabled
  3.15+ installs would prefer it to `cp310-abi3` (AGENTS.md has the
  evaluation). Reconsider before adding `cp316t`.

## Decisions in this rewrite

- **D1**: the scope expands to every capability of pymupdf's API that applies
  to PDF documents, except the [non-goals](#non-goals).
- **D2**: one API in which the same name means the same thing; pymupdf names
  carry pymupdf's meaning, and redesigns take new names. This replaces
  "pymupdf-style, not pymupdf-compatible" and rules out a compatibility layer.
- **D3**: geometry types are adopted (F4) as immutable values; pymupdf's
  in-place mutators are not provided.
- **D4**: lightweight `Annot`, `Widget`, `Link`, and outline handles replace
  the dictionary-only policy (M0, M3); setters apply immediately, and there is
  no `update()`.
- **D5**: the low-level object API becomes public (F5) as a typed object
  model instead of pymupdf's string-based `xref_*` methods.
- **D6**: incremental save moves from the watchlist into M7.
- **D7**: opt-in reconstruction of damaged xref streams and truncated files is
  scheduled (M7) under the single-source-of-truth constraint.
- **D8**: redaction is in scope with a verification contract (M4).
- **D9**: the core wheel budget is 9.0 MiB.
- **D10**: changelog fragments (F2) and the parity manifest (F3) become part
  of the process.
- **D11**: migration is served by tools generated from the manifest (F9): a
  codemod, migration hints, and `llms.txt`.
- **D12**: there are no deprecation windows before 1.0; the API reset (M0)
  ships as one breaking release.
- **D13**: the API design (F0) is written and reviewed before implementation
  fans out.

## Evidence log

Dated facts that still drive decisions. Earlier detail is in Git history.

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
  are 6.4–7.4 MiB, versus 6.8–7.9 MiB for the published 0.14.0 wheels. Thin LTO
  sped up extraction by 6–14%; native wheels keep their symbol tables.
- **2026-10-18, rendering**: cross-call render-cache reuse made re-renders
  3–28% faster with at most 3 MiB more memory. MuPDF's 2–3× warm-raster lead
  comes from rasterization and its glyph and image caches, not font parsing.
- **2026-10, damaged input**: over 14 corpus files with 8 deterministic damage
  types, pylopdf 0.14.0 opened 35 of 112 inputs and hayro alone rendered 103.
  lopdf 0.45 recovery raised usable openings from 33 to 57 in this
  repository's reproduction; damaged xref streams and truncated files without a
  classic trailer remain rejected.
- **2026-10-19, parity**: the pymupdf 1.28.0 source defines 141 public
  `Document` methods, 115 `Page` methods, and 53 `Annot` methods. On the
  corpus, five `show_pdf_page` placements of one `bill-hr815.pdf` page
  embedded 25 font files, and `insert_pdf` of `f1040.pdf` (199 fields) left 0
  registered fields and 128 orphaned widgets. The first parity manifest
  classified 948 pymupdf 1.28.0 entries as 40 supported, 120 deviations,
  593 planned, and 195 non-goals, before its reclassification under the
  same-name, same-meaning rule.
- **2026-10-20, parity**: pymupdf 2.0 requires Python 3.11 or later and
  installs pymupdf4llm and pymupdf-layout with onnxruntime. Its inventory adds
  15 public names to 1.28.0's, among them `Document.to_markdown`, `to_text`,
  `to_json`, and `to_chunks` forwarding to pymupdf4llm, `Pixmap.flip_rotate`,
  `Page.repair_flat_rbgs`, and `calc_matrix`, and drops
  `Annot.update_timing_test`. The manifest now lists 962 entries at 18.6%
  coverage.
