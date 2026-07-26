# pylopdf roadmap

This is the canonical medium-term plan. It began with a 2026-07-22 survey of
the market and upstream projects (all APIs in lopdf 0.44, all hayro 0.7 crates,
and the Python PDF ecosystem), followed by a 2026-07-23 deeper review of areas
outside the intended core: krilla, typst, pure-Rust OCR, digital signatures, and
HTML-to-PDF. Current competitive claims were reviewed again on 2026-07-25.
Survey sections at the end are dated snapshots, not rolling current claims.

See [AGENTS.md](AGENTS.md) for day-to-day development context and
[CHANGELOG.md](CHANGELOG.md) for completed changes.

## Strategy

Build **a verifiably accurate, permissively licensed library that combines
rendering, positioned text extraction, and editing in one package**.

- As of 2026-07, no mature permissive library combines all three. pymupdf is
  AGPL; [pypdfium2](https://pypdfium2-team.github.io/pypdfium2/readme.html)
  documents a volunteer support model, limited editing, and no raw PDF
  dictionary/stream/name-tree access; pikepdf deliberately excludes extraction
  and rendering; pypdf has no renderer and is slower on the published pylopdf
  extraction corpus.
- pymupdf's structural weaknesses are difficult to erase: AGPL licensing and
  [officially unsupported multithreaded use](https://pymupdf.readthedocs.io/en/latest/faq/index.html),
  including on free-threaded Python. Version 1.28.0 does publish an experimental
  Linux x86-64 cp314t wheel, so differentiate through pylopdf's explicit
  concurrency contract and five-target cp314t release matrix rather than merely
  claiming free-threaded availability. Published
  [pymupdf 1.28.0](https://pypi.org/project/pymupdf/1.28.0/) wheels are about
  17.5–24.7 MiB versus 5.0–5.8 MiB for pylopdf 0.10.0. pymupdf-layout was first
  published in 2025-11; its current
  [1.28.0 package](https://pypi.org/project/pymupdf-layout/1.28.0/) has a roughly
  39.6 MiB wheel and uses PolyForm Noncommercial plus commercial licensing.
  MIT's commercial advantage therefore remains material.
- Rust competitor pdf_oxide, started in 2025-11, releases frequently and records
  about 142,000 monthly downloads, but has no renderer and publishes
  self-reported benchmarks without third-party verification as of 2026-07-25.
  Differentiate through a real-world corpus, reproducible evidence, and upstream
  contributions.
- **oxidize-pdf** (`bzsanti/oxidizePdf`, MIT, crates.io `oxidize-pdf`) is a
  separate direct competitor. It combines parsing, generation, extraction,
  encryption, splitting, merging, and rotation in pure Rust while promoting
  structure-aware chunking for AI/RAG and is released frequently. Do not
  confuse it with pdf_oxide.
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
- Preserve **one-way data flow**. lopdf's `Document` is the sole editable source
  of truth; hayro is an immutable view for rendering and extraction. An
  unedited, unencrypted document may use its original input bytes when hayro
  accepts them and reports the same page count. Every edit invalidates that
  fast path and rebuilds hayro from lopdf serialization, so rendered output
  reflects edited and saved state. New engines must preserve this shape.
  krilla returns bytes that are imported into lopdf; engines never share mutable
  state.
- Use the supported lopdf and hayro surfaces deeply: lopdf encryption,
  `SaveOptions`, object-graph import, images, TOC, text replacement, and
  dictionary operations; hayro `Device`, `RenderSettings`, `warning_sink`, and
  immutable render/extraction snapshots. Evaluate incomplete or unstable
  surfaces such as incremental save and offset viewports before exposing them.
- Implement areas absent from lopdf through pylopdf's own dictionary operations:
  AcroForm, annotation creation, attachments, and page labels.
- Keep the core wheel small by choosing between native implementation and
  ecosystem integration. Use typst/typst-py for typesetting and new-document
  PDF/A, pyHanko for signatures, and veraPDF for PDF/A validation. Protect
  integration recipes with tests.
- Maintain the three-engine split: editing = lopdf, rendering/extraction =
  hayro, generation = krilla with HarfRust shaping. The production krilla
  configuration is `default-features = false`, without redundant raster or PDF
  import support; generated pages cross the existing lopdf Form-XObject
  boundary. Arbitrary embedded fonts, textbox layout, and AcroForm appearances
  now use this path. New-document PDF/A and eventually tagged PDF/UA remain
  gated future work rather than reasons to broaden the core prematurely.

## Release plan

Each release has one theme. Ordering follows architectural dependencies;
completed releases remain below as historical evidence.

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
      cover the recipe. At v0.8, krilla remained the future option for
      self-contained CJK `insert_text`; that path shipped in the later v0.11
      work.
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
      synchronization, and `NeedAppearances`. At v0.9, native appearance
      generation remained stage two and pylopdf's renderer did not display
      filled values; the current v0.11 work completes that stage.
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
      At that release, documented limitations included tables, multicolumn
      order, vertical writing, and some emphasis metadata. Later extraction work
      added table results, multicolumn and conservative vertical-CJK order, and
      emphasis. The current v0.11 work automatically inserts complete bordered
      tables; conservative borderless insertion remains explicit.
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

Released as v0.10.0 on 2026-07-25.

v0.10 is the pre-1.0 stabilization release, not the OCR release. It publishes
the substantial safety, performance, documentation, and link-reading work
completed after v0.9, then establishes the reusable interpretation layer needed
for deeper extraction accuracy. The release is intentionally allowed to refine
pre-1.0 APIs.

- [x] Publish the decompression-limit, object-import isolation, malformed
  input, rotated extraction, rendering, compression, documentation, benchmark,
  and `Page.get_links` changes as one coherent minor release.
- [x] Synchronize PyPI tags and GitHub Releases, enable public issue reporting, and
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
- [x] Recover an incorrect final classic `startxref` only when an intact table
  in the final revision passes a complete bounded retry. Surface recovery
  through warnings, `Document.is_repaired`, and metadata probes; normalize on
  save and refuse xref-stream guessing or previous-revision rollback.
- [x] Add artifact smoke tests that install every natively runnable wheel plus
  the sdist and exercise import, open, extraction, rendering, and save before
  publication. Cross-compiled Linux aarch64 and macOS x86_64 wheels remain
  build-only because their release runners cannot execute the target binary.
- Migrate to hayro 0.8 when released. The extensive v0.10/v0.11 layout work is
  already isolated behind bounded, owned `TextPage` and `TablePage` caches, so
  the expected `Device`/`DrawProps` migration should not change the Python API.

### v0.11 — layout, creation, and concurrency depth

v0.11 completes its implementation and validation boundary before v1.0.
Layout, creation, form appearances, typed mapping contracts, concurrency,
native OCR, and the PyEmscripten deployment path are release-ready. Publishing
the first OCR model artifact remains the prerequisite for the main v0.11 tag.
Work beyond this release keeps the same rule: no arbitrary feature-count
deadline, only accurate, measurable, coherent boundaries.

- [x] Build deterministic multicolumn reading order on `TextPage`: sustained
  whitespace gutters split line segments into recursive left-to-right columns,
  with full-width headings and footers preserved and isolated wide gaps
  rejected.
- [x] Preserve overlapping text paint runs as source-ordered logical layers
      before geometric line ordering. Exact CJK and Latin overprints, distinct
      strings at one origin, partial overlaps, and slight offsets retain each
      phrase, search result, and bbox across text, words, blocks, dict, and
      Markdown output instead of interleaving equal-position glyphs.
- [x] Add high-confidence geometry-based table extraction for complete
  axis-aligned stroked grids, with owned `TableFinder` / `Table` results,
  display-space cell bboxes, text matrices, and Markdown export.
- [x] Expose general vector paint operations through `Page.get_drawings()`.
      The hayro Device returns bounded, typed, pymupdf-style fill/stroke paths
      with display-space line/cubic geometry and normalized paint/stroke
      properties. Ten real-world first pages plus synthetic styling, rotation,
      and adversarial path-count cases cover the extraction boundary.
- [x] Add attachment-oriented
      `Document.compress_images(dpi=150, quality=75)`. Interpret every indirect
      raster placement through hayro and retain the largest reuse by
      aggregating minimum effective DPI per source axis. Atomically rewrite
      only safe, unmasked, 8-bit DeviceGray or DeviceRGB DCT or Flate streams
      to JPEG; Flate supports bounded decoding with absent or consistent PNG
      predictors. Resize with Lanczos3, use optimized Huffman coding, and skip
      non-smaller output.
      Repeat calls at the same settings are idempotent, object and decoded-pixel
      work is bounded, and the GIL is released. Synthetic placement, reuse,
      size, render, save/reopen, mask, idempotence, and hostile-input cases plus
      the redistributable WDL masked-image corpus and CPython 3.14t
      distinct-document concurrency guard the contract.
      A compact local separable Lanczos3 implementation limits the measured
      Windows abi3 wheel increase to 0.07 MiB (6.78 to 6.86 MiB); bounded
      Flate decoding raises it another 0.04 MiB to 6.90 MiB.
- [x] Extend the inspectable rule-based core to thin filled-rectangle rules and
  rectangular merged cells. Keep adversarial search bounded and reject broken
  outer grids and compact filled decorations.
- [x] Add an explicit, opt-in `strategy="text"` for borderless tables. Require
  at least three consecutive rows, stable segment counts, aligned left/right
  edges, compatible leading, and clear gutters; keep the vector-rule strategy
  as the default and document aligned multicolumn prose as an unavoidable
  ambiguity.
- [x] Add confidence diagnostics and conservative region clipping before
      considering an optional layout model. `find_tables(clip=)` uses
      rotation-resolved display coordinates and returns only complete candidate
      bboxes. Text tables expose em-normalized alignment error, minimum gutter,
      and row-gap variation plus a documented non-probabilistic ranking score;
      complete vector grids score 1.0.
- [x] Add vertical CJK extraction by assembling transformed vertical baselines
  directly and conservatively inferring hidden font WMode from CJK glyph
  geometry. Vertical columns read top-to-bottom and right-to-left between
  horizontal page furniture; synthetic Shift-JIS vertical fixtures cover the
  positive path and the Japanese business-document corpus guards against false
  classification. Ruby, warichu, and mixed-orientation typography remain
  explicit follow-up depth.
- [x] Turn the successful krilla spike into arbitrary subset-embedded OpenType
      text insertion. `insert_text(fontfile= / fontbuffer=, fontindex=)` shapes
      Unicode through HarfRust 0.12, generates in the target page's
      rotation-resolved display space, and imports through the existing
      Form-XObject boundary. Extraction, search, multiline placement, rotation,
      color, overlay order, invalid or incomplete fonts, and save round-trips
      are covered. RTL glyph shaping works, while extraction currently follows
      visual rather than logical order.
      A 4.5 MB Noto Sans JP source produces a 3.3 KB edited PDF for the test
      phrase. The Windows abi3 wheel is 5.42 MB versus 4.44 MB before krilla
      (+0.98 MB); `NOTICE.md`, PEP 639 license files, and the wheel SBOM retain
      third-party attribution.
- [x] Build `insert_textbox` on the same generation boundary. Standard 14 text
      uses Adobe AFM widths; arbitrary OpenType text uses HarfRust advances and
      krilla subsetting. Shared UAX #14 wrapping handles CJK, grapheme-safe
      emergency breaks handle overlong words, and left/center/right/justify,
      tab expansion, custom leading, rotation, overlay order, overflow
      non-mutation, missing glyphs, and save round-trips are covered.
      The Windows abi3 wheel is 5.58 MB, up 0.16 MB for canonical Core 14
      metrics plus Unicode line/grapheme tables. The completed AcroForm work
      below reuses this generation boundary.
- [x] Complete second-stage AcroForm appearance generation for text, choice,
      checkbox, and radio widgets. Standard text auto-fits with canonical
      Helvetica metrics; explicit OpenType sources and optional
      `pylopdf[cjk]` use HarfRust/krilla subsetting. Preserve non-empty authored
      button states, synthesize missing vector states, honor inherited
      alignment/multiline flags plus widget rotation/background/border, and make
      updates atomic. Comb text fields additionally honor inherited `MaxLen`,
      position Unicode graphemes individually, and reject overlength values
      atomically. A render-only state-dictionary normalization bridges hayro 0.7
      while keeping saved PDFs canonical and save/reopen-visible.
- [x] Add `Document.render_pages(workers=)` over one immutable hayro snapshot,
  with deterministic input order, a dedicated 1–64 worker pool, four-worker
  default, GIL release, and a ~512 MB estimated working-memory concurrency cap.
  Document mutation/other same-document calls from external threads remain
  outside the contract. Published scaling on 12 usrguide pages at 2x:
  1/2/4/8 workers = 400.8/200.5/118.5/83.6 ms.
- [x] Add `get_pixmap(clip=)` in rotation-resolved display coordinates with
  outward pixel rounding, page intersection, and explicit non-intersection
  errors. hayro 0.7 lacks an offset viewport, so this initially crops the
  full-page raster and retains the full-page size/cost limits; pursue an
  upstream offset viewport before claiming true region-only rendering.
- [x] Add `Pixmap.save(path)` for direct PNG output from immutable rendered
      pixels. Encoding and filesystem I/O release the GIL, strings and path-like
      objects are accepted, non-PNG extensions are rejected, and failures remain
      inside the `PdfError` hierarchy.
- [x] Build and test version-specific cp314t wheels after the mutable `Document`
      concurrency audit. Import keeps the GIL disabled; immutable Pixmaps expose
      a read-only zero-copy buffer; distinct-document extraction is tested for
      correctness and measured at 1.74x with two threads on Windows 11 /
      CPython 3.14.6t. CI runs the full suite on 3.14t, and release CI builds
      cp314t alongside abi3 on all five targets. Local Windows artifacts are
      4.43 MB for cp314t and 4.44 MB for abi3.
- [x] Replace public `dict[str, Any]` shapes with documented, runtime-importable
  `TypedDict` contracts while preserving pymupdf-style dictionaries. This also
  covers metadata inputs/results and promotes word/block/form-kind aliases to
  public runtime types.
- [x] Adopt the optional OCR track below after RTen execution, model packaging,
      memory use, multilingual accuracy, and distribution-size gates passed.

#### Optional OCR track for v0.11 — `pylopdf[ocr]`

The gate is **go** as of 2026-07-25. “pip-only, no shared libraries,
permissively licensed Japanese OCR” remains an ecosystem gap: pymupdf integrates
the Tesseract engine but still requires
[external language data (`tessdata`) and configuration](https://pymupdf.readthedocs.io/en/latest/installation.html#enabling-integrated-ocr-support),
pponnxcr is AGPL, and rapidocr depends on the C++ onnxruntime. pylopdf now fills
that gap without broadening the mandatory Python dependency set.

- [x] Statically link RTen 0.24, a pure-Rust runtime under MIT/Apache-2.0, with
      only its native RTen-format reader. The ONNX parser remains outside the
      core wheel. Model loading and inference release the GIL, use a dedicated
      bounded thread pool, and share immutable Pixmap storage without copying.
      The Windows abi3 wheel is 7.06 MB versus 5.42 MB for v0.10.0, a 1.64 MB
      increase for the offline inference engine.
- [x] Select PP-OCRv6 small rather than the earlier v5 mobile candidate. The
      unified detector and recognizer cover 50 languages including Japanese,
      Simplified and Traditional Chinese, and English. RTen output matched ONNX
      Runtime numerically (detector maximum difference 0; recognizer about
      3e-6). On the tracked 1,188-character MHLW fixture, the native pipeline
      measured 3.788% / 3.704% whitespace-stripped strict CER and 0.842% /
      0.842% after NFKC at 150 / 300 dpi. The RapidOCR v6 reference measured
      0.926% / 0.758% after NFKC, exposing both the 150-dpi win and 300-dpi loss.
- [x] Publish model data through a separately versioned
      `pylopdf-ocr-models` wheel, following the font-wheel pattern. It contains
      deterministic RTen conversions plus the multilingual dictionary, with
      pinned source URLs, source and artifact SHA-256 values, conversion
      commands, Apache-2.0 licensing, wheel smoke tests, SBOM generation,
      provenance attestations, and an immutable GitHub release workflow. The
      wheel is 26.6 MB; the two uncompressed RTen models total about 31.2 MB.
- [x] Add reusable `OcrEngine`, `Page.get_text_ocr`, and `Page.apply_ocr`.
      Results are typed `OcrWord` mappings in rotation-resolved display
      coordinates. Applying OCR retains visual pixels and inserts the existing
      invisible searchable CID layer. Pages with extractable text are skipped
      by default, making repeated application idempotent. Display-coordinate
      region clips let mixed-content pages recognize scanned areas without
      duplicating digital text; callers can still opt into appending.
- [x] Keep full-page detector memory bounded with overlapping tiles and a
      4,096-candidate safety cap. The default 1,408-pixel tile with 192-pixel
      overlap uses six tiles for a 300-dpi A4 page and measured about 419 MiB
      peak child-process memory; 1,280 measured about 369 MiB and 1,536 about
      475 MiB. Each engine now admits one complete render-and-recognize call by
      default, preventing accidental outer concurrency from multiplying that
      measured peak; `max_concurrent` can be raised through 16 only after
      workload measurement. Sustained whitespace gutters retain deterministic
      left-to-right multicolumn reading order after edge-duplicate merging.
- [x] Reject premature quantization. Full int8 Conv/MatMul quantization broke
      detection and recognition; recognizer MatMul-only quantization reduced
      size but worsened NFKC CER from 0.842% to 1.010%. The first release
      keeps the f32 models and prioritizes accuracy.
- [x] Prerequisite 1, Japanese accuracy measurement, completed 2026-07-23:
      **go**. At 300 dpi, the PP-OCRv5 mobile Chinese model, which covers Chinese,
      Japanese, and English and has no separate v5 Japanese recognizer, measured
      4.0% strict CER and 1.3% after NFKC on five synthetic cases plus one MHLW
      document with 2,428 ground-truth characters. Kanji, kana, and digits were
      nearly perfect. Remaining differences were width folding and symbols such
      as circled numbers, postal marks, and reference marks. It beat the v4
      Japanese-specific model in practical accuracy and trailed the server model
      by only 0.5 points.
- [x] Prerequisite 2: prove RTen execution and choose the stronger PP-OCRv6
      small models through numerical, synthetic, Japanese real-document,
      memory, and artifact-size measurements.
- The first native engine deliberately exposes axis-aligned boxes and no
  automatic page-orientation classifier. Explicit `rotation=90 / 180 / 270`
  correction now turns OCR input clockwise, maps boxes back to the original
  display space, and writes an orientation-aware invisible layer without
  changing PDF page rotation. Arbitrary skew, automatic sideways-page
  detection, ruby, warichu, and mixed-orientation typography remain explicit
  depth. Use ocrs-cjk (MIT/Apache) as a reference, not a dependency.
- [ ] Register the first `pylopdf-ocr-models` PyPI Trusted Publisher and publish
      model v0.1.0 before the main v0.11 release.
- [x] Extend field validation to a licensed, image-only Japanese archival scan
      with 384 manually verified characters. It measured 1.823% / 1.302% strict
      CER and 1.562% / 1.042% NFKC CER at 150 / 300 dpi. A shared-engine,
      two-document 150-dpi check exactly matched sequential text at both
      admission limits, while `max_concurrent=1` took 6.31s and 2 took 6.75s.
      The higher limit did not improve this four-thread workload and still owns
      separate buffers, reinforcing the conservative default of 1.

### v1.0 — product-quality declaration of trust

Target no earlier than 2026-08. v1.0 is not a calendar-driven promotion of the
current API. It follows v0.10 and v0.11 field use and ships only after the
library's product experience, error recovery, documentation, performance, and
known-limit behavior are polished together.

- [x] Publish the semantic-versioning and deprecation policy before the final
      freeze. The EN/JA/zh-CN/KO contract defines the public boundary, typed
      mappings, behavioral corrections, runtime changes, and a post-v1.0
      two-minor-and-six-month deprecation window. A deterministic 0.11 candidate
      snapshot now reviews exports, signatures, members, mapping keys, aliases,
      constants, and exception inheritance on every native Python test lane.
- Freeze the v1.0 API only after 0.11 field use and the remaining limitation
  review; the snapshot is a review gate, not a premature compatibility claim.
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
- [x] Publish an explicit support and concurrency contract covering GIL-enabled,
      free-threaded, single-document, and multi-document use, plus the supported
      `render_pages` boundary and immutable Pixmap buffer behavior.
- Validate installation and core workflows from every published wheel and the
  sdist, and publish release provenance alongside the artifacts.
- Review every documented limitation. Improve high-value limits before release;
  keep only those backed by a clear architectural or ecosystem boundary.
- [x] Bound `Page.get_images()` output amplification per page across placement
      count, cumulative source pixels, returned payload bytes, and the
      Flate-to-DCT fast path. Repeated reuse now fails atomically instead of
      multiplying one source into unbounded Python-owned byte strings.
- [x] Bound attachment retrieval before Python-byte materialization. The public
      `embfile_get(max_size=64 MiB)` default caps every decoder layer, uses a
      stable `embedded_file_size` rejection code, and requires an explicit
      opt-out for unbounded reads. Name-tree traversal now borrows direct object
      shapes, visits cycles once, and rejects excessive entries, nodes, depth,
      or name bytes; failed additions cannot create an unreadable tree.
- [x] Align normal text generation with the optional CJK product experience:
      `insert_text` and `insert_textbox` now auto-select the JP-subset sans or
      serif font for Japanese/Han input when `pylopdf[cjk]` is installed,
      without weakening the single-font, no-per-glyph-fallback boundary.
      Hangul and locale-specific Chinese typography remain explicit-font cases
      until the data package has measured pan-CJK coverage.
- [x] Remove the Pillow/PNG round-trip from rendered-image reuse:
      `Page.insert_image(pixmap=)` now converts immutable straight-alpha RGBA8
      storage directly into a Flate-compressed PDF Image XObject, preserves
      transparency, and omits the soft mask for fully opaque input. Its
      `rotate=` option removes another preprocessing step for all JPEG, PNG, and
      Pixmap sources, with clockwise right-angle rotation composed in display
      space and the rotated aspect ratio retained.
- [x] Remove the serialize/open workaround from same-document page placement.
      `show_pdf_page` now clones the current lopdf graph under the released GIL
      and imports from that pre-edit snapshot, supporting both another page and
      the target page itself without aliasing mutable state.
- [x] Translate runtime errors and warnings to English before API freeze
      (2026-07-24, about 100 Rust/Python messages plus tests).
- [x] Make English canonical for repository documentation, comments, docstrings,
      automation, and future commit messages. Localized docs and CJK fixtures are
      the only exceptions (2026-07-24).
- [x] Add `SECURITY.md` with a private-reporting path, untrusted-PDF guidance,
      and `max_decompressed_size`, plus cargo-audit in CI. pip-audit is omitted
      because the core package has no mandatory Python dependencies.

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
      dependencies are shared with hayro. Production integration is now
      complete with HarfRust replacing that spike-only shaping feature: the
      abi3 wheel grew by 0.98 MB, and generated text is imported into lopdf as
      a Form XObject. `insert_textbox` now reuses this boundary.
- [x] Add `get_pixmap(clip=)` by cropping the full-page raster with exact
      rotation-resolved display-coordinate semantics. hayro `RenderSettings`
      still supports only an origin-fixed viewport, so true region-only
      rendering remains an upstream contribution candidate.
- [x] Cache extraction results by generation in bounded TextPage and TablePage
      caches, eliminating repeated interpretation in search/extract and
      repeated table workflows while keeping table-only geometry off the common
      text path.
- [x] Add `Document.render_pages(workers=)` as the supported same-document
      parallel rendering boundary; measured at 2.00x / 3.38x / 4.80x for
      two/four/eight workers in the published benchmark. Its
      Python-orchestration value grows further with cp314t.
- Keep annotation/widget dict and tuple APIs until mutation grows enough to
  justify objects. Do not copy pymupdf's heavyweight `Annot`.

### Parallel work, not tied to releases

- [x] Add a real-document-derived incorrect-classic-`startxref` regression with
  bounded recovery, save normalization, and xref-stream/rollback refusal.
- Expand the corpus with other damaged PDFs such as truncated xref tables,
  Type 3 fonts, JPX, transparency groups, and annotations/links.
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
- [x] Produce a reproducible Pyodide 0.28.3/emscripten wheel (2026-07-26).
  The builder checks the static legacy
  `cp310-abi3-pyodide_2025_0_wasm32` artifact with ordinary
  `micropip.install()` in the pinned runtime, then deterministically retags the
  same binary as the PEP 783
  `cp310-abi3-pyemscripten_2025_0_wasm32` artifact accepted by PyPI. It imports
  without wasm-bindgen shims, preserves Python exceptions through the
  WebAssembly exception tag, and passes byte-stream open, extraction,
  rendering, malformed-input recovery, and post-error reuse checks. The
  builder pins and verifies the complete native toolchain and hashed Python
  build environment.
- [x] Automate PyEmscripten wheel CI and release gates (2026-07-26).
  Pull requests build the wheel, run the pinned Pyodide smoke suite, verify its
  PEP 783 metadata, and dry-run a Cloudflare Workers bundle with pinned
  `workers-py` and Wrangler versions. Tagged releases attach build provenance,
  include the artifact in the PyPI upload and SBOM, then resolve it back from
  PyPI and repeat the Cloudflare dry run before creating the immutable GitHub
  release. Actual PyPI publication remains pending the next package release;
  compatibility breadth, resource-limit tests, size investigation, and user
  documentation continue in #20 through #23 under the #24 epic.
- [x] Establish the native/Pyodide functional compatibility matrix (2026-07-26).
  One shared suite now checks bytes-only input, PDF 2.0, text and document
  Markdown, embedded and vertical CJK, multicolumn order, bordered/borderless
  and rotated tables, vector paths, image-only input, AES-256 authentication,
  generation with subset-embedded OpenType, textbox layout, pixmaps, ordered
  serial-fallback batch rendering, virtual-filesystem save, merge/select, and
  typed error recovery. The Python 3.10 CI lane records a stable native result
  artifact; the pinned Pyodide lane must match it exactly while also satisfying
  explicit structure and content assertions. Four localized documentation
  pages distinguish the tested Cloudflare path, runtime-level Pyodide
  compatibility, unsupported direct PyPI installation through Pyodide 0.28.3's
  pre-PEP-783 `micropip`, and out-of-scope OCR/fallback-font behavior.
- [x] Establish a shared untrusted-PDF resource policy (2026-07-26).
  `DocumentLimits` bounds file bytes, pages, indirect objects, direct nesting,
  per-stream and cumulative decompression, page-content decompression, and
  cumulative interpreted glyph bytes. `DocumentLimits.web()` encodes the
  initial bounded-worker profile without rejecting a representative scanned
  page under the page-content budget. `LimitError.code` gives stable
  machine-readable rejection categories, while `Document.complexity` exposes
  cheap facts before rendering or extraction. Native and Pyodide share
  file/page/decompression/depth/unverifiable-filter/text regressions, reference
  cycles, representative vector and scan inputs, and a reproducible timing
  trend recorded in `bench/results/limits-latest.md`. Scheduled Atheris fuzzing
  generates bad xrefs, broken streams, deep direct objects, cycles, excessive
  pages, and Flate/RunLength bombs. CPU
  deadlines remain an explicit host responsibility; application-level
  parallelism must retain the library's bounded admissions.
- [x] Measure and refine the PyEmscripten distribution (2026-07-26).
  Emscripten now omits the unsupported RTen inference runtime while preserving
  an explicit `OcrError` capability boundary and external OCR text-layer
  insertion. The pinned wheel fell from 4.522 to 3.834 MiB (-15.21%), the Wasm
  code section fell 21.92%, and the tested Cloudflare gzip upload fell from
  4.570 to 3.882 MiB. CI records machine-readable artifact sections, staged
  Pyodide startup/workload timings, linear-memory checkpoints, and Wrangler
  bundle sizes. The complete core fits Cloudflare's paid-plan limits but not
  its Free compressed-size limit; removing coherent PDF features for the Free
  tier would fragment the API without establishing a credible workload, so no
  separate lightweight distribution is planned.
- [x] Publish the end-to-end WebAssembly installation and operations guide
  (2026-07-26). A tested `examples/cloudflare-worker` is the source used by CI
  and documents a bounded extraction service. Four locale guides record the
  exact Python/Pyodide/Emscripten/PyEmscripten/workers-py/Wrangler matrix,
  Cloudflare and direct-Pyodide workflows, dependency-to-capability ownership,
  OCR/threads/filesystem/rendering boundaries, resource policy, size/startup
  evidence, and the release support checklist. The public PyEmscripten
  artifact first ships in v0.11; v0.10.0 remains native-only.
- [x] Integrate detected tables into `Document.to_markdown()`: bordered grids
  are automatic, borderless candidates remain opt-in, table text is suppressed
  from prose and heading inference, and reading order is covered at all four
  right-angle rotations plus the public-domain IRS Form 1040 corpus.
- [x] Expand independent table corpora and quality evaluation beyond synthetic
  and IRS coverage. Public-domain FBI NICS and US Senate fixtures now protect
  sparse internal rules, dense numeric records, merged headers, borderless
  bodies, and all four right-angle reading orientations (2026-07-25).

## Watchlist

- **hayro 0.8**: the DrawProps `Device` API change (#1245) is merged but
  unreleased, as are #1318 and #1320. When released, update the two `extract.rs`
  implementations—likely mechanically because paint is mostly ignored—and gain
  the performance improvements. Keep this separate from krilla integration.
- **fulgur**, Blitz plus krilla for HTML-to-PDF under MIT/Apache-2.0, already
  supports `@page`, page breaks, running headers/footers, and tagged PDF/UA-1,
  but first appeared in 2026-03, is single-maintainer, is at 24.1% css-page WPT,
  and is changing APIs rapidly. Reassess around 2027-01 for survival, API
  stability, and a stable Blitz 0.3. pyfulgur currently stops at cp312 and is
  not abi3, leaving an opportunity.
- **underskrift**, BSD-2-Clause PAdES signing over lopdf by kushaldas, appeared
  in 2026-03 and claims B-B through LTA. Reconsider as an optional signature
  backend after maturity and lopdf-version alignment.
- **PP-OCRv6**, released in 2026-06: wait for ONNX conversion support before
  selecting the `[ocr]` model generation.
- **parley**, linebender's richer text layout engine and a krilla dev
  dependency: UAX #14 plus HarfRust metrics was sufficient for the bounded
  `insert_textbox` contract without adding per-glyph fallback and styling
  machinery. Reconsider only if product demand justifies a native rich-text
  surface.
- **PP-DocLayout**, Apache-2.0: possible `[layout]` alternative to the
  PolyForm-Noncommercial pymupdf-layout. It could share rten with `[ocr]`;
  evaluate after OCR succeeds.
- **Incremental save**: reconsider after real issue demand or stabilization in
  pypdf. The implementation path would retain original bytes at load, reparse
  them at save, and append only changed objects through lopdf
  `IncrementalDocument`, initially excluding encrypted documents.

## Explicit non-goals

These boundaries preserve focus. The 2026-07-23 deeper review updated the
evidence; built-in OCR moved out of this list into the gated v0.11 track.

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
- **Bundling typst or another typesetter**: current typst wheels add about
  25–35 MB and break the lightweight goal. Integrate externally. The maximum
  future native typesetting scope is text flow into a rectangle, similar to
  pymupdf `insert_htmlbox`.

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
- **pymupdf 1.28** remains AGPL. **pymupdf-layout** was first published in
  2025-11; version 1.28.0 provides a GNN layout analyzer used by pymupdf4llm
  when separately installed and enabled. It uses PolyForm Noncommercial plus
  commercial licensing, ships a roughly 39.6 MiB wheel, and depends on NumPy,
  ONNX Runtime, NetworkX, PyYAML, and the matching pymupdf release.
- **pdf_oxide** records weekly releases, about 145,000 monthly downloads, and
  899 stars, but no renderer and no third-party benchmark verification.
