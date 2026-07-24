# pylopdf roadmap

This is the canonical medium-term plan. It is based on a 2026-07-22 survey of
the market and upstream projects (all APIs in lopdf 0.44, all hayro 0.7 crates,
and the Python PDF ecosystem), followed by a 2026-07-23 deeper review of areas
outside the intended core: krilla, typst, pure-Rust OCR, digital signatures, and
HTML-to-PDF. Confirmed findings are recorded at the end.

See [AGENTS.md](AGENTS.md) for day-to-day development context and
[CHANGELOG.md](CHANGELOG.md) for completed changes.

## Strategy

Build **a verifiably accurate, permissively licensed library that combines
rendering, positioned text extraction, and editing in one package**.

- As of 2026-07, no mature permissive library combines all three. pymupdf is
  AGPL; pypdfium2 has limited editing and explicitly documents a bus factor of
  one; pikepdf deliberately excludes extraction and rendering; pypdf has slow
  extraction and no renderer.
- pymupdf's structural weaknesses are difficult to erase: AGPL licensing,
  officially unsupported threading, no free-threaded wheel, and wheels above
  20 MB. pymupdf-layout, introduced in 2026-06 to power pymupdf4llm's layout
  analysis, uses PolyForm Noncommercial plus commercial licensing. MIT's
  commercial advantage is therefore increasing.
- Rust competitor pdf_oxide, started in 2025-11, releases weekly and records
  about 145,000 monthly downloads, but has no renderer and publishes
  self-reported benchmarks without third-party verification as of 2026-07-23.
  Differentiate through a real-world corpus, reproducible evidence, and upstream
  contributions.
- **oxidize-pdf** (`bzsanti/oxidizePdf`, MIT, crates.io `oxidize-pdf`) is a
  separate direct competitor. It combines parsing, generation, extraction,
  encryption, splitting, merging, and rotation in pure Rust while promoting
  structure-aware chunking for AI/RAG. It had 91 releases and an update in the
  same month as of 2026-07-22. Do not confuse it with pdf_oxide.
- The largest demand is positioned text extraction followed by Markdown
  conversion for RAG/LLM workloads. pymupdf4llm records about 24 million monthly
  downloads and docling about 20 million.
- CJK handling—vertical writing, CID fonts, and Japanese business forms—is a
  defensible advantage built on the existing fallback implementation and
  corpus, and is difficult for global competitors to reproduce.

## Principles

- Be pymupdf-*style*, not pymupdf-compatible. Match migration-critical data
  shapes such as word tuple ordering, dict layout, and
  `search_for → list[Rect]`.
- Preserve **one-way data flow**. lopdf's `Document` is the sole source of truth;
  hayro is a pure view over serialized bytes for rendering and extraction. A
  cached `hayro_pdf` is invalidated on edits. Because hayro sees normalized
  lopdf output, damaged PDFs cannot be interpreted differently by editing and
  rendering, and rendered output always matches saved output. New engines must
  preserve this shape. krilla, for example, should return bytes that are then
  imported into lopdf; engines must not share mutable state.
- Use lopdf and hayro fully: lopdf encryption, `SaveOptions`, image insertion
  and extraction, TOC, text replacement, and incremental save primitives;
  hayro `Device`, `RenderSettings`, `warning_sink`, and hayro-write page-to-XObject
  support.
- Implement areas absent from lopdf through pylopdf's own dictionary operations:
  AcroForm, annotation creation, attachments, and page labels.
- Keep the core wheel small by choosing between native implementation and
  ecosystem integration. Use typst/typst-py for typesetting and new-document
  PDF/A, pyHanko for signatures, and veraPDF for PDF/A validation. Protect
  integration recipes with tests.
- Introduce krilla, the MIT/Apache-2.0 generation crate by hayro's author, under
  a three-engine split: editing = lopdf, rendering = hayro, generation = krilla.
  A 2026-07-23 audit confirmed that krilla core does not depend on hayro; only
  the `pdf` feature pulls hayro-write, which is unnecessary for lopdf object
  import. The intended configuration is
  `default-features = false, features = ["simple-text"]`, with built-in
  rustybuzz shaping and without redundant raster image support. skrifa, flate2,
  and png are already shared through hayro, so the estimated wheel increase is
  0.5–1 MB and must be measured. This unlocks, in order: arbitrary embedded
  fonts including CJK in `insert_text`; AcroForm appearance generation;
  in-house new-document PDF/A; and eventually tagged PDF/UA.

