# pylopdf roadmap

This is the canonical medium-term plan. It was rewritten on 2026-10-22 around
one direction: **the best PDF library for Python, built as the Python face of
the Rust PDF engines**, with lopdf for objects and persistence, hayro for
interpretation and rendering, and krilla for writing. On 2026-10-23 its goal
was raised from pymupdf's capabilities to the PDF specification itself, after
an [assessment](#assessment-2026-10-23) found that completing the plan would
not have shown that pylopdf controls PDF
([D8](#decisions)).
[api/DESIGN.md](api/DESIGN.md) holds the API design that every milestone
implements, and [AGENTS.md](AGENTS.md) the day-to-day development context.

## Goal

pylopdf gives a program complete control of PDF, and keeps every document it
changes consistent. It is also essential, engine-faithful, trustworthy, and
available everywhere Python runs.

- **Complete control.** Every feature of PDF as ISO 32000-2:2020 defines it,
  with its errata and extensions and the earlier versions that it supersedes,
  is under a program's control in five dimensions: it is read without loss,
  interpreted for what it means, created, edited, and preserved when other
  parts of the document change. The [specification map](#specification-map)
  records each feature in each dimension, with the tests that prove it, and
  any completeness claim must pass the [claim criteria](#completeness-claims)
  and name its scope and exclusions. A version number is not such a claim.
  pymupdf's capabilities are the floor: one that pymupdf has and pylopdf
  lacks is a gap, never a scope decision, and the [capability map](#capability-map)
  measures it. Covering pymupdf is not completeness.
- **Consistent.** Rewriting objects is not control. An edit keeps every
  structure that depends on what it changes valid (appearances, font widths
  and encodings, the structure tree, name and number trees, outline counts,
  cross-references, and metadata), or refuses. What it cannot keep valid,
  such as a signature over changed bytes or a PDF/A or PDF/UA claim, it
  reports before the document is saved. Control covers the document, not a
  viewer's behavior: pylopdf reads, creates, edits, preserves, and removes
  scripts, media, and 3D artwork, but does not run, play, or display them
  ([D9](#decisions)).
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
  resource policy, the consistency of edits and the checks that prove it, and
  document understanding (layout, tables, Markdown, and OCR).
- **Trustworthy.** Hostile input meets typed, bounded refusal. Results can be
  verified: redaction proves removal, extraction carries its provenance,
  independent validators (veraPDF, the Arlington PDF Model, qpdf, and
  pyHanko) check what pylopdf writes, and benchmarks publish losses with wins.
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

## Assessment: 2026-10-23

Measured against complete control of PDF rather than against pymupdf, main
(`9c73d92`) falls short in ways that the plan of 2026-10-22 did not measure:

- **Round trip.** `Canvas.draw(item)` paints `Path` items only. Text runs,
  images, pattern paints, and soft masks that `Page.content()` reads cannot
  be painted again ([Canvas](#canvas), [Writing](#writing)).
- **Editing existing text.** `Page.replace_text` follows lopdf's simple-font
  model: it does not handle CID-keyed (and so most CJK) or Type 3 fonts, and
  it does not recompute widths, so a replacement of another length moves the
  text after it ([Rewriting](#rewriting)).
- **What the content model exposes.** A paint's color space and components,
  marked-content properties such as optional-content membership, the font
  dictionary behind a `FontRef`, and the object of an image mask are not
  reported, and no item names the operators or resources that painted it
  ([Content model](#content-model), [Reading](#reading)).
- **Saving.** There is no incremental save, so a signed document cannot be
  edited without invalidating its signatures ([Persistence](#persistence)).
- **Preservation.** lopdf stores real numbers as `f32`, so opening and saving
  a document rewrites `35.6812345` as `35.681236` and `123456.789` as
  `123456.79`, and no test checks that the objects an edit does not touch
  survive it ([Engine work](#engine-work), [Verification](#verification)).
- **Raw access without meaning.** The object model edits any dictionary,
  array, or stream, but nothing interprets what such an edit means or checks
  that the structures depending on it stay consistent
  ([Conformance](#conformance)).
- **Conformance.** `pylopdf.create()` writes PDF/A and PDF/UA that krilla
  validates, but editing an existing PDF/A or PDF/UA document neither keeps
  nor reports its claim ([Conformance](#conformance)).
- **Measurement.** The capability map lists 962 names of pymupdf 2.0: 455
  available, 29 covered by ecosystem tools, 315 planned, and 163 outside
  scope, so 60.6% of the names in scope are covered. Those are pymupdf's
  names, not the specification's features, and nothing related pylopdf to
  ISO 32000-2, so finishing every milestone would not have shown that
  pylopdf covers PDF ([Specification map](#specification-map)).

## Principles

1. **Features and capabilities, not names.** Completeness is measured per
   feature of the specification, in each of its five dimensions, in the
   specification map, and per pymupdf capability in the capability map. A
   feature that a program can reach only by editing raw objects is read, not
   controlled. pylopdf's names follow [api/DESIGN.md](api/DESIGN.md).
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
8. **Consistent edits.** Each editing operation documents the structures it
   maintains, and the consistency harness of [Verification](#verification)
   checks them after every editing test. An operation that cannot keep a
   dependent structure valid refuses, and one that invalidates what it cannot
   repair, such as a signature or a conformance claim, reports it.
9. **Lossless by default.** What no edit touched, a save writes back exactly:
   every number, string, and name with its value, every stream with its
   decoded data, and, in an incremental save, the original bytes. Untouched
   real numbers retain their source decimal tokens or an exact decimal
   representation; a conversion to `f32`, `f64`, or Python `float` cannot
   establish equality. Approximate values used for rendering or arithmetic
   stay separate from the values that persistence keeps. The
   defaults only recompress streams without changing their data and drop
   objects that nothing reaches ([D6](#decisions)); renumbering,
   deduplication, and any other change of values or numbers happen only when
   a save option asks for them.
10. **Provenance.** Every derived result (a word, a table cell, a Markdown
    chunk, an OCR word, a content item) carries its page and its
    display-space geometry, and, where the PDF has one, the object, operators,
    or structure element it came from.
11. **Evidence over claims.** Features land with real-document and
    adversarial regressions, save-and-reopen and rendering checks, independent
    validators where one exists, and published benchmarks for
    performance-relevant paths, wins and losses together. A status in the
    specification map is `done` only when a named test proves it.
12. **Small core, optional data.** The core abi3 wheel stays at or below
    9.0 MiB on every platform. Fonts, OCR models, and layout models ship as
    separately versioned data packages. A pull request that grows the wheel by
    more than 0.25 MiB justifies it in writing.
13. **Break before 1.0, once per design change.** Redesigns land without
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

## Specification map

`api/spec-map.json`, which [Verification](#verification) builds, records
every feature of ISO 32000-2:2020 (PDF 2.0) with its resolved errata, of the
PDF 2.0 extensions ISO/TS 32001 to 32005 (hash algorithms, signature
algorithms, AES-GCM, integrity protection, and structure namespaces), and of
the profiles that pylopdf writes or must keep: PDF/A (ISO 19005) and PDF/UA
(ISO 14289), and, read and preserved, PDF/X (ISO 15930) and PDF/VT
(ISO 16612). A feature is a unit that a program controls on its own, such as
a filter, a family of color spaces, a shading type, an annotation type, an
action type, a field type, an encryption handler, or the structure tree. Each
entry names its clause, the PDF version that introduced it and whether PDF 2.0
deprecates it, the objects of the
[Arlington PDF Model](https://github.com/pdf-association/arlington-pdf-model)
that express it, its workstream and engines, and a status in each of five
dimensions:

| Dimension | Done when |
| --- | --- |
| Read | the objects that express the feature are reachable through the object model without loss: every value as stored, streams raw and decoded |
| Interpret | pylopdf exposes what the feature means as typed values, and rendering and extraction honor it as the specification says |
| Create | a program adds the feature to new documents and to existing ones |
| Edit | a program changes or removes the feature, and every structure that depends on it stays consistent |
| Preserve | edits elsewhere and full and incremental saves keep the feature exactly |

A status is `done` with the tests that prove it, `partial` with what is
missing, `planned` with its milestone, `engine` with the upstream change it
waits for, `n/a` only when the dimension has no meaning for the feature,
or `non-goal` for an intentional exclusion. Each `n/a` needs a
clause-based explanation; deprecation, missing implementation, and a policy
against creating a feature do not make its creation inapplicable. Each
`non-goal` names the affected feature and dimension, the decision and its
reason, and the public exclusion. A `partial` entry keeps its missing
behavior, follow-up issue, and milestone even when accepted for a release;
it remains unfinished work and earns no completion credit.

Running, playing, or displaying what a viewer acts on (scripts, media, and
3D artwork) and device-dependent controls such as halftones are `non-goal`
for interpretation only. Their data structures remain in scope; creation
of deprecated features is a separate `non-goal` under [Non-goals](#non-goals),
never `n/a`. Scope changes require a decision and a published change to the
exclusions; changing a status cannot silently narrow a claim.

Evidence comes from pylopdf's tests, from the preservation and consistency
harnesses of [Verification](#verification), and from independent tools in
the interop group: veraPDF for PDF/A and PDF/UA, the Arlington model's
checks for objects, qpdf for syntax, and pyHanko for signatures, with
pymupdf, pdf.js, and pypdfium2 as development-only oracles where a comparison
means something. `tools/check_spec_map.py` checks the map in CI and reports
coverage per clause and dimension. The map holds clause numbers, pylopdf's
own short descriptions, and the names of Arlington objects, which the
Apache-2.0 model allows; never copy text from ISO 32000-2, whose copyright ISO
holds although the PDF Association distributes it at no cost.

### Completeness claims

A release and a completeness claim have separate gates. The [v1.0](#v10)
criteria allow reviewed limitations when the API is ready to stabilize;
those limitations do not become complete because the release ships.

A claim of complete control within a declared scope requires all of:

- A published scope naming the PDF versions, the dated errata baseline,
  extensions and profile parts and conformance levels, every excluded
  feature and dimension, and any required ecosystem tools.
- Every applicable dimension inside that scope is `done`, with named tests
  that pass for the claimed release. `partial`, `planned`, and `engine`
  entries block the claim; `non-goal` entries are explicit exclusions, never
  completed features. Every `n/a` has a reviewed clause-based explanation.
- The preservation and consistency harnesses, round-trip checks, and the
  independent validators required for that scope pass on that release.
- Coverage reports show the full inventory and counts of all statuses per
  clause and dimension. Only `done` earns completion credit; a percentage
  that excludes `non-goal` entries is labeled as coverage of the declared
  scope and is shown beside the exclusions and the full inventory.

An unqualified claim to support every PDF specification is not permitted
while any applicable feature or dimension is excluded or incomplete, or a
required capability depends on an external tool. The current non-goals
therefore require a qualified claim even after the declared scope is done.
The specification map and its checker are planned in [Verification](#verification);
this document does not claim that they or these gates are implemented today.

## Capability map

`api/pymupdf-parity.json` measures pylopdf against pymupdf: the floor of
completeness and the source of the migration tools. It records, for every
public name of pymupdf 2.0, how pylopdf covers the capability behind it:

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
- **Specification.** Once the [specification map](#specification-map)
  exists, the entries of the features it touches are updated in each
  dimension with the tests that prove them. An editing operation documents
  the structures it maintains and what it reports, and the preservation and
  consistency harnesses cover it.
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
- **Tracking.** It closes or references an issue under
  [Tracking](#tracking), and checks the boxes it completes.

## Workstreams

| Workstream | Scope | Engine area |
| --- | --- | --- |
| Objects | loading, recovery, the object model, saving, incremental save, encryption, garbage collection, the specification map's tooling and harnesses | lopdf |
| Structure | the page tree, outline, destinations, page labels, attachments, metadata and XMP, optional content, structure-tree maintenance, conformance | lopdf |
| Content | the content model, interpretation guards, image and drawing inventories | hayro-interpret, hayro-syntax |
| Understanding | text and layout, search, tables, Markdown, tagged structure, OCR, layout models | RTen |
| Rendering | rasterization, SVG, `Pixmap`, render caches, parallel batches | hayro, vello_cpu, hayro-svg |
| Writing | the canvas, fonts and text layout, images, page placement, new documents, conversion | krilla, HarfRust, parley |
| Interaction | annotations, links, form fields and widgets, flattening | lopdf, krilla |
| Rewriting | the content engine, redaction, sanitization, text replacement, image rewriting | lopdf, hayro for verification |

Each workstream also owns the upstream work in its engine area and the
specification-map entries of its features, and AGENTS.md records its modules.
Concurrent pull requests stay inside one workstream; a change to another
workstream's module is its own small pull request. Shared files
(`src/pylopdf/__init__.py`, `rust/src/lib.rs`, `rust/src/document/mod.rs`,
`api/public-api.json`, and the capability map) take append-only registrations
and are regenerated after each rebase. Each pull request names its milestone.

## Phases

Phases define order, not dates. A release ships when its milestones meet
their acceptance criteria.

| Phase | Work that runs in parallel | Gate |
| --- | --- | --- |
| 1 — foundations (current) | [Content model](#content-model), [Canvas](#canvas), [API reset](#api-reset), and the first [engine work](#engine-work) | The three milestones are accepted; the next release ships them together |
| 2 — capabilities | [Verification](#verification), [Reading](#reading), [Rendering](#rendering), [Writing](#writing), [Interaction](#interaction), [Rewriting](#rewriting), [Structure](#structure), [Persistence](#persistence), [Conformance](#conformance), and [Understanding](#understanding) | Each milestone's acceptance |
| 3 — specification | [Specification completion](#specification-completion) | The specification map has no `planned` or `engine` entry, and remaining limitations are published; this is not a completeness claim |
| 4 — 1.0 | [v1.0 criteria](#v10) | Every criterion |

Verification starts at once, alongside phase 1, because every other
milestone records its features in the specification map that it builds and
is checked by its harnesses.

The pymupdf-aligned renames on main since 0.15.0 are not released on their
own. The next release carries the API reset, so users of pylopdf 0.15 and of
pymupdf migrate once, with one codemod.

## Tracking

This document says what pylopdf does next and why; GitHub issues record how
far each part has come. The pinned root issue,
[Roadmap to 1.0](https://github.com/yhay81/pylopdf/issues/325), has these
sub-issues, and the roadmap is complete when all of them are closed:

- One tracker per milestone below, titled `Milestone: <name>`, with the
  milestone's bullets and acceptance criteria as checklists and the
  capability-map entries of the milestone as a generated list.
  `tools/roadmap_issues.py sync` rewrites that list from
  `api/pymupdf-parity.json`, so a tracker can close only when its milestone
  has no planned capability left. Once the specification map exists, the
  same tool adds a second list of the milestone's features from it.
- [Engine work](https://github.com/yhay81/pylopdf/issues/324), with one
  sub-issue per engine: the releases pylopdf adopts, the pull requests it
  sends upstream, scope questions for the maintainer, what pylopdf gives
  back, and the workarounds the work removes.
- The 1.0 release gates and the continuous tracks with the watchlist.

Closing the release roadmap establishes release readiness, not complete
coverage; a completeness claim still requires its [own gate](#completeness-claims).

Work on an item starts as a sub-issue of its tracker. GitHub milestones
`Phase 1`, `Phase 2`, `Phase 3`, and `1.0` group the trackers; labels name the
workstream (`workstream:*`), the engine (`engine:*`), and the kind of engine
work (`upstream`, `adoption`, `scope-question`). A change to a milestone here
changes its tracker too.

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
  `clip`, `transform`, `opacity`, `blend`, and `mask` (all done), then
  `layer`, and `tag` on pages of open documents, which need the
  optional-content groups and the structure-tree maintenance of
  [Structure](#structure); and `draw(item)`, which paints a content item
  again (done for paths and images). `textbox` places lines as `text` does,
  `Font.text_height` measures the height it needs before drawing, `rotate`
  turns text clockwise in right angles, and `page` takes a `clip` and a
  `rotate` (all done).
- One commit places one Form XObject whose content pylopdf composes in
  display space (done). Paths, clips, transforms, and blend modes are
  operators; an opacity scope is a transparency group Form; text in an
  OpenType face is a krilla page, one per run of text items, imported as a
  Form; Standard 14 text is pylopdf's own text operators; and images and
  placed pages are Image XObjects and imported Forms drawn through their
  placement matrices. Scopes must
  enclose images and pages that already live in the target document, which a
  krilla page cannot reference, so the scopes are pylopdf's operators.
- `draw(item)` for images (done): an `Image` item names the document that
  `Page.content()` read, a canvas on a page of that document draws the image
  object again, and a canvas of another document copies it, with the objects
  it reaches but pages, when `draw` is called. Text runs need the font
  identity listed under [Content model](#content-model): a run must name the
  font program to embed again.
- New documents (done): `pylopdf.create()` returns a builder whose pages
  draw through the same canvas and which krilla writes as a whole, with
  metadata, an outline from `bookmark`, a structure tree from `tag` scopes,
  and the validators of PDF/A-1 to PDF/A-4 and PDF/UA-1. A recording is a
  list of structured operations, which `Page.draw()` composes into lopdf
  content and `create()` replays onto a krilla surface. Table header scopes,
  list numbering, and links to URIs and pages are done. Still to come: other
  annotations and placed pages.
- Upstream in krilla: the Standard 14 fonts without embedding, which krilla
  cannot draw today, so pylopdf drops its own operator writer for them;
  optional content; annotation and widget types beyond links for new
  documents; embedded pages in validated output; subset fonts named after the
  variable-font instance they embed, so pylopdf no longer renames them after
  import; and emitting a page as a Form XObject that pylopdf adds to lopdf's
  graph directly, so `generated_pdf.rs` and the import parse go away.
- In the [API reset](#api-reset), the canvas replaces `Shape` and
  `Page.draw_*`, `insert_text` and `insert_textbox`, and `insert_image`
  and `show_pdf_page` (done).

Acceptance: drawing every item of `page.content()` onto a blank page of the
same size renders, for every page of the bundled corpus, within a published
pixel tolerance of the original page, and a documented list names what does
not round-trip yet. Every existing drawing test passes through the canvas.
Paths and images (done): `tests/test_canvas_round_trip.py` redraws them for
every page of the bundled corpus within 16 levels per channel at 72 dpi
outside the items that cannot be drawn yet, which the canvas section of the
API reference lists: text runs, items under a soft mask, pattern paints,
inline images, and image masks.

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
- Done: vector drawing. `page.draw()` and its `Canvas` replace
  `Page.draw_*`, `Page.new_shape()`, and `Shape`; the codemod rewrites
  `draw_line`, `draw_rect`, `draw_circle`, `draw_oval`, `draw_polyline`,
  `draw_bezier`, `draw_squiggle`, and `draw_zigzag` statements into canvas
  blocks with `Stroke` and `Color` styles, and marks the rest.
- Done: text insertion. `Canvas.text` and `Canvas.textbox` replace
  `Page.insert_text`, `Page.insert_textbox`, `TextAlign`, and the
  `TEXT_ALIGN_*` constants; the codemod rewrites both calls, and assignments
  of a textbox's spare height, into canvas blocks, joins consecutive blocks
  on one page into one, and marks the options a canvas does not take.
- Done: image and page placement. `Canvas.image` and `Canvas.page`
  replace `Page.insert_image` and `Page.show_pdf_page`; the codemod rewrites
  both calls, and assignments of the image xref, into canvas blocks, negates
  pymupdf's anticlockwise image rotation, and marks masks, layers, clipping,
  and page rotation.
- Done: form fields. `doc.form` maps full field names to `Field` handles
  with `name`, `type` (`FieldType`), `value`, assigned to fill the field,
  `flags` (`FieldFlags`), and `choices` (`FieldChoice` records), and
  `doc.form.font` chooses the face that fills draw with; `get_form_fields()`,
  `set_form_field()`, `FormFieldInfo`, and `FormFieldType` are gone. Widget
  handles, field creation, and `reset()` come with the Interaction milestone.
- Done: the remaining constants and records. Codes and flags are enum
  members only (`AnnotationType`, `AnnotationFlags`, and `Permissions` in
  place of `PDF_ANNOT_*`, `PDF_ANNOT_IS_*`, and `PDF_PERM_*`), geometry
  constants are class attributes (`Matrix.IDENTITY`, `Rect.EMPTY`,
  `Rect.INFINITE`, the same on `IRect` and `Quad`, and `Matrix.planish()`),
  and `DocumentComplexity` and `ImageCompressionResult` are frozen
  dataclasses; language codes stay `Literal` options. The codemod rewrites
  each of them.

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
| hayro | Knockout transparency groups and overprint simulation, which hayro 0.8 does not implement, and the other rendering features of ISO 32000-2 clauses 8, 10, and 11 that the specification map finds missing | the Interpret gaps of graphics and transparency in rendering |
| hayro-svg | Text kept as text, and a transform | `svg()` takes neither `text_as_path` nor `matrix` |
| lopdf | A public object parser and serializer | pylopdf's own value conversion |
| lopdf | A requested rebuild of the cross-reference table; rebuilding when the catalog or page tree is unreadable, or when a corrected offset misses later objects, and for truncated files | the forced rebuild in `recovery.rs`, and inputs rejected today |
| lopdf | Linear graph traversal in pruning, renumbering, and deletion | `prune.rs` and `renumber.rs` |
| hayro | Object numbers read as u32 rather than i32 | with lopdf's panicking `add_object`, the renumbering above 8,388,607 in `unallocated.rs` |
| lopdf | Parsing small documents on the calling thread instead of the rayon pool | `generated_pdf.rs`, if krilla's Form output does not remove it first |
| lopdf | A bounded, iterative page-tree walk with inherited attributes | pylopdf's own walker |
| lopdf | Release of the rebuilt-trailer cleanup merged as J-F-Liu/lopdf#609 | the `/Prev` and `/XRefStm` removal after reconstruction |
| lopdf | Linearization facts and the number of revisions | pymupdf's `is_fast_webaccess` and `version_count` |
| lopdf | Untouched real numbers kept as their source decimal tokens or in an exact decimal representation and written without rounding; `f64` alone is insufficient, and approximate numeric views stay separate | the precision that every save loses today (`35.6812345` becomes `35.681236`), a Preserve gap of every feature with real-valued entries |
| lopdf | The revision and byte range of each object in an incrementally updated file | incremental saves that write only changed objects, earlier revisions as documents, and signed byte ranges checked before an edit |
| lopdf | Writing linearized and hybrid-reference files, the public-key security handler, AES-GCM (ISO/TS 32003), and integrity protection (ISO/TS 32004) | the Create entries of file structure and encryption in the specification map |
| krilla | The items listed under [Canvas](#canvas) | the Standard 14 writer, the font renaming, and the import parse |

Acceptance, per item: the engine change is released, pylopdf adopts it, the
workaround and its tests' special cases are removed, and the corpus results
and benchmarks stay unchanged or improve.

### Verification

All workstreams; the tooling belongs to Objects. It starts alongside phase 1,
because every other milestone records its features in the map it builds.

- The specification map: `api/spec-map.json` with the entries and statuses
  that [Specification map](#specification-map) describes, one entry for each
  feature-defining clause of ISO 32000-2:2020 and the extensions and
  profiles it names, every object of the Arlington PDF Model assigned to a
  feature, `tools/check_spec_map.py` in CI, and coverage per clause and
  dimension published in the documentation.
- Separate release and completeness checks: enforce the status evidence,
  exclusions, full-inventory counts, and [claim criteria](#completeness-claims).
  A release exception cannot satisfy a completeness check, and the checker
  rejects `n/a` used for deprecated creation or a policy exclusion.
- First statuses from evidence: each dimension of each feature is set from
  the tests that exist today. An applicable in-scope dimension without a
  test is `planned` with a milestone, never `done`; exclusions and `n/a`
  require their recorded reasons. Features that no milestone covers go to
  [Specification completion](#specification-completion).
- The preservation harness: every editing operation of the public API is
  applied to every corpus document and saved in full and, once
  [Persistence](#persistence) adds it, incrementally. Every object that the
  operation did not touch must read back equal, values exactly, and the
  rendering and extracted text of untouched pages must not change. Numeric
  comparisons use the source tokens or exact decimal values independently
  of the parser under test, never two already-rounded float readings. Cases
  include decimals beyond `f64` precision, small fractions, large
  coordinates, and unrelated edits as well as saves without edits.
- The consistency harness: after every editing test, the document passes the
  Arlington model's checks, qpdf reports no error, and, once
  [Conformance](#conformance) adds it, pylopdf's structural check finds
  nothing.
- Independent validators in the interop group, which holds pyHanko today:
  veraPDF, an Arlington checker, qpdf, and pikepdf, pinned and run in CI,
  with independent readers (pymupdf, pdf.js, and pypdfium2) as
  development-only oracles.
- The tracker lists: `tools/roadmap_issues.py` adds each milestone's
  specification-map features to its tracker, as it lists capability-map
  names today.

Acceptance: every feature-defining clause and every Arlington object maps to
an entry; no status is `done` without a named test; the checker distinguishes
release exceptions from complete coverage and rejects unsupported `n/a`
statuses; both harnesses run in CI over the bundled corpus, including exact
numeric comparisons; and the documentation publishes coverage and exclusions.

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
- Provenance of content: every item of `Page.content()` names the content
  stream or Form XObject that painted it, the operators that painted it, and
  the resources it used (font, XObject, graphics state, color space, pattern,
  shading, and marked-content properties), so a program goes from what it
  reads to the objects it edits. hayro's source callbacks and pylopdf's
  content engine, which follows lopdf's parse, supply them together.
- The paint as stored: a color's color space and components, and a
  pattern's or shading's object, next to the sRGB approximation.

Acceptance: span colors equal pymupdf's on the corpus; ascender-based boxes
meet a published IoU threshold; quads cover rotated text; and the operators
that every item of the corpus names repaint that item when executed alone.

### Rendering

Workstream Rendering.

- Arbitrary affine transforms through hayro's `render_into` (done); CMYK
  output (gray, RGB, and alpha are done); anti-aliasing on or off
  (LaurenzV/hayro#1611); resolution metadata in PNG and JPEG (done;
  annotations on or off, PNG, and JPEG were done before).
- `Pixmap` stays an immutable value with zero-copy NumPy interchange in both
  directions, plus decoding of PNG and JPEG files (all done; image objects
  with their masks through `ImageObject.to_pixmap`).
  Image processing (tinting, gamma, inversion, color statistics, warping,
  scaling) is a tested NumPy or Pillow recipe in the ecosystem guide rather
  than `Pixmap` methods, recorded in the capability map as `integration`.
- Closing MuPDF's lead in warm rasterization through upstream glyph and image
  caches and thread-safe caches. Measured on 2026-10-24, pylopdf leads cold
  and on image pages warm, and MuPDF leads warm on text pages (2–5×), where
  hayro rasterizes every glyph again: a covering crop-box clip
  (LaurenzV/hayro#1612, −9% warm) and a glyph cache (scope question
  LaurenzV/hayro#1613).
- Rendering of every feature that changes what a page shows, as the
  specification map lists them: knockout groups and overprint simulation
  first, upstream in hayro.

Acceptance: a diagonal transform renders pixel-identically to the equivalent
resolution; every recipe has a test; benchmarks are published; and the
specification map's Interpret entries for rendering have no `planned` entry.

### Writing

Workstream Writing. Depends on the canvas.

- Text: rotation, render modes (fill, stroke, invisible, clip), opacity, and
  outlines; per-glyph fallback across the installed font extras; styled runs
  (font, size, color, weight, style, and link) with bidirectional ordering and
  justification, with parley evaluated for the layout; and an HTML and CSS
  subset for text boxes.
- Fonts: metrics, glyph coverage, advances, bounding boxes, and style
  information for the Standard 14 fonts and OpenType faces (done:
  `Font.bbox`, `glyph_count`, `is_monospaced`, `chars()`, `char_widths()`,
  `char_bbox()`, and `program()`, and the glyph names of the Adobe Glyph List
  through `glyph_name()` and `glyph_text()`).
- Images: JPEG 2000 passthrough, masks and alpha, and replacing or removing an
  image object (done: soft masks from PNG and Pixmap alpha,
  `ImageObject.replace()`, and image objects drawn again with
  `canvas.image()`, on any canvas).
- Placement of pages with a clip and rotation (done), and optional-content
  layers on everything the canvas draws.
- Conversion of images (JPEG, PNG, and JPEG 2000 directly, other formats
  through Pillow) and CBZ archives to PDF, and of scans to searchable PDF with
  OCR.
- The complete round trip: `draw(item)` paints every item kind that
  `Page.content()` reads, so the documented list of what does not round-trip
  under [Canvas](#canvas) becomes empty. Beyond the text runs of Canvas,
  that means shading and tiling patterns, soft masks, inline images,
  and image masks, each drawn from the objects of the source document where
  the canvas cannot rebuild them.

Acceptance: text effects are verified by rendering and extraction; a diagonal
translucent watermark and mixed CJK and right-to-left runs are covered by
tests; validated PDF/A-2b and PDF/UA-1 output passes veraPDF in the interop
group; and redrawing every item of every corpus page onto a blank page
renders within the published tolerance with no exception listed.

### Interaction

Workstream Interaction. Depends on the canvas and the API reset.

- Annotations as handles with typed properties, created from typed
  specifications for every annotation type that pymupdf creates (text notes,
  free text with callouts, lines, squares, circles, polygons, polylines, ink,
  stamps, file attachments, carets, text markup, and redactions), edited and
  deleted, with appearances drawn through the canvas. Rendering synthesizes
  missing appearances without changing the document. The annotation and
  action types that pymupdf does not create belong to
  [Specification completion](#specification-completion).
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
- Text editing in every font, replacing `Page.replace_text`, which handles
  simple fonts only and keeps the old widths: text in simple, Type 3, and
  composite fonts with any CMap, in both writing modes, is re-encoded through
  the font's encoding or CMap; missing glyphs are added to an embedded subset
  or drawn in a declared fallback font; advances are recomputed, with
  compensating `TJ` adjustments that keep the following text in place; and
  `/ToUnicode`, `/ActualText`, and the structure tree stay consistent. An edit
  that the font cannot draw is refused.
- Item-level editing: removing, recoloring, transforming, or replacing a
  content item rewrites only the operators that painted it (the provenance of
  [Reading](#reading)), copying a shared Form XObject first. Redaction is the
  first user of this path.
- Operator-level content: a page's or a Form's content as an editable
  sequence of operators, operands, and inline images, written back without
  loss, for programs that need control below the content model.

Acceptance: an adversarial redaction corpus (kerned `TJ` arrays, shared
Forms, rotated pages, CJK CID fonts, Type 3 fonts, overlapping layers, inline
images, and masks) shows no leak, cross-checked by pymupdf's extraction of the
redacted areas; text edits in CID, Type 3, and vertical fonts extract as the
new text and render with the following text in place; and reading and
writing back the operators of every corpus content stream changes nothing.

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
- Structure-tree maintenance on existing documents: marked-content IDs,
  `/StructParents`, the parent tree, role maps, and attributes stay consistent
  when content is added, moved, or removed, so that the canvas can tag what it
  draws on an open document and page operations keep the tree valid.

Acceptance: changing layer visibility changes rendering and extraction; the
outline round-trips; imported pages keep working forms and links in pymupdf
and pylopdf; and every page operation on a tagged corpus leaves a structure
tree that the consistency harness accepts.

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
  keeping the document ID; the form of the cross-reference data (table,
  stream, or hybrid); the filters of each stream, kept as read or chosen; and
  linearized output.
- Lossless saving: every value that no edit touched is written back as it
  was read. Untouched decimal tokens are retained or represented exactly,
  including values beyond `f64` precision; approximate numeric views do not
  replace them. Exact numeric access and assignment must not require a
  round trip through Python `float`. lopdf's `f32` real numbers are the
  first gap ([Engine work](#engine-work)).
- Revisions: the revisions of an incrementally updated file as documents of
  their own, with the objects that each revision changed, so that a program
  can see what was signed and what changed after it.
- Encryption beyond the standard handler: the public-key handler, AES-GCM
  (ISO/TS 32003), and integrity protection (ISO/TS 32004), for reading and
  writing.

Acceptance: pyHanko signatures stay valid after incremental edits; pikepdf in
the interop group accepts every output; subsetting reduces size without
changing extracted text or rendering; and opening and saving every corpus
document without edits keeps every value of every object it reaches exactly,
which the preservation harness checks against source tokens or independent
exact decimals, including values beyond `f64` precision and unrelated edits.

### Conformance

Workstream Structure, with Writing and Interaction. Depends on the
structure-tree maintenance of [Structure](#structure).

- A structural check of any document that reports, with the object and the
  clause, where it departs from the object model of ISO 32000-2 (types,
  required entries, and value constraints from the Arlington PDF Model), from
  the invariants that pylopdf relies on (the page tree, name and number
  trees, cross-references, the structure tree and its parent tree, and
  appearances), and from the rules of a claimed profile that can be decided
  without rendering. Deciding PDF/A and PDF/UA conformance stays with
  veraPDF.
- Profiles as typed values: the PDF/A, PDF/UA, PDF/X, and PDF/VT claims of a
  document, with its output intents; `doc.pdfa_claim` joins them.
- Conformance-preserving edits: on a document that claims PDF/A-1 to
  PDF/A-4, PDF/UA-1, or PDF/UA-2, every editing operation keeps the claim
  true, by embedding fonts, adding an output intent, tagging what it draws,
  and updating XMP and the structure tree, or refuses or reports the claim it
  would break, as the caller chooses.
- Accessibility edits on existing documents: alternate text, tags, reading
  order, languages, and role maps added and changed through the structure
  tree.

Acceptance: on a corpus of PDF/A-1b, PDF/A-2b, PDF/A-2u, PDF/A-3b, PDF/A-4,
and PDF/UA-1 documents that veraPDF accepts, every editing operation of the
public API leaves a document that veraPDF still accepts or reports the claim
it breaks; and the structural check finds every defect of a corpus of
documents broken on purpose.

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

### Specification completion

Phase 3, by feature, in the workstream that owns each one. It covers the
features of ISO 32000-2 that neither the capability map nor the canonical
workflows reach, until the specification map has no `planned` entry. Each
gets typed reading, creation, and editing, and keeps its structures through
every edit and save.

- Interactive features: the remaining annotation types (screen, printer's
  mark, watermark, 3D, RichMedia, and projection), every action type
  (rendition, transition, optional-content state, hide, import, submit and
  reset form, thread, embedded go-to, and 3D view, among others), additional
  actions on documents, pages, annotations, and fields, and form data
  exchange in FDF and XFDF.
- Navigation and presentation: article threads, page transitions and display
  durations, sub-page navigation, thumbnails, portable collections and their
  navigators, and named pages.
- Multimedia and 3D as data: renditions, media clips, and 3D streams (U3D and
  PRC) with their views; playback and display stay with viewers.
- Measurement and geospatial features: viewports, measure dictionaries, and
  geospatial coordinates at full precision.
- Document interchange: associated files (also for PDF/A-3), document parts
  (PDF/VT), web capture, page-piece dictionaries, separations, trapping,
  printer marks, and the extensions dictionary.
- Signature structures beyond signing itself: DocMDP and FieldMDP
  permissions, the document security store with its validation data, and
  document timestamps, read, kept valid, and written for pyHanko to sign; and
  the unencrypted wrapper documents of PDF 2.0.
- Deprecated features (XFA forms, movie and sound annotations and actions,
  and RC4 encryption among them): read, interpreted as data, edited or
  removed, and preserved. Creating them is an explicit `non-goal` under
  [Non-goals](#non-goals); deprecation is not an `n/a` justification.

Acceptance: the specification map has no `planned` or `engine` entry, and
every feature of this milestone has a save-and-reopen test and, where a
renderer shows it, a rendering test; any `partial` or `non-goal` entries are
published separately and do not pass the [completeness claim](#completeness-claims).

## v1.0

These are release and API-stability gates. Shipping v1.0 does not establish
complete control; that requires the separate [claim criteria](#completeness-claims).

- The specification map has no `planned` or `engine` entry: every feature of
  ISO 32000-2 is `done` in each dimension that applies to it, or `partial` or
  `non-goal` under a recorded release decision. Every `partial` lists its
  missing behavior and follow-up issue and milestone; every exclusion is
  published, and `n/a` is justified by the specification. Release notes
  name these limitations without claiming complete coverage.
- The preservation and consistency harnesses pass for every editing
  operation; the round trip of [Canvas](#canvas) lists no exception; and the
  [Conformance](#conformance) acceptance holds.
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
- **Specification.** Errata and amendments of ISO 32000-2, new extensions in
  the ISO/TS 32000 series, new parts of the profiles, and releases of the
  Arlington PDF Model update the specification map within a month.

## Non-goals

- **Input formats other than PDF and images**: XPS and OpenXPS, EPUB, MOBI,
  FB2, HTML, and text documents, with pymupdf's reflow APIs (`layout`,
  chapters, and locations). Convert them to PDF with their own tools.
- **Viewer behaviors.** pylopdf does not run JavaScript or XFA, play media,
  or display 3D artwork, and it does not reproduce device-dependent controls
  such as halftones. Their data structures remain in scope for reading,
  interpretation as data, editing, preservation, and removal; creation is
  in scope except for the deprecated features below.
- **Creating deprecated features.** XFA forms, movie and sound annotations
  and actions, and RC4 encryption are not generated. Their Create entries
  are `non-goal`, with this policy recorded per feature, not `n/a`.
  Deprecation does not exclude reading, interpretation as data, editing or
  removal, or preservation. RC4 output remains refused because it is
  insecure; that is a deliberate limit on writing, not complete support.
- **MuPDF's runtime internals**: its store, message stream, memory pools,
  bindings, and helpers, which have no capability at the PDF level.
- **Compatibility layers**: no `fitz` module, no aliases, no reproduction of
  MuPDF's numerical quirks, and no byte-identical output.

Three capabilities stay with ecosystem tools, through tested recipes:

- **Cryptographic signing**: pyHanko, with incremental save keeping
  signatures valid and pylopdf reading and writing the signature structures
  around it. Watch underskrift, a BSD-2-Clause PAdES implementation over
  lopdf.
- **HTML and CSS pagination** (pymupdf's `Story`, `DocumentWriter`, and
  `Archive`): typst. Reassess fulgur, which pairs Blitz with krilla, around
  2027-01 as an engine path; the HTML subset for text boxes is in
  [Writing](#writing).
- **PDF/A and PDF/UA validation**: veraPDF. pylopdf's structural check
  reports what it can decide without rendering ([Conformance](#conformance)).

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
- **PDF Association**: the Arlington PDF Model, the amendment of ISO 32000-2
  in progress, PDF/UA-2 and Well-Tagged PDF, and the dated revision of
  ISO/TS 32004.
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
- **D8** (2026-10-23): the specification defines completeness. ISO
  32000-2:2020 with its errata and extensions, and the PDF/A and PDF/UA
  profiles, measure what complete means, in five dimensions per feature
  recorded in the specification map. The capability map stays as the floor
  and the source of the migration tools. The
  [assessment](#assessment-2026-10-23) showed that pymupdf's names left whole
  areas unmeasured: round trips, text editing in composite fonts, the paint
  as stored, preservation, and conformance after edits.
- **D9** (2026-10-23): control covers documents, not viewers. Running
  scripts, playing media, displaying 3D artwork, and device-dependent
  controls such as halftones are non-goals for interpretation; the
  structures behind them are in scope in every dimension.
- **D10** (2026-10-23): 1.0 waits for the specification map. The stability
  policy freezes the API, and an API frozen before the features it must
  cover are designed would have to break after 1.0; so 1.0 requires a map
  without `planned` or `engine` entries, and phase 3 completes the features
  that pymupdf does not reach.
- **D11** (2026-10-24): release readiness and completeness are separate.
  A reviewed `partial` may ship in 1.0 with its missing behavior and
  follow-up work published, but never satisfies a completeness claim.
  Claims name their scope, exclusions, and ecosystem dependencies, and
  require `done` in every applicable dimension inside that scope.
- **D12** (2026-10-24): intentional exclusions use `non-goal`; `n/a`
  requires that the dimension has no meaning under the specification.
  Deprecated creation remains an explicit policy exclusion, refining D9,
  and cannot inflate completion counts.
- **D13** (2026-10-24): exact persistence retains source decimal tokens or
  exact decimal values. Increasing floating-point precision does not meet
  this requirement. Preservation tests compare against source tokens or an
  independent exact-decimal oracle so matching rounded readings cannot
  conceal a loss.

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
- **2026-10-23, lopdf 0.46**: over the same 14 corpus files and 8 damage
  types, inputs whose first page reads rose from 67 to 78 of 112, because
  damaged cross-reference streams are now rebuilt. Of the first six pages of
  the 1,455 pdf.js corpus files, 4 that raised now open, 25 read text strings
  with their tabs and line breaks, 1 drops an odd UTF-16BE byte, 3 AES-128
  files that open without a password now probe with their page count, and
  `issue17147.pdf` would have lost 17 of its 26 objects to a corrected
  offset without pylopdf's rebuild; nothing else changed.
- **2026-10-23, assessment**: an external review asked whether pylopdf reads
  and writes every feature of PDF under complete control, and found that it
  does not (the [assessment](#assessment-2026-10-23)). The capability map then
  had 962 names: 455 available, 29 integration, 315 planned, and 163
  non-goals. lopdf 0.46 stores reals as `f32` (`Object::Real(f32)`, chosen for
  performance in J-F-Liu/lopdf#190 in 2022) and writes them with `f32`'s
  shortest representation, so assigning or loading `35.6812345` and saving
  writes `35.681236`, and `123456.789` becomes `123456.79`. hayro-interpret
  0.8.0 implements optional-content visibility (`ocg.rs`) but neither knockout
  groups nor overprint. ISO 32000-2:2020 is distributed at no cost by the PDF
  Association since 2023-04-05 under ISO's copyright, and the Arlington PDF
  Model describes its objects under the Apache-2.0 license.
