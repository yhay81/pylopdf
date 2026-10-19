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

pylopdf reaches **parity** when every pymupdf 1.28 public API that applies to
PDF documents is supported, supported with a documented deviation, or listed
as a reasoned non-goal with an ecosystem answer. It **exceeds** pymupdf where
pymupdf structurally cannot follow:

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
maps each pymupdf API to its pylopdf status, and CI keeps that map honest.

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

## Principles

1. **Compatible names, honest semantics.** Adopt pymupdf method names,
   argument names, constants, and data shapes by default so migration is
   mostly mechanical. Deviate only for a documented reason: zero-based pages
   outside the TOC, bounded defaults, typed errors, stale-view detection,
   immutability, or a safety guarantee pymupdf does not make. pylopdf ships no
   `fitz` alias and promises no byte-identical output.
2. **One-way data flow.** lopdf's `Document` is the sole editable source of
   truth; hayro is an immutable view for rendering and extraction. An
   unedited, unencrypted document may use its original input bytes only when
   every reference reachable from its trailer resolves to the same object in
   lopdf's graph and hayro reads the same pages as the page-tree walker. Every
   edit invalidates that fast path, so rendered output always reflects the
   edited and saved state. krilla returns bytes that are imported into lopdf.
   Engines never share mutable state.
3. **Clear engine ownership.** lopdf edits, hayro renders and extracts, krilla
   with HarfRust generates text and appearances, and pylopdf's content engine
   ([F8](#f8--content-stream-engine)) interprets and rewrites content streams
   inside the lopdf graph.
4. **Bounded by default.** Every caller input and decoded amplification has a
   documented cap. Python checks it before PyO3 copies and Rust checks it
   again. Trusted workloads opt out explicitly with `None`. Refusal raises
   `LimitError` with a stable code and returns no partial result.
5. **Prepare, then commit.** A mutation plans all fallible work first,
   invalidates caches immediately before its first change, and leaves the
   document unchanged on refusal. Structural changes bump the page generation,
   and stale views raise.
6. **Lightweight handles.** `Page`, `Annot`, `Widget`, `Link`, and outline
   items are views of a document, a generation, and an object ID. Setters
   apply immediately and atomically, regenerating appearances in the same
   operation, so no hidden dirty state can be lost by a forgotten `update()`.
   pymupdf's `update()` remains available as an explicit regeneration.
7. **Small core, optional data.** The core abi3 wheel stays at or below
   9.0 MiB on every platform; the thin-LTO wheels built from the 0.14.0
   sources are 6.4–7.4 MiB. Fonts, OCR models, and layout models ship as
   independently versioned data packages. Each pull request reports its wheel
   size delta, and growth above 0.25 MiB needs a written justification.
8. **Integrate where an ecosystem leader exists.** typst for typesetting and
   new-document PDF/A, pyHanko for cryptographic signing, and veraPDF for
   PDF/A validation. Integration recipes are covered by tests.
9. **Evidence over claims.** Every feature lands with real-document or
   adversarial regressions, save/reopen and render checks, published
   benchmarks for performance-relevant paths, and upstream reports for engine
   limitations.

## Definition of done

Every feature pull request satisfies all of the following before merge.

- **API.** pymupdf-aligned names and arguments where a counterpart exists;
  complete type hints and docstrings; an updated `pylopdf_core.pyi`;
  `api/public-api.json` refreshed after reviewing runtime, typing,
  documentation, and SemVer impact; an updated parity manifest entry.
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
milestone is done when its acceptance criteria pass on main.

### Waves

Waves define order, not dates. A release ships when one or more milestones are
complete, and version numbers are assigned at release time.

| Wave | Work that runs in parallel | Gate for the next wave |
| --- | --- | --- |
| 0 — in flight | hayro 0.8 migration; AcroForm registration on `insert_pdf`; XObject reuse for repeated insertion (#231); save-time merging of identical objects; CPython 3.15 and 3.15t (#230); the F3 parity manifest, which touches only `api/`, `tools/`, and `tests/` | Merged to main |
| 1 — foundations | F1 alone during a short freeze of `document.rs` and `__init__.py`; then F2 and F4–F8, one owner each | F1 merged with an unchanged API snapshot and corpus |
| 2 — breadth | M1 (A, B), M2 (C), M5 (E), F8 continued (F) | Milestone acceptance |
| 3 — interaction | M3 (D), then M4 (F) | Milestone acceptance |
| 4 — depth | M6 (A, H), M7 (G) | Milestone acceptance |
| 5 — rich text | M8 (C) | Milestone acceptance |
| 6 — freeze | [v1.0 readiness](#v10--api-freeze) | Parity manifest complete |

## Foundations (wave 1)

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

`api/pymupdf-parity.json` holds one entry per pymupdf 1.28 public API: module
functions, constants, and the members of `Document`, `Page`, `Annot`,
`Widget`, `Link`, `Outline`, `Pixmap`, `TextPage`, `Shape`, `TextWriter`,
`Font`, `Point`, `Rect`, `IRect`, `Quad`, `Matrix`, `Story`,
`DocumentWriter`, `Archive`, and `DisplayList`. Each entry records `status`
(`supported`, `deviation`, `planned`, `integration`, or `non-goal`), the
pylopdf equivalent, the target milestone, and a note. The 2026-10-19 counts of
public methods are Document 141, Page 115, Annot 53, Pixmap 40, Rect 22,
Font 20, Shape 17, TextPage 16, Story 16, Link 11, Outline 11, Matrix 7,
Quad 7, Widget 5 plus 30 public attributes, TextWriter 5, Archive 4,
DisplayList 4, and DocumentWriter 3.

- `tools/check_parity.py` verifies that every `supported` or `deviation`
  entry names a symbol present in `api/public-api.json`, that every `planned`
  entry has a milestone, and reports coverage per class.
- The migration guide's parity tables are generated from the manifest in all
  four locales, with localized headings and untranslated API names. The
  current guide still lists `rawdict` as unsupported although 0.14.0 added it;
  generation removes that class of drift.
- Record API names and statuses only. Never copy pymupdf code or documentation
  text, which is AGPL-licensed.

### F4 — Geometry types

Add pymupdf-compatible `Point`, `Rect`, `IRect`, `Quad`, and `Matrix` in
top-left display space: `Rect.intersect`, `include_point`, `include_rect`,
`contains`, `intersects`, `is_empty`, `is_infinite`, `is_valid`, `normalize`,
`round`, `transform`, `morph`, `quad`, `get_area`, and the corner properties;
`Matrix.prerotate`, `prescale`, `preshear`, `pretranslate`, `concat`,
`invert`, `is_rectilinear`, and multiplication; `Quad.rect`, `transform`,
`morph`, `is_rectangular`, `is_convex`, and `is_empty`. Every API accepts
these types and plain tuples interchangeably.

Decision required: pymupdf geometry objects mutate in place
(`r.include_point(p)`), while pylopdf's `Rect` and `Point` are immutable
NamedTuples. The recommendation is to stay immutable, because the values are
hashable, thread-safe, and already returned everywhere, and to make every
pymupdf mutator return the new value. The migration guide then flags discarded
results as the main porting hazard.

### F5 — Low-level object API

Expose the lopdf graph through pymupdf's xref vocabulary:
`Document.xref_length`, `xref_object`, `xref_get_key`, `xref_get_keys`,
`xref_set_key`, `xref_is_stream`, `xref_is_font`, `xref_is_image`,
`xref_is_xobject`, `xref_stream`, `xref_stream_raw`, `update_stream`,
`update_object`, `get_new_xref`, `xref_copy`, `pdf_catalog`, `pdf_trailer`,
`page_xref`, `xref_xml_metadata`, and `Page.xref`.

- Values use PDF object syntax strings, like pymupdf. lopdf 0.45 keeps its
  object parser and writer private, so pylopdf implements a bounded
  direct-object parser and serializer, or upstreams a public API. Decoded
  streams use the existing bounded multi-layer decoder.
- Every write invalidates the hayro snapshot and derived caches. Writes to the
  trailer, Catalog, page-tree nodes, or pages also bump the page generation.
- A pymupdf xref is an object number; pylopdf maps it to lopdf's
  `(number, generation)` pair from the object table.
- The API is covered by SemVer but documented as raw access whose semantic
  correctness is the caller's responsibility.

### F6 — Resource reuse and duplicate collection

- In review as #231: reuse one XObject for repeated `insert_image` input and
  one imported Form for repeated `show_pdf_page` placements of an unchanged
  source page. Five placements of one `bill-hr815.pdf` page then write one
  Form and 5 font files (39.7 KB instead of 192 KB).
- In progress: save-time merging of identical objects. The target API is
  pymupdf's `garbage` levels for `save` and `tobytes`: 0 keeps every
  object; 1 removes unreferenced objects, as `True` does today; 2 also compacts
  numbering; 3 also merges identical non-stream objects; 4 also merges
  identical streams. `True` and `False` keep their meaning because `bool` is a
  subclass of `int`. Merging hashes serialized objects, confirms equality
  before retargeting references, and stays bounded by the object count.

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

## Milestones

### M1 — Extraction and rendering depth

Workstreams A and B. Depends on hayro 0.8, F1, and F4.

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
- Inventory: `get_image_info(hashes=, xrefs=)`, `get_image_rects`,
  `get_image_bbox`, `extract_image(xref)`, `get_fonts(full=)`,
  `get_xobjects`, `Document.get_page_images`, `get_page_fonts`,
  `get_page_xobjects`, `extract_font(xref)`, and `get_char_widths`. hayro
  already reports image object IDs through `RasterImage::stream().obj_id()`,
  which `compress_images` uses today.
- Traces: `get_texttrace`, `get_bboxlog(layers=)`, `get_drawings(extended=True)`
  with the clip and group hierarchy, `get_cdrawings`, and `cluster_drawings`.

Rendering:

- `get_pixmap(matrix=, dpi=, colorspace=, clip=, alpha=, annots=)` and the
  same options on `render_page` and `render_pages`. Arbitrary affine matrices
  use hayro 0.8 `render_into`, and `annots=` sets
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
  upstream hayro-svg option or a pylopdf SVG device and remains a documented
  deviation until then.

Decision required: pymupdf's `Page.get_images()` returns xref tuples, while
pylopdf's returns decoded placements. The recommendation is to add
`get_image_data()` with today's behavior, deprecate the old return shape for
one minor release, and then return pymupdf's tuples.

Acceptance: span colors match pymupdf exactly on the corpus; ascender-based
boxes meet a published IoU threshold; quads cover rotated-text search; a
diagonal matrix renders pixel-identically to the matching `scale`; and every
default output is unchanged.

### M2 — Drawing and generation

Workstream C. Depends on F1, F4, and F7.

- `Page.new_shape()` returns a `Shape` with `draw_line`, `draw_rect(radius=)`,
  `draw_circle`, `draw_oval`, `draw_polyline`, `draw_bezier`, `draw_curve`,
  `draw_quad`, `draw_sector`, `draw_squiggle`, `draw_zigzag`,
  `finish(width, color, fill, lineCap, lineJoin, dashes, even_odd, morph, closePath, fill_opacity, stroke_opacity, oc)`,
  `insert_text`, `insert_textbox`, and `commit(overlay=)`, which writes one
  content stream per commit. `Page.draw_*` methods are single-shape shortcuts.
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
  `delete_responses`, `update()`, `xref`, and `next`. `Page` gains
  `annots(types=)`, `load_annot`, `first_annot`, `annot_names`, `annot_xrefs`,
  and `delete_annot`.
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
  `delete_widget`, `load_widget`, `Widget.update()`, `reset()`,
  `Document.is_form_pdf`, `need_appearances()`, and `get_sigflags()`.
- `Document.bake(annots=, widgets=)` flattens the `/AS`-selected `/AP /N`
  into page content with the same BBox-to-Rect mapping hayro uses.
- Signature fields: create empty signature widgets and report signed state;
  an interop test signs a pylopdf-created field with pyHanko.
- JavaScript actions (widget `/AA`, document-level `/JavaScript`) can be
  authored and removed. pylopdf never executes them.
- Migration: `Page.annots()` and `Document.get_form_fields()` currently return
  dictionaries. The handles accept the legacy keys through mapping access for
  one deprecation window.

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

Workstream E. Depends on F5.

- XMP: `get_xml_metadata`, `set_xml_metadata`, and `del_xml_metadata`, with
  optional synchronization from `set_metadata`.
- TOC: `get_toc(simple=False)` with kind, target point, zoom, color, bold,
  italic, collapse state, and xref; `set_toc(toc, collapse=)` with
  fourth-element dictionaries; `set_toc_item`, `del_toc_item`, and
  `get_outline_xrefs`; `Document.outline` traversal with `Outline` views.
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
- Attachments: `embfile_info`, `embfile_upd`, and `embfile_count`, shared with
  M3 file attachment annotations.

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
  `use_objstms=` (an alias of `object_streams=`), `compression_effort=`,
  `incremental=`, and `encryption=` with pymupdf's constants, together with
  `ez_save()`, `saveIncr()`, and `can_save_incrementally()`.
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
- Encryption: verify reading of RC4 40/128-bit and AES-128/256, and write
  AES-128 and AES-256. `PDF_ENCRYPT_KEEP` preserves a supported input handler.
  Writing RC4 is refused as a documented deviation.
- Journal: `journal_enable`, `journal_start_op`, `journal_stop_op`,
  `journal_undo`, `journal_redo`, `journal_can_do`, `journal_position`,
  `journal_op_name`, `journal_save`, and `journal_load`, built from
  object-level snapshots per operation under a memory budget.
- `rewrite_images` parity: lossless recompression, bitonal and grayscale
  targets, and `set_to_gray`, complementing `compress_images`.

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

- The parity manifest has no `planned` entries left, and every deviation is
  documented.
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
  anti-aliasing if vello_cpu exposes it), standalone `Colorspace` objects,
  `Pixmap.warp`, and `Pixmap.pdfocr_*`. Images-to-PDF plus native OCR covers
  the last case.
- **Drop-in aliasing**: no `fitz` module and no byte-identical output.
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

## Decisions in this rewrite

- **D1**: the scope expands to every pymupdf API that applies to PDF
  documents, except the [non-goals](#non-goals).
- **D2**: compatible names and data shapes become the default, replacing
  "pymupdf-style, not pymupdf-compatible".
- **D3**: geometry types are adopted (F4); immutability is the recommended
  deviation.
- **D4**: lightweight `Annot`, `Widget`, `Link`, and outline handles replace
  the dictionary-only policy once mutation is in scope (M3).
- **D5**: the low-level object API becomes public (F5).
- **D6**: incremental save moves from the watchlist into M7.
- **D7**: opt-in reconstruction of damaged xref streams and truncated files is
  scheduled (M7) under the single-source-of-truth constraint.
- **D8**: redaction is in scope with a verification contract (M4).
- **D9**: the core wheel budget is 9.0 MiB.
- **D10**: changelog fragments (F2) and the parity manifest (F3) become part
  of the process.

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
  registered fields and 128 orphaned widgets.