## Release plan

Each release has one theme. Ordering follows dependencies: Page object, then
extraction, then drawing.

### Near term: 0.5.x foundations

- [x] Cache the hayro PDF and invalidate it after edits, eliminating repeated
      serialization and parsing for every render.
- [x] Release the GIL for load, save, render, extraction, and merge.
- [x] Add `dpi=` and `background=` to `render_page`.
- [x] Add `garbage=`, `deflate=`, and `object_streams=` to `save` and `tobytes`
      through lopdf `SaveOptions`. The measured reduction on already-compressed
      `bill-hr815.pdf` is 13%.
- [x] Make the repository public and configure its description and topics
      (2026-07-22).
- [x] Add encryption and CJK rows to the README comparison table (2026-07-23).
- [ ] Improve discovery through possible participation in py-pdf/benchmarks and
      articles on relevant developer platforms.

### v0.6 — complete page operations and saving

Released as v0.6.0 on 2026-07-23.

- [x] Add `Page` objects with `doc[i]`, negative indices, iteration, and
      generation tracking that raises `StalePageError` after structural changes.
- [x] Read and write page rotation and `MediaBox`/`CropBox`, resolving
      inheritance and indirect references.
- [x] Support ranged `insert_pdf` (`from_page`, `to_page`, `start_at`, including
      reverse order), `new_page`, `copy_page`, and page duplication through
      repeated indices in `select`.
- [x] Read and write TOC with `get_toc` and `set_toc`; page numbers are one-based
      for pymupdf compatibility.
- [x] Encrypt on save with AES-256 V5/R6 and permissions while leaving the
      in-memory document plaintext.
- [x] Add the `PdfError`/`PasswordError`/`DocumentClosedError`/
      `EncryptedDocumentError`/`StalePageError` hierarchy.
- [x] Publish `peek_metadata` for fast metadata without full parsing and
      `max_decompressed_size` for decompression-bomb protection.

### v0.7 — positioned text extraction

Released as v0.7.0 on 2026-07-23.

- [x] Replace lopdf extraction with a hayro-interpret `Device` implementing
      `get_text("text"/"words"/"blocks"/"dict")`,
      `search_for → list[Rect]`, and invisible text. This fixed lopdf#535 and
      non-embedded CJK extraction. MCID retention remains unimplemented and can
      be added when `to_markdown` requires it.
- [x] Add `Page.get_images`, passing through filter chains ending in DCT as JPEG.
- [x] Route hayro's `warning_sink` through Python warnings as `PylopdfWarning`.
- [x] Add `Pixmap`. The buffer protocol was deferred because `Py_buffer` entered
      the stable ABI in Python 3.11 and conflicts with `abi3-py310`; `samples`
      performs one copy. Reconsider when raising the abi3 floor or adding cp314t.
- Note: hayro 0.8 is expected to change the `Device` API to `DrawProps`, requiring
  one migration. See the watchlist.

### v0.7.x — ecosystem integrations

Make intentionally external features “solved through integration.”

- [x] Document typst-py for typesetting and new-document PDF/A, pyHanko for
      signatures, and veraPDF for validation.
- [x] Add integration tests in `tests/test_interop.py` under the `interop`
      dependency group. Verify `typst.compile → pylopdf.open(stream=)` and that a
      pyHanko incremental signature preserves the entire pylopdf output as an
      unchanged prefix. Include the group in CI.

### v0.8 — drawing

Released as v0.8.0 on 2026-07-23.

- [x] Add `insert_image`: JPEG SOF parsing and passthrough; PNG decoding through
      the png crate with soft-mask transparency. Avoid lopdf's image-crate
      feature to keep the wheel small. Append content without re-encoding and
      wrap existing content in `q/Q` once.
- [x] Add `show_pdf_page` for watermarks and stamps through native lopdf
      page-to-Form-XObject import. hayro-write proved unnecessary: import the
      object graph and resources like merge, keep content bytes unchanged, and
      resolve rotation and CropBox visually.
- [x] Solve CJK watermarks and headers through typst integration: typeset a
      one-page PDF, then apply it with `show_pdf_page`. typst subset-embeds fonts
      and can reuse `pylopdf-fonts-cjk` through `font_paths`. Integration tests
      cover the recipe. krilla remains the future option for self-contained CJK
      `insert_text`.
