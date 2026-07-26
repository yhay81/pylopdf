# AGENTS.md

This file is the canonical development context for coding agents.
`CLAUDE.md` only imports this file; update this file instead.

pylopdf is a published Python library for PDF editing, rendering, extraction,
and generation, implemented in Rust. Editing is powered by
[lopdf](https://github.com/J-F-Liu/lopdf), rendering and extraction by
[hayro](https://github.com/LaurenzV/hayro), and generated text and form
appearances by [krilla](https://github.com/LaurenzV/krilla) plus HarfRust. Its
API is inspired by pymupdf. See [README.md](README.md) for the concept and API
overview.

## Working conventions

- Commit directly to `main` and push after each coherent unit of work. Do not use
  feature branches.
- Write commit messages, code comments, docstrings, repository documentation,
  configuration comments, and user-facing messages in English.
- Non-English text is allowed only in localized documentation and data required
  to test Unicode or CJK behavior.
- Do not place experiments unrelated to PDF processing in this repository.

## Development commands

- `uv sync` — build the extension and install dependencies. Rust changes are
  included in uv's rebuild cache keys.
- `uv run pytest` / `uv run ruff check .` / `uv run mypy src tests`
- `uv sync --group bench && uv run python bench/run.py` — run reproducible
  benchmarks. Results are written to `bench/results/latest.md`; publish wins and
  losses together.
- `uv sync --all-extras && uv run python bench/ocr.py` — reproduce native OCR
  strict/NFKC CER and elapsed time on two licensed Japanese fixtures, plus a
  bounded shared-engine concurrency check. Results are written to
  `bench/results/ocr-latest.md`.
- `uv sync --group docs && uv run zensical serve -f mkdocs.yml` — preview the
  English documentation with Zensical. Locale configurations are
  `mkdocs.ja.yml`, `mkdocs.zh-cn.yml`, and `mkdocs.ko.yml`. To reproduce the
  production validation, build all four configurations in EN → JA → zh-CN → KO
  order with `uv run --no-sync zensical build -f <config> -c -s`. A push to
  `main` deploys the site through `docs.yml`.
- `cargo clippy --manifest-path rust/Cargo.toml --all-targets` /
  `cargo fmt --manifest-path rust/Cargo.toml`
- Do not add Rust unit tests. Verify all behavior through Python tests in
  `tests/`.
- Real-world PDF regressions belong in `tests/test_real_world.py`. Record corpus
  sources, licenses, and known limitations in
  `tests/assets/real_world/README.md`, and bundle only redistributable files.

## Architecture and invariants

- `_Document` (`rust/src/document.rs`) is a thin conversion and error-mapping
  layer. The Python `Document` (`src/pylopdf/__init__.py`) owns validation,
  zero-/one-based conversion, and closed-state handling.
- Python API page numbers are zero-based; Rust/lopdf page numbers are one-based.
  Keep the conversion centralized in `_lopdf_page_number`.
- `merge` and `select` must materialize inherited page attributes (`Resources`,
  `MediaBox`, `CropBox`, `Rotate`) into page dictionaries because lopdf does not
  resolve page attribute inheritance.
- Text extraction is implemented as a hayro `Device` in `rust/src/extract.rs`.
  It collects glyph Unicode and positions, then assembles lines
  (`LINE_TOLERANCE`), words (`WORD_GAP`), and blocks (`BLOCK_GAP`).
  `get_text("words"/"blocks"/"dict")` and `search_for` share the same glyph
  collection through a bounded, generation-invalidated `TextPage` cache. CJK
  fallback configuration also applies to extraction, including invisible OCR
  text. Hayro normalizes glyph space to 1000 upem, so font size is the transform
  factor × 1000. Vertical bboxes approximate baseline ± a size ratio.
  Overlapping paint runs on one baseline are split into source-order logical
  layers before inline geometry sorting; preserve distinct overprints rather
  than interleaving or deduplicating their glyphs.
  Sustained whitespace gutters split same-baseline segments into recursive
  left-to-right columns; full-width headings and footers remain outside the
  column regions, and isolated wide gaps stay on one line. `find_tables` uses a
  separate bounded, generation-invalidated `TablePage` cache so normal text
  extraction does not collect or analyze vector rules. It collects at most
  4096 axis-aligned candidates from strokes or thin filled polygons. A table
  requires a connected outer grid with at least two rows and columns.
  Rectangular merged cells are tiled from missing internal dividers; covered
  row-major slots are `None`, with an internal anchor map retained for exact
  Markdown span expansion. Coarse grid spans gain synthetic dividers only when
  at least three evenly led physical lines occupy half the cross-axis slots and
  adjacent slot signatures overlap by at least 0.8. This inference is symmetric
  across right-angle rotations; hybrid grids score 0.95 while complete vector
  grids score 1.0. Materialization is capped at 4096 slots and merged-span
  searches at 65,536 candidates. The opt-in borderless
  `strategy="text"` requires at least three consecutive physical rows with the
  same segment count, aligned left or right edges, compatible leading, and
  clear gaps. It intentionally does not run as the default because aligned
  multicolumn prose is geometrically ambiguous. `find_tables(clip=)` returns
  only complete candidate bboxes inside the display-coordinate region; it does
  not synthesize partial tables or reduce the cached full-page interpretation
  cost. `TableDiagnostics.confidence` is a deterministic ranking heuristic,
  not a probability. Text diagnostics retain em-normalized alignment error,
  minimum gutter, and row-gap variation.
  `Document.to_markdown()` inserts complete bordered tables by default and
  accepts `table_strategy="text"` for conservative non-overlapping borderless
  candidates or `None` to disable table conversion. Document conversion stops
  page iterable materialization at 4,096 entries and defaults to a 64 MiB
  cumulative UTF-8 output cap. It uses a page-at-a-time heading-count pass and
  a page-at-a-time rendering pass rather than retaining every page layout,
  table, and word list together. It removes contained text
  from prose and heading inference while retaining words outside a table on the
  same physical line, and normalizes physical table matrices to the dominant
  logical text direction on right-angle rotations. Merged spans expand from the
  internal anchor map rather than guessing from adjacent empty slots.
  Extraction coordinates use the same display space as rendering by passing
  `initial_transform(true)` to the context, resolving page rotation and CropBox
  offsets. Baseline direction is retained and exposed in line dicts. Rotated
  baselines assemble along their direction while remaining writing mode 0.
  Uniform axis-aligned pages derive logical inline and block axes from that
  direction, so 90/180/270-degree lines and sustained columns preserve reading
  order. Explicitly rotated glyph bboxes follow the baseline and cross-axis;
  inferred mode-1 CJK keeps its conservative upright-glyph approximation.
  Because hayro does not expose font WMode, mode-1 CJK lines are inferred only
  from conservative single-glyph vertical chains: top-to-bottom within a line,
  right-to-left across columns, with horizontal headings and footers preserved.
  Ruby, warichu, and mixed-orientation typography are not interpreted.
- `Page.get_drawings` uses a separate hayro Device and releases the GIL. It
  returns interpreted fill/stroke paint operations as pymupdf-style
  `DrawingInfo` mappings in rotation-resolved display coordinates. Commands are
  self-contained lines or cubics; quadratics convert exactly to cubics, and
  adjacent fill/stroke callbacks for one PDF operator combine as `type="fs"`.
  Solid paints expose normalized RGB and opacity; patterns retain geometry with
  `None` paint values. Clipping/group/soft-mask structure and clip-resolved
  visibility, optional-content layer names, text, images, and annotations are
  outside this path API. Optional-content visibility is still applied. Reject
  output above 8,192 paths or 131,072 commands rather than returning a partial
  result.
- `Page.get_images` releases the GIL and materializes each drawn placement as
  JPEG passthrough or PNG. Reject the complete result above 4,096 placements,
  64,000,000 cumulative source pixels, or 64 MiB of encoded payloads per page.
  Bound Flate-to-DCT passthrough decompression to the remaining byte budget;
  never return a partial list.
- `Document.embfile_get` releases the GIL and defaults to a 64 MiB decoded-size
  limit applied to every filter layer. `max_size=None` is the explicit
  unbounded opt-out; limit failures use `LimitError.code ==
  "embedded_file_size"`, while malformed or unsupported filters must not fall
  back to encoded bytes. EmbeddedFiles name-tree traversal borrows direct
  shapes, visits indirect cycles once, and rejects more than 4,096 entries or
  nodes, 32 levels, or 1 MiB of encoded/decoded names. Attachment edits must not
  create an over-limit tree or invalidate caches after a failed operation.
  They preflight the Catalog write target rather than cloning the whole
  Document for rollback, cap new key/filename/description input at 1 MiB, and
  validate inline FileSpecs before cloning at 4,096 direct objects, 32 levels,
  and 1 MiB of direct string/name/stream data. Indirect references are leaves.
- `Document.compress_images` interprets indirect raster XObject placements
  through a separate hayro Device and aggregates the minimum effective DPI per
  source axis, so a reused image retains enough pixels for its largest
  placement. It atomically edits a lopdf clone, releases the GIL, and rewrites
  only direct, single-filter, 8-bit DeviceGray or DeviceRGB DCT/Flate streams
  without masks or custom decode arrays. DCT decode parameters are excluded;
  Flate accepts no predictor or consistent PNG predictor parameters through
  lopdf's bounded decoder. Strict zune-jpeg decoding, Lanczos3 resizing, and
  jpeg-encoder optimized Huffman coding are used, and a candidate is committed
  only when its encoded payload becomes smaller. The
  private `/PylopdfQuality` marker prevents repeat calls at the same or higher
  quality from introducing generational loss when dimensions are unchanged.
  Actual rewrites invalidate hayro and derived interpretation caches without
  making existing `Page` views stale; no-op calls preserve all caches. Reject
  more than 16,384 unique indirect raster objects or more than 250 million
  eligible decoded source pixels in one operation, and skip an individual
  source above 64 million pixels. The compact local separable Lanczos3
  implementation avoids a general-purpose resizing dependency. On Windows
  abi3, the wheel measured 6.86 MiB versus 6.78 MiB at the preceding v0.11
  commit (+0.07 MiB); extending the same path to bounded Flate decoding
  measured 6.90 MiB (+0.04 MiB).
- Native OCR uses RTen 0.24 with only its `rten_format` feature. The core wheel
  contains the pure-Rust inference engine; PP-OCRv6 small detector,
  recognizer, and dictionary data come from the independently versioned
  `pylopdf-ocr-models` package through the `[ocr]` extra. `OcrEngine` loads one
  immutable model set and owns a dedicated 1–16 thread pool. Loading and
  inference release the GIL. OCR clones the Pixmap's `Arc<[u8]>`, composites
  RGBA onto white, and uses overlapping detector tiles bounded to 256–2048
  pixels, a 4096-candidate cap, and deterministic edge deduplication. The
  default 1408-pixel tile and 192-pixel overlap measured about 419 MiB peak on
  a 300-dpi A4 page. Recognizer class count must match dictionary length + 2.
  Results use rotation-resolved display coordinates and recursive sustained-
  gutter column order. One immutable engine can serve calls on distinct
  Documents, including CPython 3.14t. A Python admission semaphore covers the
  complete render-and-recognize operation; `max_concurrent=1` is the
  memory-safe default and values through 16 require workload measurement.
  Every admitted call owns raster and inference buffers. A two-document,
  four-thread field check at 150 dpi exactly matched sequential output, while
  admission limits 1 and 2 took 6.31s and 6.75s respectively, reinforcing the
  default of 1. Same-Document restrictions still apply. `Page.apply_ocr` skips
  pages with extractable text by default so repeated runs are idempotent. With
  `clip=`, only intersecting text triggers the skip and result boxes remain in
  full-page display coordinates. Clipping reduces OCR detector input but not
  hayro's current full-page rendering cost. The first engine returns
  axis-aligned boxes only;
  `rotation=90 / 180 / 270` turns the rendered OCR input clockwise, maps boxes
  back to the unmodified display space, and makes `apply_ocr` orient its
  invisible baseline accordingly. Nonzero rotation temporarily adds one RGBA
  raster copy inside the complete-call admission limit. Arbitrary skew,
  automatic page orientation, ruby, warichu, and mixed-orientation typography
  are not interpreted.
- `Page.insert_ocr_text_layer` stops iterable materialization at 4,096
  non-empty words or 1 MiB of aggregate UTF-8 text per call. The Rust boundary
  repeats both checks, CID assignment stops before a 65,535th distinct
  character, and CID maps, ToUnicode data, and content operators are prepared
  before PDF mutation. Input rejection preserves caches, but invalidate
  immediately before the first PDF mutation because later malformed-resource
  errors can leave a partial edit.
- Rendering caches a hayro snapshot in `_Document.hayro_pdf`. An unedited,
  unencrypted load first consumes its original input bytes and falls back to a
  lopdf serialization only when hayro rejects them or reports a different page
  count. Editing methods must call `invalidate_hayro_pdf`, which also discards
  the original-byte fast path; edited state must always be reflected in
  rendering.
- `Document.render_pages` is the supported same-document concurrency boundary:
  it renders an immutable hayro snapshot on a dedicated rayon pool, preserves
  input order, releases the GIL, accepts 1–64 requested workers, and caps actual
  concurrency to roughly 512 MB of estimated raster and conversion buffers.
  One call accepts at most 4,096 page entries. Completed PNGs atomically share
  the default 512 MiB cumulative encoded-output budget across serial, rayon,
  and PyEmscripten execution; `max_size=None` is the explicit unbounded opt-out
  and limit failures return no partial list. Other simultaneous calls or edits
  on the same `Document` are outside the contract.
- `Document.render_page_svg` and `Page.render_svg` default to a 64 MiB UTF-8
  output cap and raise `LimitError` with code `svg_output_size` before PyO3
  creates the Python string; `max_size=None` explicitly opts out. hayro-svg 0.7
  returns only a completed `String`, so this boundary does not cap the
  converter's one internal Rust allocation.
- `Page.get_pixmap(clip=)` accepts rotation-resolved display coordinates,
  intersects them with the page, and rounds outward to pixel boundaries.
  hayro 0.7 lacks an offset viewport, so clipping crops a full-page raster and
  does not relax the full-page render-size limits.
- Release the GIL with `Python::detach` for heavy operations: load, save, render,
  extraction, merge, and compression.
- `Page` is a lightweight view of a `Document` plus a generation number.
  Python methods that change page structure must call `_bump_generation()`.
  Otherwise an old `Page` could silently refer to a different page. Old pages
  must raise `StalePageError` after structural changes.
- `delete_pages`, `select`, and `insert_pdf` accept at most 4,096 page entries
  per call in both Python and Rust. Iterable materialization stops at the
  4,097th item, and range insertion checks its size before allocation or graph
  import. `delete_pages([])` is a true no-op: do not invalidate caches or bump
  the Python generation.
- Rust defines `PdfError` (a `ValueError`-compatible base) and `PasswordError`;
  Python defines `DocumentClosedError`, `EncryptedDocumentError`, and
  `StalePageError`. Add new errors under the `PdfError` hierarchy instead of
  introducing plain `ValueError` exceptions.
- Encryption during `save` operates on a clone, so the in-memory document always
  remains plaintext. Python generates the key with `os.urandom(32)`.
- `Document.tobytes` defaults to a 512 MiB serialized PDF output boundary.
  Normal, object/xref-stream, and encrypted core paths must all write through
  `BoundedPdfOutput`, which refuses the write crossing the limit before Python
  bytes conversion and raises stable code `pdf_output_size`. `max_size=None`
  is the explicit trusted-input opt-out. File `save` remains streamed and
  outside this in-memory output boundary. Preserve the documented mutation
  semantics of `garbage`, `deflate`, and `object_streams` on output refusal.
- TOC page numbers in `get_toc` and `set_toc` are one-based for pymupdf
  compatibility. All other page APIs are zero-based.
- lopdf automatically decrypts PDFs with an empty user password. Other encrypted
  PDFs require the `password` argument or `authenticate()`, which reopens the
  document with a password. `_ensure_open` must check `is_encrypted` because an
  undecrypted document otherwise appears to have zero pages.
- Lenient opening repairs only an incorrect final `startxref` that points away
  from an intact classic xref table in the final revision. The bounded linear
  scan requires the final `%%EOF`, `startxref`, classic header/entry, and
  `trailer`; it never guesses objects, repairs xref streams, or falls back
  across an earlier `%%EOF`. A full lopdf retry under the original password and
  decompression limits remains authoritative. Repaired bytes feed hayro,
  `PylopdfWarning` makes the event visible, `Document.is_repaired` and
  `MetadataProbe.repaired` retain it, and saving normalizes the xref data.
- CJK fallback replaces hayro's `font_resolver`
  (`pick_cjk_fallback` in `rust/src/document.rs`). Detect CJK through
  `CIDSystemInfo` or the `BaseFont` name. Serif-like names use the serif slot;
  other names use sans. Font files come from
  `fonts/pylopdf-fonts-cjk/`, an uv workspace member exposed through the `[cjk]`
  extra and auto-detected during rendering.
- Drawing (`rust/src/draw.rs`) appends streams to `/Contents` without
  re-encoding existing content. Existing arrays are wrapped in `q/Q` only once.
  `_Document.isolated_content_pages` retains verified page IDs after later
  overlays or underlays; do not trust a persistent PDF marker from untrusted
  input. Initial leading/trailing sentinel detection borrows stream bytes and
  never clones complete page-content streams. Before cache invalidation, input
  decoding, or dependent object creation, drawing calls reject raw arrays above
  4,096 entries, reference chains above 32 levels or with cycles, and any
  insertion that would take the final array above 4,096 stream references.
  Inputs use display coordinates with a top-left origin and page rotation
  resolved, then convert to `cm`/`Tm`. `insert_image(pixmap=)` splits immutable
  straight-alpha RGBA8 storage directly into Flate-compressed RGB plus an
  optional soft mask; fully opaque Pixmaps must not create a mask.
  `insert_image(rotate=)` rotates every source clockwise in normalized
  right-angle steps, swaps the aspect ratio for 90/270, and composes with target
  page rotation in display space. Same-document `show_pdf_page` must clone the
  lopdf graph before importing the source Form XObject so the target page can
  safely source itself without serialization or mutable aliasing. Annotations
  must always include an appearance stream at `AP /N`, because hayro does not
  render annotations without one. `render_annotations` defaults to true.
- Simple-font text replacement is prepared in `rust/src/text_replace.rs`.
  Search/replacement/fallback input is capped at 4,096 UTF-8 bytes. Decoded
  page content, aggregate font encoding data, intermediate growth, and the
  final content stream share the public `max_size` boundary. Re-encoding uses
  a calculated upper bound before allocation and linear per-character
  fallback. Commit one new page-owned stream only after all fallible work
  succeeds so copied pages do not mutate shared `/Contents`; no-match and
  failure paths must preserve document bytes and caches. A successful
  replacement materializes inherited page attributes and clears the drawing
  isolation marker for that page.
- Embedded-font text generation lives in `rust/src/generate.rs`. krilla is
  pinned to 0.8.2 with all default features disabled; HarfRust 0.12 supplies
  shaping without krilla's unmaintained rustybuzz/ttf-parser path. Raster and
  PDF-import features remain disabled. Generation creates a transparent page
  in target display coordinates, subset-embeds the selected OpenType face, and
  returns bytes that the existing lopdf Form-XObject path imports. It releases
  the GIL and rejects missing glyphs. Without an explicit font source,
  Japanese/Han text auto-selects the optional `pylopdf[cjk]` JP-subset sans
  font, or serif for Times aliases. This selects one font for the complete run;
  it is not per-glyph fallback. Hangul, locale-specific Chinese typography, and
  other scripts need an explicit font. Paragraph layout remains outside
  `insert_text`. RTL shapes render, but extraction currently follows visual
  order.
  Keep third-party acknowledgements in `NOTICE.md` and include both license
  files through PEP 639. The Windows abi3 wheel measured 5.42 MB after
  integration, up from 4.44 MB.
- `Page.insert_textbox` completes layout before mutating the PDF and returns
  negative spare height without drawing on overflow. Standard 14 measurement
  uses canonical Adobe AFM widths; embedded OpenType measurement uses HarfRust
  advances and font vertical metrics. Both share greedy UAX #14 wrapping in
  `rust/src/layout.rs`, with grapheme-safe emergency breaks and soft-line-only
  justification. Keep page rotation resolved through display coordinates and
  preserve the no-mutation overflow boundary. The resulting Windows abi3 wheel
  is 5.58 MB, up 0.16 MB from the arbitrary-font baseline.
- AcroForm filling writes `/V`, synchronizes button `/AS`, and regenerates
  widget `/AP /N`. Text and choice appearances auto-fit in widget-local
  coordinates, respect inherited `/Q` and multiline `/Ff`, and reuse the Core
  14 or krilla generation paths. Comb text fields resolve inherited `/MaxLen`,
  count Unicode graphemes, position them individually, and reject overlength or
  incompatible flag combinations. Preserve non-empty authored button states
  and synthesize only missing/empty Off/on states. Widget `/MK /R`, `/BG`,
  `/BC`, `/BS /W`, and legacy `/Border` feed the appearance. Keep updates
  atomic by restoring the document clone on error. hayro 0.7 cannot select an
  `/AP /N` state dictionary, so the rendering snapshot substitutes any
  resolvable widget `/AS` stream; the editable/saved PDF remains canonical.
  Retain the original-byte fast path when no selected state stream can be
  resolved.
- AcroForm field-tree reads borrow object shapes, visit indirect cycles once,
  release the GIL, and reject complete results above 4,096 entries/nodes, 8,192
  edges, 64 levels, 1 MiB of encoded/decoded/materialized names or values, or
  4,096 choice-value items. Inherited values use shared storage during the walk
  and count once per returned leaf. `set_form_field` must enforce the same tree
  and 1 MiB input-value boundary while preserving its atomic rollback contract.
- AcroForm button handling rejects more than 4,096 widgets, 8,192 `/AP /N`
  state entries, 4,096 unique returned names, or 1 MiB of encoded/returned
  state-name text. Borrow and preflight state dictionaries before cloning them.
  Appearance synchronization must budget missing `Off`/on keys before mutation
  so a successful fill remains readable under the same limits. Resolve indirect
  field `/Kids` arrays consistently.
- Page annotation and link reads borrow direct/indirect `/Annots` arrays,
  release the GIL, and reject complete results above 4,096 array entries or
  1 MiB of aggregate encoded/returned subtype, Contents, URI, file, and
  destination text per call. Creation must preflight the same page count,
  1 MiB aggregate generated subtype plus Contents/URI input, and 4,096
  highlight rectangles before creating dependent objects or invalidating
  caches. Successful output must remain readable under the same budget.
- Named-destination `/Names/Dests` lookup is iterative, visits indirect cycles
  once, and rejects traversal above 4,096 entries/nodes, 8,192 edges, 32
  levels, or 1 MiB of scanned key bytes. Do not turn a truncated lookup into an
  ordinary unresolved destination. Legacy catalog `/Dests` remains a direct
  dictionary lookup after a bounded name-tree miss. `Page.get_links` builds one
  borrowed index lazily per call; do not rescan the tree for each link.
- TOC reads use pylopdf's iterative outline walk rather than lopdf's recursive
  parser. They visit indirect cycles once, release the GIL, index named
  destinations once, and reject partial results above 4,096 nodes/entries,
  8,192 edges, 64 levels, 32 destination indirections, or 1 MiB of
  source/returned text. `set_toc` preflights the entry, depth, and
  source/encoded-title boundaries before mutation.
- Info metadata reads decode only the eight public standard fields, release the
  GIL, and reject aggregate source or returned text above 1 MiB. The fast
  metadata probe applies the returned-text boundary. `set_metadata` must batch
  and preflight the 1 MiB source/encoded boundary before mutation; inline Info
  dictionaries are moved rather than cloned.
- Encode non-ASCII metadata strings as UTF-16BE with a BOM.
- Page-label number-tree reads borrow node shapes, visit indirect cycles once,
  release the GIL, and reject the complete result above 4,096 entries/nodes, 32
  levels, or 1 MiB of encoded/decoded style and prefix text. Do not return a
  silent depth-truncated list. `set_page_labels` must enforce the same
  entry/text boundary before mutation and invalidate caches only after success.
- `Document.get_pdfa_claim` releases the GIL and defaults to a 1 MiB decoded
  XMP limit applied to every filter layer. `max_size=None` is the explicit
  unbounded opt-out and failures use `LimitError.code == "xmp_metadata_size"`;
  malformed or unsupported filters must not fall back to encoded bytes. Match
  exact `pdfaid:part` / `pdfaid:conformance` XML elements or attributes, not
  lookalike prefixes, quoted values, comments, or CDATA. This remains a
  self-declaration read rather than PDF/A validation.
- `api/public-api.json` is the reviewed candidate public surface. It covers
  `__all__`, signatures and defaults, documented members, TypedDict keys, type
  aliases, NamedTuple fields, enum/constant values, and exception inheritance.
  Run `uv run python tools/check_api_surface.py`; refresh with `--update` only
  after reviewing runtime, typing, documentation, and SemVer impact. The
  snapshot detects changes but does not decide compatibility.
- GIL-enabled CPython 3.10–3.14 uses one `abi3-py310` wheel per platform.
  Free-threaded CPython 3.14 uses a version-specific `cp314-cp314t` wheel.
  Add `abi3t-py315` only when 3.15t builds can be tested: enabling it alongside
  3.14t breaks maturin's cross-compilation config by raising the implied
  minimum interpreter version. Add size-increasing dependencies cautiously;
  published v0.10.0 wheels are about 5.0–5.8 MiB depending on platform and ABI.
- Pyodide 0.28.3 uses a static wheel built by `tools/build_pyodide.sh` with
  exact Python 3.13.2, Emscripten 4.0.9 and its Node.js 20.18.0, Rust 1.95.0,
  pyodide-build 0.30.7, maturin 1.14.1, and hashed build dependencies. The
  builder first verifies and smoke-tests the runtime-native
  `cp310-abi3-pyodide_2025_0_wasm32` tag, then uses wheel 0.47.0 under the same
  `SOURCE_DATE_EPOCH` to replace it with the PEP 783
  `cp310-abi3-pyemscripten_2025_0_wasm32` publication tag. Only the latter may
  remain in the release artifact directory because PyPI no longer accepts the
  legacy tag. Rust v0 symbol mangling avoids invalid legacy Emscripten exports.
  The wheel must import `env.__cpp_exception` and must not require a
  wasm-bindgen shim. CI and release builds must also pass
  `tools/smoke_cloudflare.py`, which pins workers-py 1.15.0 and Wrangler
  4.114.0. Emscripten excludes lopdf's `chrono` and `rayon` features: browser
  clocks pull in js-sys imports, and the global rayon pool cannot create
  workers there. `render_pages` keeps its public contract but runs serially;
  native targets retain bounded rayon execution. Emscripten also omits RTen
  inference while exposing an API-compatible `_OcrEngine` stub that raises
  `OcrError` and directs callers to external OCR plus
  `Page.insert_ocr_text_layer()`. Keep artifact sections, staged Pyodide
  startup/workload timing, linear-memory checkpoints, and Wrangler bundle
  measurements in CI. The 2026-07-26 baseline is a 3.834 MiB wheel and
  3.882 MiB compressed Cloudflare bundle; it fits the paid plan, not the Free
  compressed-size limit. Preserve one complete Wasm distribution unless a
  coherent deployment need justifies a variant.
- `tools/pyodide_compat.py` is the shared native/Pyodide functional contract.
  Keep its logical result independent of Python implementation details and
  platform-sensitive raster bytes. It must combine explicit content/structure
  assertions with full text/Markdown hashes rather than relying on either
  alone. The Python 3.10 CI job uploads the native baseline, and the Pyodide
  job must compare it exactly. Coverage includes bytes input, PDF 2.0,
  embedded/vertical CJK, sustained columns, bordered/borderless rotated tables,
  vector extraction, image-only input, AES-256 authentication, Standard 14 and
  subset-embedded OpenType generation, textbox layout, pixmaps,
  `render_pages(workers=1/4)`, virtual-filesystem save, merge/select, and typed
  error recovery. Add only small redistributable PDFs listed in
  `tests/assets/real_world/README.md`; non-PDF inputs such as the existing CJK
  font may be supplied from already licensed repository assets. Do not claim
  direct PyPI installation through Pyodide 0.28.3: its `micropip` predates PEP
  783 even though the binary itself passes that runtime's suite.
- `DocumentLimits` is the public untrusted-input policy. Python validates the
  immutable positive budgets and the Rust load boundary enforces file bytes,
  pages, indirect objects, direct array/dictionary depth, per-stream and
  cumulative decompression, and page-content decompression before hayro work.
  Cumulative UTF-8 glyph payload is admitted lazily across interpreted pages
  and shared by the TextPage/TablePage caches. Policy failures must raise
  `LimitError`, a `PdfError` subclass whose first argument and `.code` are one
  of the documented stable resource identifiers. `Document.complexity` may
  traverse structure but must not decode streams or invoke hayro. Keep
  `max_decompressed_size=` as the compatible shorthand, keep the web profile
  usable for representative scans, and require the host to enforce CPU
  deadlines. Native/Pyodide logical regressions and generated Atheris seeds
  guard this boundary.
- Hayro warnings are collected by the interpreter settings sink in
  `pending_warnings`; Python's `_emit_warnings` drains them as
  `PylopdfWarning` after each operation.
- `Pixmap` is immutable. Version-specific builds expose its RGBA8 storage
  through a read-only zero-copy buffer. The buffer protocol remains unavailable
  under `abi3-py310` because `Py_buffer` entered the stable ABI in Python 3.11;
  `Pixmap.samples` is the one-copy portable fallback. `Pixmap.save` encodes and
  writes PNG output while the GIL is released and maps I/O failures to
  `PdfError`.
- Concurrent operations on distinct `Document` objects are supported.
  Concurrent external calls or edits on the same `Document` are not; `Page`
  shares its parent's restriction. `Document.render_pages` is the supported
  bounded same-document parallel read boundary.
## Known pitfalls

- lopdf's `time` feature contains an uncompilable `From<time::Time>`
  implementation introduced in 0.43.0. Upstream #527 is fixed but unreleased,
  so this project uses `chrono`.
- lopdf's content parser drops all operations after a comment line followed by
  an indented line, reported as lopdf#535. pylopdf is unaffected since v0.7
  because extraction moved to hayro (`rust/src/extract.rs`).
- The pre-commit `validate-pyproject` hook with `trove-classifiers` validates
  classifier existence. v0.4.0 was rejected by PyPI because of the invalid
  classifier `Topic :: Text Processing :: Markup :: PDF`.
  Do not add `validate-pyproject-schema-store`; it raises `UnboundLocalError`.
- Synchronize the version manually in three places: `pyproject.toml`,
  `rust/Cargo.toml`, and `src/pylopdf/__init__.py`.
- Release CI cross-compiles macOS x86_64 on an arm64 runner because Intel runner
  queues are slow.

## Release procedure

1. Update the version in all three locations, add the changelog entry, commit,
   and push.
2. Run `git tag -a vX.Y.Z -m "..." && git push origin vX.Y.Z`.
3. GitHub Actions (`release.yml`) builds abi3 and cp314t wheels plus the sdist
   for five platforms and publishes through PyPI Trusted Publishing.

The font wheel has a separate release process. Update the version in
`fonts/pylopdf-fonts-cjk/pyproject.toml`, then push a `fonts-vX.Y.Z` tag to run
`release-fonts.yml`. The first release requires registering the
`pylopdf-fonts-cjk` Trusted Publisher on PyPI with workflow
`release-fonts.yml` and environment `pypi`. Publish the font wheel before the
main package because the main `[cjk]` extra references it.

The OCR model wheel also releases separately. Update the version in
`models/pylopdf-ocr-models/pyproject.toml` and
`models/pylopdf-ocr-models/src/pylopdf_ocr_models/__init__.py`, keep the
artifact hashes synchronized in `SHA256SUMS` and its README, then push an
`ocr-models-vX.Y.Z` tag. Tests and `release-ocr-models.yml` consume that
manifest. The release workflow runs `tools/smoke_ocr_models_artifact.py`
against isolated wheel and sdist installations before attestation. The first
release requires registering the `pylopdf-ocr-models` Trusted Publisher on
PyPI with workflow `release-ocr-models.yml` and environment `pypi`. Publish the
model wheel before the main package because the main `[ocr]` extra references
it.

## Roadmap

[ROADMAP.md](ROADMAP.md) is the canonical medium-term plan, based on the
2026-07-22 market and upstream survey plus the 2026-07-23 deeper review of
out-of-scope areas. It includes strategy, the v0.6–v1.0 release plan, ecosystem
integrations, a watchlist, and explicit non-goals.

- Current phase: v0.11.0 is release-prepared on 2026-07-26 after v0.10.0 shipped
  on 2026-07-25. It completes `insert_textbox`, AcroForm appearances, typed
  public mapping contracts, vector and table extraction depth, image
  compression, native OCR, the PyEmscripten artifact, and Cloudflare deployment
  gates. Its documented 0.11 candidate API surface is now checked
  deterministically across every native Python test lane, with the post-v1.0
  SemVer and deprecation contract published in four languages. The first
  separately versioned OCR model package must be published before the main tag.
  Incremental save was rejected after OSS analysis and remains on the
  watchlist. v1.0 is targeted no earlier than 2026-08, after field feedback and
  further product refinement rather than as a deadline-driven API freeze.
- lopdf#535 no longer affects pylopdf since the v0.7 hayro extraction engine.
  An upstream fix remains a parallel contribution candidate.
- See [CHANGELOG.md](CHANGELOG.md) for completed history.