- [x] Publish lopdf's simple-encoding partial text replacement as
      `Page.replace_text`, explicitly excluding CJK.
- [x] Add `Page.insert_text` for headers, footers, page numbers, and Bates
      numbers using Standard 14 fonts and WinAnsi. CJK input points to the typst
      recipe. Rotated pages remain upright through display-space `Tm`.
- [x] Read annotations and create highlight/link annotations. Search results can
      be passed directly for “search and mark.” Highlights always include an
      `AP /N` appearance stream with Multiply blending. hayro renders
      annotations with appearances when `render_annotations` is true by default,
      enabling pixel-level tests. It does not render annotations without `AP`.

### v0.9 — document finishing

Released as v0.9.0 on 2026-07-23.

- [x] Implement first-stage AcroForm reading and filling through
      `get_form_fields` and `set_form_field`: inherited `FT`/`Ff`/`V`, fully
      qualified dotted names, checkbox bool-to-on-state resolution, `/AS`
      synchronization, and `NeedAppearances`. Native appearance generation
      remains stage two, so pylopdf's renderer does not yet display filled values.
- [x] Add EmbeddedFiles through `embfile_add`, `names`, `get`, and `del`, with
      recursive Kids reading, flat rewriting, preservation of other `/Names`
      trees, Unicode names in `UF`, and survival across
      garbage/deflate/object-stream saves.
- [x] Add page labels through `get_page_labels`, `set_page_labels`, and
      `Page.get_label`, including recursive number-tree reading, flat rewriting,
      and R/r/A/a/D label calculation.
- [x] Add initial `Document.to_markdown` and `Page.to_markdown`. The most common
      size is body text; larger sizes become heading levels. CJK wrapped lines
      join without spaces, lists normalize, and OCR layers participate.
      Documented limitations: tables, multicolumn order, vertical writing, and
      some emphasis metadata. Smoke-tested on six real-world files.
- Deferred: incremental save. A 2026-07-23 OSS review found that qpdf and pikepdf
  succeed with normalization-and-rewrite designs, while pypdf's implementation
  accumulated bugs immediately after its 5.0 debut in 2024-09 (for example
  pypdf#3118). The main need—signature preservation—is already covered by
  pyHanko with byte-prefix guarantees. Reconsider when real issue demand appears.
- [x] Add `Page.insert_ocr_text_layer`, following the ocrmypdf approach:
      non-embedded CID font, Identity-H, ToUnicode, and `Tr 3`. It extracts and
      searches CJK independently of fallback fonts with nearly zero size growth,
      and accepts `get_text("words")`-shaped data.
- [x] Read XMP PDF/A claims with `Document.get_pdfa_claim`, returning
      `(part, conformance)`. Integration tests read `(2, "B")` from typst's
      krilla-validated output. The docstring states that this is not validation.

### v0.10 — hardening and reusable page interpretation

v0.10 is the pre-1.0 stabilization release, not the OCR release. It publishes
the substantial safety, performance, documentation, and link-reading work
completed after v0.9, then establishes the reusable interpretation layer needed
for deeper extraction accuracy. The release is intentionally allowed to refine
pre-1.0 APIs.

- Publish the unreleased decompression-limit, object-import isolation, malformed
  input, rotated extraction, rendering, compression, documentation, benchmark,
  and `Page.get_links` changes as one coherent minor release.
- Synchronize PyPI tags and GitHub Releases, enable public issue reporting, and
  add contributor guidance plus issue and pull-request templates. Require a
  redistributable minimal PDF for parser, renderer, and extraction regressions.
- [x] Introduce an internal bounded, generation-invalidated `TextPage` that
  interprets and clusters a page once, then serves `get_text`, `search_for`, and
  `to_markdown`. It owns glyph geometry, transformed baseline direction, and
  font metadata without retaining references into hayro.
- [x] Cache page interpretation without weakening the one-way lopdf-to-hayro
  data flow. Every edit invalidates both the hayro parse and derived text pages;
  fallback-font changes invalidate derived text pages while retaining the parse.
- [x] Add an initial coverage-guided public-API fuzzing lane for bounded open,
  positioned extraction, search, rendering, editing, object-stream saving, and
  reopening, seeded by the redistributable corpus. Continue expanding
  damaged-input coverage for truncated xrefs, Type 3 fonts, JPX, transparency
  groups, annotations, and links.
- [x] Add artifact smoke tests that install every natively runnable wheel plus
  the sdist and exercise import, open, extraction, rendering, and save before
  publication. Cross-compiled Linux aarch64 and macOS x86_64 wheels remain
  build-only because their release runners cannot execute the target binary.
- Migrate to hayro 0.8 when released before building extensive new layout logic
  on the old `Device` interface.

### v0.11 — layout, creation, and concurrency depth

v0.11 is the main capability-expansion release before v1.0. It has no arbitrary
feature-count deadline: work continues until the new capabilities are accurate,
measurable, and coherent rather than stopping at a nominal parity checklist.

- [x] Build deterministic multicolumn reading order on `TextPage`: sustained
  whitespace gutters split line segments into recursive left-to-right columns,
  with full-width headings and footers preserved and isolated wide gaps
  rejected.
- [x] Add high-confidence geometry-based table extraction for complete
  axis-aligned stroked grids, with owned `TableFinder` / `Table` results,
  display-space cell bboxes, text matrices, and Markdown export.
- [x] Extend the inspectable rule-based core to thin filled-rectangle rules and
  rectangular merged cells. Keep adversarial search bounded and reject broken
  outer grids and compact filled decorations.
- [x] Add an explicit, opt-in `strategy="text"` for borderless tables. Require
  at least three consecutive rows, stable segment counts, aligned left/right
  edges, compatible leading, and clear gutters; keep the vector-rule strategy
  as the default and document aligned multicolumn prose as an unavoidable
  ambiguity.
- Add confidence diagnostics and region clipping before considering an optional
  layout model.
- [x] Add vertical CJK extraction by assembling transformed vertical baselines
  directly and conservatively inferring hidden font WMode from CJK glyph
  geometry. Vertical columns read top-to-bottom and right-to-left between
  horizontal page furniture; synthetic Shift-JIS vertical fixtures cover the
  positive path and the Japanese business-document corpus guards against false
  classification. Ruby, warichu, and mixed-orientation typography remain
  explicit follow-up depth.
- Turn the successful krilla spike into arbitrary embedded-font text insertion,
  then `insert_textbox`, and finally native AcroForm appearance generation.
  Measure wheel size and rendering fidelity at every stage.
- Add `Document.render_pages(workers=)`, define same-document concurrency
  semantics, and evaluate `get_pixmap(clip=)` together with upstream hayro
  viewport support.
- Build and test cp314t wheels only after the mutable `Document` concurrency
  audit. Enable the Pixmap buffer protocol in the version-specific lane and
  verify real parallel scaling rather than treating wheel availability alone as
  free-threading support.
- Replace public `dict[str, Any]` shapes with documented `TypedDict` contracts
  where doing so remains compatible with pymupdf-style data.
- Continue the optional OCR track below if rten execution, model packaging,
  memory use, and end-to-end accuracy all pass their gates.

#### Optional OCR track for v0.11 — `pylopdf[ocr]`

Decision depends on measured accuracy. “pip-only, no shared libraries,
permissively licensed Japanese OCR” remains a gap: pymupdf requires an external
Tesseract install, pponnxcr is AGPL, and rapidocr depends on the C++ onnxruntime.
This aligns with the CJK moat and merits staged exploration.

- Runtime: statically link rten, a pure-Rust ONNX runtime under MIT/Apache-2.0.
  Estimated main-wheel increase: 1.5–2.5 MB.
- Model: PP-OCRv5_mobile, with 4.6 MB detection plus 15.8 MB recognition under
  Apache-2.0. Its standard model already includes Japanese.
- Distribution: a separate `pylopdf-ocr-models` wheel, following the font-wheel
  pattern so model generations can update independently.
- [x] Prerequisite 1, Japanese accuracy measurement, completed 2026-07-23:
      **go**. At 300 dpi, the PP-OCRv5 mobile Chinese model, which covers Chinese,
      Japanese, and English and has no separate v5 Japanese recognizer, measured
      4.0% strict CER and 1.3% after NFKC on five synthetic cases plus one MHLW
      document with 2,428 ground-truth characters. Kanji, kana, and digits were
      nearly perfect. Remaining differences were width folding and symbols such
      as circled numbers, postal marks, and reference marks. It beat the v4
      Japanese-specific model in practical accuracy and trailed the server model
      by only 0.5 points.
- [ ] Prerequisite 2: prove rten can execute the PP-OCRv5 mobile ONNX models.
- Design constraints from the spike: render OCR input on white, not the default
  transparent background; default to 300 dpi because 200 dpi misses lines at
  9 pt and below; do not downscale internally; distribute
  detection/recognition/classifier/dictionary, about 22 MB, in a separate wheel.
- Use ocrs-cjk (MIT/Apache) as a reference, not a dependency.

### v1.0 — product-quality declaration of trust

Target no earlier than 2026-08. v1.0 is not a calendar-driven promotion of the
current API. It follows v0.10 and v0.11 field use and ships only after the
library's product experience, error recovery, documentation, performance, and
known-limit behavior are polished together.

- Freeze the API and publish semantic-versioning and deprecation policies.
- [x] Publish the EN/JA/zh-CN/KO documentation and pymupdf migration guide.
      Rebuilt on 2026-07-24 with Zensical 0.0.51 and a custom Living Document
      theme at <https://yhay81.github.io/pylopdf/>. Includes per-locale strict
      builds, search, dark mode, same-page switching, `llms.txt`, and an Open
      Graph card. English is canonical; Japanese, Simplified Chinese, and Korean
      are first-class translations defined in `LANGUAGES.md`. `docs.yml`
      deploys on pushes to main without building Rust.
- [x] Publish reproducible benchmarks from `bench/run.py` using one corpus, one
      task definition, medians, wins and losses, and pymupdf similarity as a
      fidelity proxy. The first 2026-07-23 run found pylopdf faster on four of
      seven extraction files, 4.1× faster for merge, and faster on all seven 2×
      renders. Apply separately to py-pdf/benchmarks.
- Publish an explicit support and concurrency contract covering GIL-enabled,
  free-threaded, single-document, and multi-document use.
- Validate installation and core workflows from every published wheel and the
  sdist, and publish release provenance alongside the artifacts.
- Review every documented limitation. Improve high-value limits before release;
  keep only those backed by a clear architectural or ecosystem boundary.
- [x] Translate runtime errors and warnings to English before API freeze
      (2026-07-24, about 100 Rust/Python messages plus tests).
- [x] Make English canonical for repository documentation, comments, docstrings,
      automation, and future commit messages. Localized docs and CJK fixtures are
      the only exceptions (2026-07-24).
- [x] Add `SECURITY.md` with a private-reporting path, untrusted-PDF guidance,
      and `max_decompressed_size`, plus cargo-audit in CI. pip-audit is omitted
      because the package has no runtime Python dependencies.

### Continuing engineering inventory — v0.10 through v1.x

Candidates from the 2026-07-23 lopdf/hayro/krilla inventory. Completed items
remain as evidence; unfinished items feed v0.10 and v0.11 in dependency order
rather than waiting automatically for v1.x.

- [x] Switch flate2 to zlib-rs. Three merge rounds over the corpus
      (554 pages) with garbage=3, deflate, and object streams improved median
      save time from 74 to 66 ms, or 13%, with only a 0.01% output-size increase.
      The earlier 3.3× result measured compression alone; GC and serialization
      dominate complete saves.
- [x] Do not expose `SaveOptions.compression_level` or `linearize` yet.
      In lopdf 0.44, linearize is a dead writer flag; `is_linearized` only
      detects existing files. `compression_level` affects only object streams
      through four buckets, while normal streams always use
      `Compression::best()`. Contribute consistent normal-stream support
      upstream before exposing the option.
- [x] Add `Page.get_links` for `/A` actions (URI, GoTo, GoToR, Launch, Named) and
      direct `/Dest`. Resolve GoTo named destinations through multilevel,
      cycle-safe `/Names` trees and legacy `/Dests`; convert destinations to
      zero-based page numbers, display-coordinate points, and zoom. Return
      pymupdf-style dicts with `LINK_GOTO` constants and `Point`. Verified by
      resolving all 40 GoTo links in `usrguide.pdf`.
- [x] Complete a krilla integration spike on 2026-07-23: **go**. krilla 0.8.2
      builds in isolation with `default-features = false` and `simple-text`.
      It subset-embedded a 4.5 MB Noto Sans JP font into an 8 KB one-page PDF,
      which pylopdf opened, extracted with exact Unicode through ToUnicode, and
      rendered. The spike executable is 3.3 MB, but skrifa and related
      dependencies are shared with hayro, so actual extension growth should be
      much smaller and must be measured. Next: design arbitrary-font
      `insert_text`, generate one page with krilla, and import it as a Form
      XObject into lopdf.
- [ ] Add `get_pixmap(clip=)`. hayro `RenderSettings` supports only an
      origin-fixed viewport. Decide between proposing offset/transform upstream
      and initially rendering the full page then cropping.
- [ ] Cache extraction results by generation to eliminate repeated
      interpretation in search-then-annotate loops. Consider one layer keyed like
      `hayro_pdf` and an explicit TextPage-style object.
- [ ] Add `Document.render_pages(workers=)` as a thin supported API over existing
      GIL-free rendering, measured at 1.93× with two threads. Its full value
      arrives with cp314t.
- Keep annotation/widget dict and tuple APIs until mutation grows enough to
  justify objects. Do not copy pymupdf's heavyweight `Annot`.

### Parallel work, not tied to releases

- Expand the corpus with damaged PDFs such as truncated xrefs, Type 3 fonts,
  JPX, transparency groups, and annotations/links.
- [x] Normalize rotated-page extraction into display space (2026-07-23) by
      passing the renderer's `initial_transform(true)` to extraction. Reading
      order, search, words, image bboxes, and OCR layers now use display
      coordinates on rotated pages and correctly handle nonzero CropBox origins.
- [x] Improve rendering speed (2026-07-23). Profiling found PNG encoding, not
      rasterization, responsible for up to 85%; png's default
      Balanced+Adaptive managed about 11 MB/s on photos. Switching to
      Fast/fdeflate and releasing the GIL made pylopdf faster than pymupdf on all
      seven corpus renders, including `wdl6812` from 278 to 43 ms. Remaining
      candidates: reuse `RenderCache` for the hayro PDF lifetime, worth 27–35%
      but requiring a self-reference design; zlib-rs for high compression; and
      upstream hayro stencil-mask and `num_threads` improvements.
- [x] Add font names and pymupdf-compatible flags to extraction spans from
      embedded font weight/italic metadata. `to_markdown` now emits emphasis.
      Standard 14 Type 1 fonts still produce flags 0 because hayro exposes no
      font data; upstream Type 1 metadata remains a contribution candidate.
- Upstream contributions, started 2026-07-23; three of four merged by 2026-07-24:
  - [lopdf#537](https://github.com/J-F-Liu/lopdf/pull/537), a one-line fix plus
    regression test for lopdf#535, is **merged** but newer than lopdf 0.44.0 and
    awaits a release.
  - [hayro#1315](https://github.com/LaurenzV/hayro/issues/1315) reports stencil
    masks about 5× slower.
  - [hayro#1316](https://github.com/LaurenzV/hayro/issues/1316) proposes exposing
    `num_threads`. PR [#1317](https://github.com/LaurenzV/hayro/pull/1317)
    remains **open** after all eight cargo-hack feature combinations, clippy,
    fmt, and pixel-identical validation over 147 pages. A/B medians over seven
    runs show 1.35–1.55× at scale 4–6, 10–20% at scale 2, and no benefit on
    scan-dominated files.
  - [hayro#1318](https://github.com/LaurenzV/hayro/pull/1318) is **merged**. It
    composites mismatched masks onto a common grid instead of nested drawing,
    reducing `wdl6812` mask drawing from 11.4 to 4.2 ms and the page from about
    30 to 21 ms. The PR discloses visually reviewed low-amplitude differences in
    26 upstream tests caused by compositing order.
  - [hayro#1320](https://github.com/LaurenzV/hayro/pull/1320), following issue
    [#1319](https://github.com/LaurenzV/hayro/issues/1319), is **merged**. It
    replaces packed 1-bit mask expansion with a LUT. The original issue
    incorrectly attributed the cost to JBIG2 and was publicly corrected after
    confirming that JBIG2 filters already produce 8-bit data. A synthetic
    2400×3150 mask improved from 48–60 ms to 1.5–1.6 ms, about 33×, with
    pixel-identical output. The remaining 4.4 ms in `wdl6812` is hayro-jbig2
    arithmetic decoding.
  - crates.io still has hayro 0.7.1 from 2026-06-05, so these merges are
    unreleased. hayro 0.8's DrawProps change is also merged and unreleased; the
    next release will likely combine the Device migration with #1318/#1320.
  - Other candidates: hayro #452 for an official text extraction `Device`, Type
    1 font metadata, clip/offset in `RenderSettings`, a `'static` `RenderCache`,
    consistent normal-stream `compression_level` in lopdf, and implementing or
    removing lopdf's dead `linearize` flag.
- [x] Add a Python 3.10 CI job to validate the abi3 floor (2026-07-23).
- Experiment with a Pyodide/emscripten wheel; pymupdf's wasm wheel cannot be
  installed through micropip.
- Research table extraction as a major post-v1.0 theme.

## Watchlist

- **hayro 0.8**: the DrawProps `Device` API change (#1245) is merged but
  unreleased, as are #1318 and #1320. When released, update the two `extract.rs`
  implementations—likely mechanically because paint is mostly ignored—and gain
  the performance improvements. Keep this separate from krilla integration.
- **fulgur**, Blitz plus krilla for HTML-to-PDF under MIT/Apache-2.0, already
  supports `@page`, page breaks, running headers/footers, and tagged PDF/UA-1,
  but is four months old, single-maintainer, at 24.1% css-page WPT, and changing
  APIs rapidly. Reassess around 2027-01 for survival, API stability, and a stable
  Blitz 0.3. pyfulgur currently stops at cp312 and is not abi3, leaving an
  opportunity.
- **underskrift**, BSD-2-Clause PAdES signing over lopdf by kushaldas, appeared
  in 2026-03 and claims B-B through LTA. Reconsider as an optional signature
  backend after maturity and lopdf-version alignment.
- **PP-OCRv6**, released in 2026-06: wait for ONNX conversion support before
  selecting the `[ocr]` model generation.
- **parley**, linebender's text layout engine and a krilla dev dependency:
  evaluate for line breaking when implementing `insert_textbox`, the declared
  upper limit for native typesetting.
- **zune-jpeg**: candidate JPEG recompressor for a future
  `compress_images(dpi=, quality=)`, useful for email attachments and missing
  from pypdf/pikepdf.
- **PP-DocLayout**, Apache-2.0: possible `[layout]` alternative to the
  PolyForm-Noncommercial pymupdf-layout. It could share rten with `[ocr]`;
  evaluate after OCR succeeds.
- **Incremental save**: reconsider after real issue demand or stabilization in
  pypdf. The implementation path would retain original bytes at load, reparse
  them at save, and append only changed objects through lopdf
  `IncrementalDocument`, initially excluding encrypted documents.

## Explicit non-goals

These boundaries preserve focus. The 2026-07-23 deeper review updated the
evidence; built-in OCR moved out of this list into a conditional v0.10 candidate.

- **Drop-in pymupdf compatibility**: remain pymupdf-style.
- **Converting or validating arbitrary PDFs as PDF/A**: krilla's validated
  export is for new content and explicitly rejects embedded PDF pages as
  `ValidationError::EmbeddedPDF`. Converting lopdf-edited PDFs therefore cannot
  be assembled from the current ecosystem. Validation would duplicate
  veraPDF's hundreds of Java rules. Use typst for new-document PDF/A and expose
  only XMP claim reading in v0.9.
- **Native digital signatures**: technically possible with lopdf
  `IncrementalDocument`, whose writer preserves original bytes, but pyHanko
  already provides active MIT-licensed PAdES B-LTA and validation. Domestic
  demand tends to require certified timestamps and LTV; a B-B-only
  implementation would be a poor entry. Watch underskrift.
- **XFA or JavaScript forms**: XFA is deprecated in PDF 2.0, has no Rust
  implementation, and lacks major-viewer support. PDF JavaScript demand is
  mostly form calculation; bundling an engine conflicts with both wheel-size
  and security goals.
- **Native HTML-to-PDF**: recreating pagination would duplicate the work behind
  fulgur's roughly 2,800 commits. Keep fulgur on the watchlist.
- **Bundling typst or another typesetter**: typst-py adds 25–33 MB and breaks the
  lightweight goal. Integrate externally. The maximum future native typesetting
  scope is text flow into a rectangle, similar to pymupdf `insert_htmlbox`.

## Survey notes: confirmed 2026-07-22

- lopdf 0.44.0 was current. Its `time` feature still did not compile; upstream
  #527 was merged but unreleased. Keep default features disabled.
- `save_with_options` automatically raises output to PDF 1.5 and switches to an
  xref stream when using object streams. `ObjectStreamConfig` defaults to
  100 objects and compression level 6.
- hayro 0.7 includes the `Device` trait and an official extraction example. All
  crates are dual MIT/Apache-2.0. typst 0.14 uses it for embedded PDFs.
- Monthly PyPI downloads from pypistats: pymupdf 106M, pypdf 116M, pdfplumber
  54M, pypdfium2 68M, pikepdf 9.3M, pymupdf4llm 24M, docling 20M.
- Concrete AGPL avoidance: doctr#486 removed pymupdf, browser-use#2610 treated a
  transitive dependency as a problem, and marker created pdftext explicitly
  “without the AGPL license.”

## Survey notes: Rust PDF crates, 2026-07-23

- **krilla**, a high-level pdf-writer-based generation API in the
  LaurenzV/typst ecosystem, is hayro's sibling project and the strongest
  reference for future drawing.
- Several extraction-focused Rust projects appeared in 2026:
  **kreuzberg**, an active multilingual document extractor with 8.7k stars;
  **pdf-extract**, based on lopdf with 3.19M total crates.io downloads; unpdf;
  and pdfsink-rs. v0.7 extraction enters a competitive market, not an empty one.
- mupdf-rs (AGPL) and poppler-rs (GPL-family) can only be references.
  pdfium-render is MIT over BSD-family PDFium but is unnecessary because hayro
  already renders.
- pdf-rs/pdf is an MIT low-level parser with experimental writing. It is much
  smaller than lopdf, which has 12.87M downloads and 2.2k stars, so switching has
  little motivation.

## Survey notes: deeper out-of-scope review, 2026-07-23

Confirmed findings across krilla, typst, pure-Rust OCR, signatures, and
HTML-to-PDF:

- **krilla 0.8.2**, MIT OR Apache-2.0 by hayro's author, supports validated
  PDF/A-1 through PDF/A-4 conformance and PDF/UA-1. CI validates with veraPDF and
  Arlington; typst 0.14 uses it as the PDF backend. Its `pdf` feature imports
  existing pages through hayro-write 0.7.0, but validated output rejects them as
  EmbeddedPDF. `NOTICE.md` discloses resvg-derived MPL code, which would require
  wheel license attribution.
- **hayro-write 0.7.0** explicitly calls itself an internal crate not meant for
  external use. krilla's `pdf` feature is a more stable wrapper when needed.
- **typst-py** wheels measure 25.7–36.5 MB, record 437,000 monthly downloads,
  follow upstream releases within a day, and ship cp38-abi3, cp314t, and
  emscripten wheels. It exports PDF/A-1b through 4 and UA-1. typst still lacks
  vertical writing (#5908) and ruby (#1489), so it cannot claim complete
  Japanese typesetting.
- **Pure-Rust OCR**: upstream ocrs is Latin-only and its model is
  CC-BY-SA-4.0. PP-OCRv5_mobile is Apache-2.0 and its 4.6 MB detector plus
  15.8 MB recognizer include Japanese. rten, with 970,000 total downloads and
  active MIT/Apache development, is the preferred pure-Rust runtime; tract-onnx
  is second. rapidocr demonstrates long-term redistribution of Apache-2.0
  models with LICENSE/NOTICE.
- **Digital signatures**: RustCrypto cms 0.2.3 can build PAdES B-B but lacks an
  ESS signing-certificate-v2 type, requiring custom DER. lopdf
  `IncrementalDocument` preserves original bytes. pyHanko 0.35.2 remains the
  active MIT-licensed Python reference.
- **PDF/A validation**: veraPDF, dual GPLv3+/MPLv2+ in Java, is effectively the
  only OSS implementation. Rust pdf-compliance requires a commercial production
  license; no native Python validator exists.
- **HTML-to-PDF**: Blitz is pre-alpha and schedules fragmentation for 1.0.
  hyper-render died after two days. fulgur, Blitz plus krilla under
  MIT/Apache and started in 2026-03, has 55 releases and about 2,814 commits
  implementing paged media. pyfulgur 0.37.0 ships cp39–cp312 non-abi3 wheels.
  weasyprint records 33.13M monthly downloads and documents performance
  limitations, proving demand for a Rust alternative.
- **XFA and JavaScript**: XFA is deprecated in PDF 2.0 and has no Rust
  implementation. pdf.js enables QuickJS form calculation in a sandbox by
  default; an extraction/editing library does not need general JavaScript.
- **pymupdf 1.28** remains AGPL and introduced pymupdf-layout, a GNN layout
  analyzer behind pymupdf4llm, under PolyForm Noncommercial plus commercial
  licensing.
- **pdf_oxide** records weekly releases, about 145,000 monthly downloads, and
  899 stars, but no renderer and no third-party benchmark verification.
