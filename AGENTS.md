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

- Open a short-lived branch and pull request for each coherent unit of work.
  `main` is protected: direct pushes are rejected, and merging requires the six
  required status checks. Squash-merge so one merged pull request becomes one
  `main` commit.
- Write commit messages, code comments, docstrings, repository documentation,
  configuration comments, and user-facing messages in English.
- Non-English text is allowed only in localized documentation and data required
  to test Unicode or CJK behavior.
- Do not place experiments unrelated to PDF processing in this repository.

## Development commands

- `uv sync` — build the extension and install dependencies. Rust changes are
  included in uv's rebuild cache keys. maturin builds the release profile, so
  local builds use the published thin-LTO profile. For faster Rust iteration
  only, set `CARGO_PROFILE_RELEASE_LTO=false` and
  `CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16`; both are uv cache keys, so
  unsetting them rebuilds the published profile before measurement.
- `uv run pytest` / `uv run ruff check .` / `uv run mypy src tests`
- `uv sync --group bench && uv run python bench/run.py` — run reproducible
  benchmarks. Results are written to `bench/results/latest.md`; publish wins and
  losses together. Rendering reports end-to-end PNG time plus cold and warm
  Pixmap rasterization and PNG encoding time and size for both pylopdf and
  pymupdf, so encoder settings cannot hide rasterization losses.
- `py -3.14t bench/free_threaded.py` — measure independent-document extraction
  without the GIL. Results are written separately to
  `bench/results/free-threaded-latest.md` so regular benchmark runs cannot
  discard them.
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
  The custom theme keeps footer foreground and background colors inverted in
  both color schemes. Text selection uses explicit foreground and background
  variables, including selected links. Inline code uses a subtle paper
  background while fenced code remains dark. Retain at least WCAG AA contrast,
  including links inside inline code.
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
  resolve page attribute inheritance. `page_attribute_has_value` treats a null
  value, an indirect null, and a reference to a missing object as an omitted
  attribute, following ISO 32000-2 §7.3.7 and §7.3.10. Imports, page-tree
  rebuilds, and drawing therefore materialize the value inherited from the
  original ancestors, or the source default, and the page geometry readers
  inherit past such a value in the same way.
- Text extraction is implemented as a hayro `Device` in `rust/src/extract.rs`.
  It collects glyph Unicode and positions, then assembles lines
  (`LINE_TOLERANCE`), words (`WORD_GAP`), and blocks (`BLOCK_GAP`).
  `get_text("words"/"blocks"/"dict"/"rawdict")` and `search_for` share the same glyph
  collection through a bounded, generation-invalidated `TextPage` cache.
  The v0.14.0 `rawdict` option exposes one `chars` entry per retained glyph,
  with its complete Unicode `c`, the shared approximate bbox, and actual baseline
  origin. Do not split multi-character mappings or invent positioned spaces.
  Raw materialization must reuse dict grouping and existing text/glyph admission,
  without retaining additional data in the cache or allocating raw output for
  other extraction options.
  `search_for` caps the UTF-8 needle at 4,096 bytes before PyO3 copying and
  defaults returned geometry to 4,096 hits. Direct Rust calls repeat both
  checks, `max_hits=None` explicitly opts trusted result sets out, and
  failures use `search_input_size` or `search_hit_count` without returning a
  partial list. Dense matching must advance byte/character cursors
  monotonically and derive the first/last mapped glyph without per-hit vectors.
  Lowercase needle/page indexes, character-to-glyph maps, and result geometry
  grow fallibly; allocation refusal raises `PdfError` without returning partial
  hits, including trusted `max_hits=None` searches.
  CJK fallback configuration also applies to extraction, including invisible OCR
  text. Glyphs hayro delivers without any Unicode mapping (for example a Type 3
  font with no ToUnicode CMap, upstream gap LaurenzV/hayro#1331) are skipped;
  each fresh page interpretation with such glyphs surfaces one deduplicated
  static `PylopdfWarning` through the pending-warnings sink, and
  `pdfium-type3.pdf` pins the extraction gap as an upstream-linked expected
  failure. Hayro normalizes glyph space to 1000 upem, so font size is the transform
  factor × 1000. Vertical bboxes approximate baseline ± a size ratio.
  Overlapping paint runs on one baseline are split into source-order logical
  layers before inline geometry sorting; preserve distinct overprints rather
  than interleaving or deduplicating their glyphs. Run detection compares the
  preceding retained glyph without cloning its text, and line/run/layer
  collections grow fallibly. Glyph text stores single characters inline and
  keeps one owned heap string only for multi-character mappings; equality is
  textual, so a one-character CMap string still matches its inline form. Line
  clustering partitions, sorts, and splits arena indexes, whose order equals
  collection source order, instead of moving glyph records between
  intermediate collections; materialization then moves each record once into
  its clustered line, and whole-line ordering sorts precomputed per-line
  geometry keys rather than rescanning glyphs inside sort comparators.
  `DocumentLimits.max_text_glyphs` bounds cumulative positioned glyph records
  before caching or structured Python output. Text and table interpretations
  of one page share one admission, failed pages consume no budget, and
  refusals use `text_glyph_count`. The compatible default is `None`;
  `DocumentLimits.web()` sets 65,536.
  When `max_text_size` is configured, plain-text assembly preflights its exact
  UTF-8 size and caps one extraction call at twice that payload budget
  because inferred gaps plus line endings cannot outnumber non-empty glyph
  records. The output bound spans every internal chunk of the call, so
  repeated page numbers cannot amplify a bounded interpretation without bound.
  Admission keeps running text and glyph totals beside the per-page maps, so
  whole-document batches do not rescan every interpreted page.
  `Document.get_text(pages=None)` is the public batched entry. Each chunk of
  at most 4,096 page entries shares one hayro `InterpreterCache`, so a font
  reused by several pages parses once per chunk while whole-document calls
  never retain every parsed font; single-page interpretation keeps one
  per-call cache. No interpreter cache crosses threads or survives
  `invalidate_hayro_pdf`. Batched and single-page interpretation temporarily
  detach the hayro snapshot so budget admission and LRU insertion can run
  beside the cache borrow; helpers on that path must not touch `hayro_pdf` or
  `hayro_source`, and the snapshot is restored on success and failure. Plain-text
  output, multi-page joining, and structured span/word/line/block
  materialization grow fallibly; allocation refusal returns `PdfError` without
  discarding or partially exposing the cached page interpretation. Structured
  word generation reuses borrowed glyph slices instead of building a temporary
  reference vector for every word. After geometry-driven line and column
  discovery, pure RTL lines are reversed in place into logical Unicode order.
  A line with exactly one Latin or numeric token bracketed by strong RTL text
  also restores that token's forward order. Edge tokens, multiple tokens,
  standalone marks, general neutral punctuation, and other mixed-direction
  paragraph layouts remain in producer visual order. Multi-character CMap
  mappings remain intact, separate nonspacing-mark glyphs stay with their base,
  and symmetric inline-gap checks keep inferred spaces valid in either
  direction. Cache the required bidi properties once per collected glyph
  rather than repeating Unicode table lookups during each output
  materialization. Search, spans, words, tables, and Markdown share that order.
  Sustained whitespace gutters split same-baseline segments into recursive
  left-to-right columns; full-width headings and footers remain outside the
  column regions, and isolated wide gaps stay on one line. Baseline bands
  bisected by a wide gutter must keep prose-like density: rows that fragment
  into more than four same-baseline cell segments remain row-major, so
  spreadsheet label/total columns are not split from their grid. Sustained sub-em
  gutters require at least four dense support lines, 16 glyphs and four
  alphanumeric characters on each side, and one plausible separator per line;
  aligned labels, dot leaders, and dense multi-separator rows remain row-major.
  Confirmed narrow regions may absorb explicit whitespace, up to 0.5 em of
  horizontal scan/OCR drift, and small glyph-advance overlap on their
  continuation lines. Gutter discovery uses
  lightweight segment bboxes and font-size samples; only confirmed columns
  move glyphs into fallibly grown segment/region vectors. Narrow-gap events,
  support samples, established-line geometry, and owned splits also grow
  fallibly. Glyph, line, size, and gutter ordering uses in-place unstable sorts
  with explicit source-order or geometry tie-breakers so admitted extraction
  does not require hidden stable-sort scratch allocation. `find_tables` uses a
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
  searches at 65,536 candidates. Grid detection retains borrowed word glyph
  slices instead of materializing page-wide duplicate strings; only accepted
  cell text is owned. Rule components use a fallibly grown sorted vector rather
  than a `BTreeMap`, and coordinate, edge-interval, hybrid-inference, cell,
  coverage, anchor, and result buffers either grow fallibly or sort in place
  without allocation. The opt-in borderless `strategy="text"` requires at
  least three consecutive physical rows with the
  same segment count, aligned left or right edges, compatible leading, and
  clear gaps. Candidate segments borrow physical-line glyph slices rather than
  cloning text and font metadata for every qualifying row. Candidate runs,
  bounds, cells, anchors, and cell text grow fallibly, and an allocation
  refusal invalidates the complete table interpretation instead of returning
  partial results. Returning cached bordered or text tables deep-copies cell
  text, anchors, and result vectors fallibly after clip filtering; refusal
  exposes no partial result and preserves the cache. The text strategy
  intentionally does not run as the default because aligned multicolumn prose
  is geometrically ambiguous.
  `find_tables(clip=)` returns only complete candidate bboxes inside the
  display-coordinate region; it does not synthesize partial tables or reduce
  the cached full-page interpretation cost. `TableDiagnostics.confidence` is a
  deterministic ranking heuristic, not a probability. Text diagnostics retain
  em-normalized alignment error, minimum gutter, and row-gap variation.
  `Document.to_markdown()` inserts complete bordered tables by default and
  accepts `table_strategy="text"` for conservative non-overlapping borderless
  candidates or `None` to disable table conversion. Document conversion
  covers every page by default, shares the explicit page-entry bound below,
  validates entries once rather than re-walking the page tree per page, and
  defaults to a 64 MiB cumulative UTF-8 output cap. It uses a page-at-a-time heading-count pass and
  a page-at-a-time rendering pass rather than retaining every page layout,
  table, and word list together. It removes contained text
  from prose and heading inference while retaining words outside a table on the
  same physical line, and normalizes physical table matrices to the dominant
  logical text direction on right-angle rotations. Merged spans expand from the
  internal anchor map rather than guessing from adjacent empty slots. Document
  conversion passes each table the remaining aggregate Markdown budget before
  page assembly. Direct `Table.to_markdown` calls preflight exact escaped UTF-8
  size, including merged-cell expansion, and default to the same 64 MiB limit.
  Page headings, paragraphs, lists, and tables are charged as retained entries;
  paragraph and consecutive-list assembly must remain linear rather than using
  repeated immutable-string concatenation.
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
  Candidate collection stops at 4,097 before disabling inference, and its
  selection/partition buffers plus the shared glyph, table-rule, and font-cache
  collectors grow fallibly. Allocation refusal fails the interpretation
  without caching a partial page. Ruby, warichu, and mixed-orientation
  typography are not interpreted.
- `Page.get_drawings` uses a separate hayro Device and releases the GIL. It
  returns interpreted fill/stroke paint operations as pymupdf-style
  `DrawingInfo` mappings in rotation-resolved display coordinates. Commands are
  self-contained lines or cubics; quadratics convert exactly to cubics, and
  adjacent fill/stroke callbacks for one PDF operator combine as `type="fs"`.
  Solid paints expose normalized RGB and opacity; patterns retain geometry with
  `None` paint values. Clipping/group/soft-mask structure and clip-resolved
  visibility, optional-content layer names, text, images, and annotations are
  outside this path API. Optional-content visibility is still applied. Reject
  output above 8,192 paths, 131,072 commands, or 131,072 aggregate stroke-dash
  values rather than returning a partial result. Transform, bound, and compute
  each path bbox in one element pass without cloning the complete path.
  Interpreted commands retain a static kind plus an inline four-point buffer,
  avoiding one String and point-Vec allocation per command. Dash syntax and
  public command/path tuples materialize only after their aggregate admissions,
  grow fallibly, and return no partial result on allocation refusal.
- `Page.get_images` releases the GIL and materializes each drawn placement as
  JPEG passthrough or PNG. Reject the complete result above 4,096 placements,
  64,000,000 cumulative source pixels, or 64 MiB of encoded payloads per page.
  Bound Flate-to-DCT passthrough decompression to the remaining byte budget;
  non-passthrough PNG encoding writes into that same remaining budget. Combine
  separate RGB/gray and alpha planes through a bounded scratch buffer instead
  of a complete interleaved copy. JPEG passthrough copies, PNG output, and the
  placement collection grow fallibly; allocator refusal must not be mistaken
  for an unsupported image or codec failure. Format tags are static. Never
  return a partial list.
- `Document.embfile_add` and `embfile_get` share a 64 MiB default attachment
  boundary. Adds reject byte input before its PyO3 copy and repeat the check in
  Rust; gets apply the decoded-size limit to every filter layer. `max_size=None`
  is the explicit unbounded opt-out; limit failures use `LimitError.code ==
  "embedded_file_size"`, while malformed or unsupported filters must not fall
  back to encoded bytes. EmbeddedFiles name-tree traversal borrows direct
  shapes, visits indirect cycles once, and rejects more than 4,096 entries or
  nodes, 32 levels, or 1 MiB of encoded/decoded names. Caller lookup/deletion
  names stop at 1 MiB before tree traversal with `embedded_file_input_size`.
  Traversal/shape stacks, cycle sets, collected entries, returned names, and
  flat rewrite arrays grow fallibly; name ordering uses in-place sorts.
  Attachment edits must not create an over-limit tree or invalidate caches
  after a failed operation.
  They preflight the Catalog write target rather than cloning the whole
  Document for rollback, cap new key/filename/description input at 1 MiB before
  attachment-data copying in Python and again in Rust, and validate inline
  FileSpecs before cloning at 4,096 direct objects, 32 levels, and 1 MiB of
  direct string/name/stream data. Indirect references are leaves.
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
  Candidate JPEG bytes and Flate streams remain borrowed through admission,
  decode, resize, and re-encode; release those borrows before mutating the cloned
  lopdf document rather than cloning complete encoded sources per candidate.
  JPEG encoding writes through a boundary one byte below the source payload and
  stops as soon as the result cannot be smaller rather than materializing a
  complete rejected encoding. The interpreted usage map and its sorted result
  grow fallibly and return no partial aggregate on allocation refusal. Candidate
  and mask-reference sets also grow fallibly before any compression edit.
  Actual rewrites invalidate hayro and derived interpretation caches without
  making existing `Page` views stale; no-op calls preserve all caches. Reject
  more than 65,536 interpreted indirect raster placements, 16,384 unique
  indirect raster objects, or 250 million eligible decoded source pixels in one
  operation, and skip an individual source above 64 million pixels. The compact
  local separable Lanczos3
  implementation avoids a general-purpose resizing dependency. On Windows
  abi3, the wheel measured 6.86 MiB versus 6.78 MiB at the preceding v0.11
  commit (+0.07 MiB); extending the same path to bounded Flate decoding
  measured 6.90 MiB (+0.04 MiB).
- Native OCR uses RTen 0.24 with only its `rten_format` feature. The core wheel
  contains the pure-Rust inference engine; PP-OCRv6 small detector,
  recognizer, and dictionary data come from the independently versioned
  `pylopdf-ocr-models` package through the `[ocr]` extra. `OcrEngine` loads one
  immutable model set and owns a dedicated 1–16 thread pool. Detector,
  recognizer, and dictionary paths share a default 64 MiB cumulative input
  budget enforced before RTen parses either model; `max_model_size=None` is
  the explicit trusted-input opt-out. Path reads use fallible 64 KiB chunks and
  admit at most one byte beyond the remaining bounded budget. Dictionary
  materialization stops at 65,536 entries. Loading and inference release the
  GIL. OCR clones the
  Pixmap's shared immutable RGBA backing, composites
  RGBA onto white, and uses overlapping detector tiles bounded to 256–2048
  pixels, a 4096-candidate cap, and deterministic edge deduplication.
  Rotation rasters, detector/recognizer inputs, probability-map masks,
  connected-component growth, tile starts, candidates, and results allocate
  fallibly and surface failures as `OcrError`. The
  default 1408-pixel tile and 192-pixel overlap measured about 419 MiB peak on
  a 300-dpi A4 page. Recognizer class count must match dictionary length + 2.
  Results use rotation-resolved display coordinates and recursive sustained-
  gutter column order. One immutable engine can serve calls on distinct
  Documents, including CPython 3.14t. A Python admission semaphore covers the
  complete render-and-recognize operation; `max_concurrent=1` is the
  memory-safe default and values through 16 require workload measurement.
  Every admitted call owns raster and inference buffers. A two-document,
  four-thread field check at 150 dpi exactly matched sequential output, while
  admission limits 1 and 2 took 6.19s and 5.70s respectively. This is not a
  throughput claim; peak-memory amplification retains the default of 1.
  Dictionary entries, tile positions, recognized text, candidate union-find,
  row/column ordering, height samples, and gutter intervals grow fallibly under
  their existing caps; ordering uses in-place sorts with explicit geometry
  tie-breakers. Same-Document restrictions still apply. `Page.apply_ocr` skips
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
  character, and Python counts UTF-8 without allocating a complete encoded
  copy. CID maps, ToUnicode data, and content operators are prepared before
  PDF mutation. Input rejection preserves caches, but invalidate immediately
  before the first PDF mutation because later malformed-resource errors can
  leave a partial edit.
- Rendering caches a hayro snapshot in `_Document.hayro_pdf`. An unedited,
  unencrypted load without cross-reference recovery first consumes its original
  input bytes and falls back to a lopdf serialization only when hayro rejects
  them or reports a different page count. A recovered load retains no original
  bytes: hayro would recover the damaged input independently and can disagree,
  for example by rolling an incremental file back to its first revision.
  Editing methods must call `invalidate_hayro_pdf`, which also discards
  the original-byte fast path and the link-resolution cache; edited state must
  always be reflected in rendering and link reads. The page index needs no
  call: mutable document access drops it, as described with the walker below.
  `DocumentLimits.max_interpretation_size` bounds both retained original input
  and the complete reserialization after edits, decryption, or
  state-appearance normalization. Under a finite limit, serialize before
  constructing the state-normalized render copy. The bounded writer must not
  install a partial hayro cache. Original input above a finite limit retains
  only its size and limit until first-interpretation refusal; admitted
  in-memory input copies use fallible exact allocation. Failures use
  `interpretation_size`. Its
  compatible default is `None`, while `DocumentLimits.web()` sets 64 MiB.
- Raster rendering reuses one hayro `RenderCache` across calls through
  `rust/src/render_cache.rs`. hayro's cache borrows its `Pdf` snapshot and
  shares maps through `Rc`, so it never lives in the `Send + Sync` `_Document`
  and never crosses a thread: each thread keeps at most one slot that owns an
  `Arc` of the snapshot together with the cache through `self_cell`.
  `render_page`, `Page.get_pixmap`/`render`, and serial `render_pages` use the
  slot only on the thread that created the document, and only while the
  document's generation token and the snapshot identity both match.
  `invalidate_interpreted_pages` (therefore `invalidate_hayro_pdf` and every
  fallback-font change, because cached fonts depend on the resolver), `close`,
  and dropping the document end the generation and free a matching slot on
  that thread. One call grows the cache as a call-scoped cache would; a slot
  that has served more than 64 distinct pages, or whose render panicked, is
  dropped before the call returns. Other threads, parallel workers, SVG
  conversion, and text extraction keep call-scoped caches. An edit or drop on
  another thread cannot free the creating thread's slot, so that thread keeps
  at most one stale snapshot until it renders, invalidates or drops a document,
  or exits; keep this one-slot-per-thread bound. Interleaved 8-round A/B on
  GitHub-hosted Linux runners measured fresh-document renders within 1.5%,
  first-page re-renders 3–5% faster in corpus total, and 27-page
  `usrguide.pdf` `get_pixmap` loops 22–28% faster at 1x, with pixel-identical
  output and at most 3 MiB more memory on all five targets.
- `Document.render_pages` is the supported same-document concurrency boundary:
  it renders an immutable hayro snapshot on a dedicated rayon pool, preserves
  input order, releases the GIL, accepts 1–64 requested workers, and caps actual
  concurrency to roughly 512 MB of estimated raster and conversion buffers.
  Serial and PyEmscripten execution use one hayro `RenderCache` per chunk of
  at most 4,096 page entries: the first chunk on the creating thread starts
  from the retained cache, other chunks and threads use call-scoped caches.
  Native parallel execution gives each bounded worker its own cache, replaced
  after 4,096 rendered pages, and uses an atomic page queue to retain dynamic
  load balancing; a cache never crosses a thread. Pixel preflight does not
  materialize an intermediate
  collection. Serial results, worker-local indexed results, parallel reduction,
  input-order slots, and the final returned vector all grow fallibly.
  It renders every page by default and shares the explicit page-entry bound
  below. PNG writer chunks atomically
  share the default 512 MiB cumulative encoded-output budget across serial,
  rayon, and PyEmscripten execution, refusing the first crossing write before
  retaining it; `max_size=None` is the explicit unbounded opt-out and limit
  failures return no partial list. Other simultaneous calls or edits on the same
  `Document` are outside the contract.
- `Document.render_page`, `Page.render`, and `Pixmap.tobytes` default to a
  64 MiB encoded-PNG output boundary. The shared PNG writer refuses the write
  crossing the limit before Python bytes conversion and grows its retained
  output fallibly. `max_size=None` is the explicit trusted-output opt-out.
  Render failures use `render_output_size`; direct Pixmap failures use
  `pixmap_output_size`.
- `Document.render_page_svg` and `Page.render_svg` default to a 64 MiB UTF-8
  output cap and raise `LimitError` with code `svg_output_size` before PyO3
  creates the Python string; `max_size=None` explicitly opts out. hayro-svg 0.7
  returns only a completed `String`, so this boundary does not cap the
  converter's one internal Rust allocation.
- `Page.get_pixmap(clip=)` accepts rotation-resolved display coordinates,
  intersects them with the page, and rounds outward to pixel boundaries.
  hayro 0.7 lacks an offset viewport, so clipping crops a full-page raster and
  does not relax the full-page render-size limits. Straight-alpha conversion
  and cropped output reserve their RGBA buffers fallibly. A clip converts only
  its selected hayro pixels rather than first materializing complete-page RGBA.
  The completed `Vec<u8>` moves into an `Arc<Vec<u8>>` without copying the
  complete raster.
- Release the GIL with `Python::detach` for heavy operations: load, save, render,
  extraction, merge, and compression.
- `Page` is a lightweight view of a `Document` plus a generation number.
  Python methods that change page structure must call `_bump_generation()`.
  Otherwise an old `Page` could silently refer to a different page. Old pages
  must raise `StalePageError` after structural changes.
- Page batches validate entries against one page-count snapshot instead of
  re-walking the page tree per entry. `pages=None` and complete `insert_pdf`
  ranges cover every page whatever the page count. Explicit `get_text`,
  `to_markdown`, `render_pages`, `select`, and `delete_pages` entries may repeat
  pages up to the larger of the page count and 4,096; materialization stops at
  the first entry beyond that bound with `LimitError.code ==
  "page_entry_count"`, so repetition never costs more than one complete pass
  and `select` cannot grow a large document. Rust repeats the relative check,
  walking the page tree only above the floor, and core `merge_pages` rejects
  repeated source pages before mutation because one page object cannot appear
  twice in the tree. `delete_pages([])` is a true no-op: do not invalidate
  caches or bump the Python generation. Imported and selected page orders, duplicate tracking,
  inherited page dictionaries, spliced orders, and root/rebuilt Kids arrays
  grow fallibly. `select` validates and plans all page IDs and duplicate copies
  before cache invalidation or PDF mutation; page-tree rebuilds resolve every
  page and allocate the final Kids array before committing dictionaries.
- Structural edits (`delete_pages`, `select`, `insert_pdf`, `new_page`,
  `copy_page`, `set_toc`, and `show_pdf_page`) and `garbage=True` saves remove
  unreachable objects with `prune_unreachable_objects` in `rust/src/prune.rs`.
  Never call lopdf's `Document::prune_objects`: it records visited IDs in a
  `Vec` and scans it for every reference and every object, which is quadratic
  in object count. The replacement keeps lopdf's semantics, following
  references from the trailer through arrays, dictionaries, stream
  dictionaries, and reference-valued indirect objects but never through stream
  content, and visits each existing object once with a hash set and an explicit
  stack. The set is reserved for the current object count and the stack grows
  fallibly; refusal raises `PdfError` before any object is removed. Pruning
  still runs after the edit it follows has committed, so a refusal there leaves
  that edit and its unreachable objects in place.
- `insert_pdf`, `show_pdf_page`, and generated text and form appearances
  renumber their disposable source copy with `renumber_objects` in
  `rust/src/renumber.rs` before any target mutation. Never call lopdf's
  `Document::renumber_objects_with`: it walks the graph with a `Vec` of visited
  IDs, twice when page numbers descend in page order, and rescans the bookmark
  table that `set_toc` fills once per renumbered object, so it is quadratic in
  source object count. The replacement keeps lopdf's numbering: objects keep
  their generation and take consecutive numbers in ascending ID order, and page
  objects trade slots so that page-tree order ascends. Unlike lopdf, it
  replaces every reference to a missing object, matched by number and
  generation, with null, as ISO 32000-2 §7.3.10 reads it. A kept number would
  resolve to whatever target object uses it after import, including objects
  that the import or a later edit adds. Read the page order with the bounded
  walker before renumbering; `renumber_objects` needs it to order page slots
  and rewrites it to the new IDs. A hash map reserved for the object count
  assigns IDs, a fallibly grown stack rewrites references in the trailer and
  in every object, and lopdf's bookmark table is left alone because only
  `set_toc` uses it, after clearing it. Refusal raises `PdfError` and may
  leave the copy partly rewritten.
- Rust defines `PdfError` (a `ValueError`-compatible base) and `PasswordError`;
  Python defines `DocumentClosedError`, `EncryptedDocumentError`, and
  `StalePageError`. Add new errors under the `PdfError` hierarchy instead of
  introducing plain `ValueError` exceptions. Since lopdf 0.45, wrapped causes
  appear only through `Error::source`; format lopdf errors through
  `LopdfErrorChain` so limit and decoder details stay in messages.
- Encryption during `save` operates on a clone, so the in-memory document always
  remains plaintext. Python generates the key with `os.urandom(32)`. Open,
  authenticate, fast metadata probe, and AES-256 save passwords stop at the PDF
  2.0 boundary of 127 UTF-8 bytes before PyO3 copying or KDF work. Direct Rust
  entry points repeat the check, failures use `password_input_size`, and save
  validation must precede save-option mutation, cloning, or output creation.
- Public `Document.save` must securely create a same-directory sibling, stream
  every normal, object/xref-stream, or encrypted output there, and replace the
  requested path only after the core writer succeeds. Map creation/replacement
  errors into `PdfError`, preserve the requested path in messages, and clean up
  failed temporaries. Carry an existing regular file's POSIX mode onto the
  sibling before replacement. Preserve a final symlink by atomically replacing
  its resolved target, while keeping the requested path in errors. Save options
  still mutate the in-memory document before I/O. Direct `_Document` path
  writers remain a low-level non-atomic boundary.
- `Document.tobytes` defaults to a 512 MiB serialized PDF output boundary.
  Normal, object/xref-stream, and encrypted core paths must all write through
  `BoundedPdfOutput`, which refuses the write crossing the limit before Python
  bytes conversion, grows retained output fallibly, and raises stable code
  `pdf_output_size` for the configured boundary. `max_size=None` is the explicit
  trusted-input opt-out. File `save` remains streamed and outside this in-memory
  output boundary. Restore lopdf's temporary xref/object-stream `max_id` and
  trailer writes after every successful or failed byte/file serialization so
  repeated no-op output is deterministic. Preserve the documented mutation
  semantics of `garbage`, `deflate`, and the PDF-version/xref-mode changes from
  `object_streams` on output refusal.
- TOC page numbers in `get_toc` and `set_toc` are one-based for pymupdf
  compatibility. All other page APIs are zero-based.
- lopdf automatically decrypts PDFs with an empty user password. Other encrypted
  PDFs require the `password` argument or `authenticate()`, which reopens the
  document with a password. `_ensure_open` must check `is_encrypted` because an
  undecrypted document otherwise appears to have zero pages.
- Lenient opening relies on lopdf 0.45 cross-reference recovery
  (`rust/src/recovery.rs`). lopdf moves a `startxref`, `/Prev`, or `/XRefStm`
  offset that misses by at most 64 bytes to the nearest `xref` keyword, and
  rebuilds the table from line-start object headers plus the newest classic
  `trailer` whose `/Root` exists when standard resolution fails; later
  revisions win. It reports both only as `log` warnings from `lopdf::reader`,
  emitted on the loading thread before object parsing moves to the rayon pool.
  `recovery::load_document` and `load_metadata` install one process-wide
  logger and capture those records in a thread-local scope, so concurrent
  loads keep their own result. Rust unit tests are not used: the damaged-corpus
  regressions in `tests/test_real_world.py` and the Pyodide contract must
  catch any lopdf message change. The two kinds emit distinct
  `PylopdfWarning` text, set `Document.is_repaired` and
  `MetadataProbe.repaired`, and saving normalizes the xref data. A rebuilt
  trailer drops `/Prev` and `/XRefStm`, which still point into the damaged
  input. lopdf's other lenient tolerances (19-byte xref entries, stream
  `/Length` recovered at an unambiguous object boundary, and skipped
  unparseable objects) are not reported. Reconstruction never runs for
  damaged xref-stream files without a classic trailer; they remain rejected.
  Before every lenient load, a linear pre-scan estimates lopdf's
  reconstruction work: each `stream` keyword after the last `endstream`
  repeats a scan to the end of the input and a backward `obj`/`/Length`
  search. Input above 64 bytes per input byte plus 64 MiB fails with
  `LimitError.code == "recovery_scan_size"` before lopdf runs; real damaged
  files carry at most a truncated final stream there. `DocumentLimits` apply
  to recovered documents unchanged.
- CJK fallback replaces hayro's `font_resolver`
  (`pick_cjk_fallback` in `rust/src/document.rs`). Detect CJK through
  `CIDSystemInfo` or the `BaseFont` name, route Adobe-Japan1, Korea1, GB1, and
  CNS1 to independent `ja`, `ko`, `zh-CN`, and `zh-TW` slots, and use
  conservative BaseFont-name hints only when collection metadata is absent.
  Serif-like names use the serif slot; other names use sans. Font files come
  from locale workspace packages under `fonts/pylopdf-fonts-{jp,ko,zh-cn,zh-tw}/`
  and are exposed only through matching locale extras. Explicit and
  auto-selected fallback font input shares the 64 MiB default OpenType boundary
  described below. Auto-discovery keeps package assets as paths and must read
  every selected sans/serif pair successfully before one cache update. Failed
  path reads or size checks must preserve configured fonts and caches.
- Drawing (`rust/src/draw.rs`) appends streams to `/Contents` without
  re-encoding existing content. Existing arrays are wrapped in `q/Q` only once.
  `_Document.isolated_content_pages` retains verified page IDs after later
  overlays or underlays; do not trust a persistent PDF marker from untrusted
  input. Initial leading/trailing sentinel detection borrows stream bytes and
  never clones complete page-content streams. Before cache invalidation, input
  decoding, or dependent object creation, drawing calls reject raw arrays above
  4,096 entries, reference chains above 32 levels or with cycles, and any
  insertion that would take the final array above 4,096 stream references. They
  fallibly prepare the exact final array and any q/Q isolation streams before
  dependent object creation, then commit assigned references without growing
  the collection. XObject and Font registration also materializes inherited
  Resources and the selected category into one page-owned direct dictionary
  before dependent work. Reject resource dictionaries above 4,096 entries,
  indirect chains above 32 levels or with cycles, and direct shapes above 8,192
  objects, 32 levels, or 1 MiB; reserve the generated name with a null slot and
  replace it without collection growth at commit. Resolve all four inheritable
  page attributes in one parent-tree pass with a 64-level/cycle boundary.
  Form-XObject imports admit at most 65,536 source objects and 64 MiB of wrapped
  decoded page content. They prepare bounded content, inherited geometry, and
  the complete movable object collection before cache invalidation or target
  mutation; commit then has no recoverable source-side work.
  Inputs use display coordinates with a top-left origin and page rotation
  resolved, then convert to `cm`/`Tm`. `insert_image(pixmap=)` splits immutable
  straight-alpha RGBA8 storage through fallibly allocated RGB and optional
  alpha planes, then Flate-compresses through a fallible writer; fully opaque
  Pixmaps must not create a mask. Encoded JPEG insertion moves the admitted
  input directly into its XObject. PNG alpha separation compacts decoded color
  samples in place and fallibly allocates only the separate alpha plane.
  Encoded `insert_image` input defaults to 64 MiB and PNG decode to 64,000,000
  pixels. `filename=` reads through a bounded Rust path with the GIL released
  and fallible 64 KiB retained-buffer growth; `stream=` is checked in Python
  before PyO3 copying and again in Rust. Preflight PNG IHDR dimensions before
  allocating decoded storage. `max_size=None` and `max_pixels=None` are explicit
  trusted-input opt-outs; failures use `image_input_size` and
  `image_pixel_count`.
  `insert_image(rotate=)` rotates every source clockwise in normalized
  right-angle steps, swaps the aspect ratio for 90/270, and composes with target
  page rotation in display space. Same-document `show_pdf_page` must clone the
  lopdf graph before importing the source Form XObject so the target page can
  safely source itself without serialization or mutable aliasing. Form source
  page content is strictly decoded under its aggregate boundary before target
  invalidation or mutation; decoder errors and unsupported filters must not
  fall back to encoded bytes, and size refusal uses `form_content_size`.
  Unfiltered content remains borrowed while building the combined Form stream.
  Annotations must always include an appearance
  stream at `AP /N`, because hayro does not render annotations without one. The
  immutable rendering snapshot
  conservatively synthesizes missing appearances for bounded RGB Highlight,
  Underline, StrikeOut, and Squiggly annotations with valid `QuadPoints`; this
  must not mutate the editable or saved PDF. Aggregate geometry stops at 4,096
  quads and 65,536 generated path segments without returning a partial
  synthesized set. State substitutions and missing text-markup appearances are
  planned once, with borrowed dictionary reads and fallible collections, before
  preparing the rendering clone. Quad bounding boxes do not materialize a
  second point vector, and generated appearance buffers grow fallibly.
  Pylopdf-created Highlights prepare quads, appearance operators, and
  `QuadPoints` before cache invalidation or dependent-object creation.
  Annotation appends validate the final page limit and preallocate their
  `/Annots` target before dependent-object creation. Direct arrays and unshared
  indirect arrays reserve one slot in place; page-shared indirect arrays
  prepare one fallibly detached direct array so copied pages do not leak edits.
  The final reference push must not grow a collection. Link annotations also
  build their complete input-derived dictionary before cache invalidation.
  `render_annotations` defaults to true.
- Simple-font text replacement is prepared in `rust/src/text_replace.rs`.
  Search/replacement/fallback input is capped at 4,096 UTF-8 bytes. Decoded
  page content, aggregate font encoding data, intermediate growth, and the
  final content stream share the public `max_size` boundary. Python counts
  caller UTF-8 incrementally before PyO3 copying; direct Rust calls repeat the
  check. Re-encoding uses a calculated upper bound before allocation and
  linear per-character fallback. Commit one new page-owned stream only after
  all fallible work succeeds so copied pages do not mutate shared `/Contents`;
  no-match and failure paths must preserve document bytes and caches. A
  successful replacement materializes inherited page attributes and clears
  the drawing isolation marker for that page.
- Embedded-font text generation lives in `rust/src/generate.rs`. krilla is
  pinned to 0.8.2 with all default features disabled; HarfRust 0.12 supplies
  shaping without krilla's unmaintained rustybuzz/ttf-parser path. Raster and
  PDF-import features remain disabled. Generation creates a transparent page
  in target display coordinates, subset-embeds the selected OpenType face, and
  returns bytes that the existing lopdf Form-XObject path imports. It releases
  the GIL and rejects missing glyphs. Without an explicit font source,
  optional providers `pylopdf[ar]`, `[he]`, `[hi]`, `[jp]`, `[ko]`, `[th]`,
  `[zh-cn]`, and `[zh-tw]` supply Noto Sans/Serif fonts. Unambiguous script
  markers select a provider; pure Han remains JP-first for compatibility and
  `font_language` resolves the intended locale. This selects one font for the
  complete run; it is not per-glyph fallback. Mixed provider scripts require
  an explicit font. `insert_text`, `insert_textbox`,
  `set_form_field`, and `set_fallback_font` share a 64 MiB default OpenType
  boundary. Python rejects buffer input before PyO3 copying; file input uses a
  one-byte-overrun bounded Rust read with the GIL released and fallible 64 KiB
  retained-buffer growth. Direct byte/path core variants repeat the check,
  `max_font_size=None` explicitly opts trusted input out, and failures use
  `font_input_size` without mutation.
  `insert_text` and `insert_textbox` also share a 1 MiB default aggregate UTF-8
  boundary. Python rejects text before normalization, tab expansion, or PyO3
  copying; textbox tab expansion is size-preflighted without materializing the
  expanded string, and direct core variants repeat the check. A configured
  budget also caps physical and final wrapped layout at 4,096 lines; AcroForm
  text and choice appearances use the same fixed layout cap. Collect UAX #14
  and grapheme boundaries once per wrapped paragraph, and use local galloping
  plus binary refinement so long unbreakable runs do not repeatedly rescan or
  measure complete tails. Boundary indexes, line collections, and retained line
  text grow fallibly; allocation refusal becomes `PdfError` without exposing a
  partial layout or mutating the document. Pylopdf-owned single-line
  normalization, grapheme references, shaped-line/glyph collections, WinAnsi
  output, and input-derived text-operator growth are also fallible. Standard 14
  width checks and WinAnsi support probes do not allocate an encoded text copy.
  Allocation refusal returns `PdfError`; insertion keeps its no-draw boundary
  and form filling uses its document rollback. `max_text_size=None` explicitly
  opts trusted insertion input out, failures use `text_input_size` or
  `text_line_count`. Paragraph layout remains outside `insert_text`. Pure RTL
  lines and lines with one Latin or numeric token bracketed by RTL text
  round-trip through extraction in logical Unicode order; the broader
  mixed-direction boundary remains in producer visual order. krilla emits
  `/ActualText` for complex glyph-to-Unicode mappings.
  Generated PDFs retain it for compliant viewers, but hayro 0.7 does not expose
  that property to extraction devices, so some reordered or context-dependent
  complex-script clusters remain an explicit pylopdf extraction limitation.
  Keep third-party acknowledgements in `NOTICE.md` and include both license
  files through PEP 639. The Windows abi3 wheel measured 5.42 MB after
  integration, up from 4.44 MB.
- `Page.insert_textbox` completes layout before mutating the PDF and returns
  negative spare height without drawing on overflow. Standard 14 measurement
  uses canonical Adobe AFM widths; embedded OpenType measurement uses HarfRust
  advances and font vertical metrics. Both share greedy UAX #14 wrapping in
  `rust/src/layout.rs`, with grapheme-safe emergency breaks and soft-line-only
  justification. Measure the complete visible paragraph first and return it
  directly when it fits so wide one-line boxes remain linear; otherwise reuse
  one break/grapheme index and bounded local probes. Preserve
  trailing-whitespace trimming and empty-paragraph semantics. Keep page
  rotation resolved through display coordinates and preserve the no-mutation
  overflow boundary. The resulting Windows abi3 wheel is 5.58 MB, up 0.16 MB
  from the arbitrary-font baseline.
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
  4,096 choice-value items. Inherited values use shared storage during the
  walk and count once per returned leaf. Field-attribute inheritance (`/FT`,
  `/Ff`, and `/V` in the walk; `/Q` and `/MaxLen` along the `/Parent` chain)
  reads a null value, an indirect null, and a reference to a missing object as
  an omitted entry, following ISO 32000-2 §7.3.7 and §7.3.10 as
  `page_attribute_has_value` does, so the field inherits its parent's value.
  `inheritable_field_value` keeps any other value on the field, including an
  unusable one or a cyclic or over-long reference chain, and that value stops
  inheritance; only `/FT` and `/Ff` also inherit past other unreadable values.
  pylopdf reads neither `/DA` nor `/DV`; route any inheritable key it starts
  reading through the same helper. Tree stacks, cycle sets, inherited
  names/types, choice values, joined values, returned entries, field-widget
  lists, and button-state names grow fallibly; returned ordering uses an
  in-place sort with object-ID tie-breaking.
  `set_form_field` must reject caller
  field names and values above 1 MiB before font discovery, button lookup, or
  file reads, repeat the check in Rust with `form_field_input_size`, enforce the
  same tree limits, and preserve its atomic rollback contract.
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
  caches. Caller annotation text must stop before PyO3 copying with
  `annotation_input_size`, direct Rust calls repeat the boundary, and
  highlight iterables stop at item 4,097 without complete materialization.
  Successful output must remain readable under the same budget.
- Named-destination `/Names/Dests` lookup is iterative, visits indirect cycles
  once, and rejects traversal above 65,536 entries/nodes or 131,072 edges with
  `named_destination_count`, above 16 MiB of scanned key bytes with
  `named_destination_text_size`, or above 32 levels. Do not turn a truncated
  lookup into an ordinary unresolved destination. TOC reads, `Page.get_links`,
  `insert_pdf` link import, and page cleanup share these large-manual limits:
  the cleanup rewrite in `rust/src/page_cleanup.rs` matches the read-side
  walker's dictionary-node, edge, entry, and key-byte accounting, and legacy
  catalog `/Dests` uses the same entry and key-text limits, so every
  well-formed tree that resolves can also be cleaned and imported. Admitted
  keys are copied without a second charge; retained destination values copy
  within 16 direct objects per admitted entry (1,048,576) and 16 MiB of value
  text. Names looked up by `resolve_local_destination_array` charge the
  `DestinationNameBudget` of the structure being walked: outline cleanup uses
  the 16 MiB TOC text budget, name-tree rewrites share the key-text budget,
  and page links and the OpenAction keep the 1 MiB per-page annotation budget.
  Legacy catalog `/Dests` remains a direct dictionary lookup after a bounded
  name-tree miss. `Page.get_links` reuses one `LinkResolutionCache` on
  `_Document` until `invalidate_hayro_pdf` drops it on the next edit. It holds
  an owned name-tree index built on the first named lookup; page order and
  reverse page numbers come from the shared page index. The name-tree index
  sorts and deduplicates borrowed keys in place with a traversal-order
  tie-break, so the first occurrence wins, then allocates one exact buffer of
  distinct keys plus entries that keep only each destination's page operand,
  fit type, and numeric operands for binary search. A refused or failed build
  caches nothing, so every later named lookup repeats the bounded build and a
  refusal raises again. Do not rescan the name tree or walk the page tree per
  link or per page: destination geometry resolves from cached page IDs.
  Traversal stacks, cycle sets, the borrowed TOC index, and the owned link
  index grow fallibly. Integer page indexes in link destinations use checked
  conversion and must name an existing page; anything else is unresolved like
  an unknown page reference. Convert PDF-supplied integers with `try_from`
  rather than truncating `as` casts.
- TOC reads use pylopdf's iterative outline walk rather than lopdf's recursive
  parser. They visit indirect cycles once, release the GIL, index named
  destinations once, and reject partial results above 65,536 nodes/entries or
  131,072 edges with `toc_entry_count`, above 16 MiB of source/returned text
  with `toc_text_size`, or above 64 levels or 32 destination indirections.
  These large-manual limits are shared by page-cleanup outline traversal,
  which also charges looked-up destination names to the TOC text budget, so
  every written outline remains readable and editable. Page indexes,
  destination and outline cycle sets, outline stacks, and returned entries grow
  fallibly. `set_toc` preflights the entry, depth, and source/encoded-title
  boundaries before PyO3 copying, using `toc_entry_count` and
  `toc_input_size`; direct Rust calls repeat the entry and text boundaries, then
  prepare entries and the parent stack fallibly before PDF mutation.
- Info metadata reads decode only the eight public standard fields, release the
  GIL, and reject aggregate source or returned text above 1 MiB. The fast
  metadata probe applies the returned-text boundary and accepts an optional
  input-file boundary before parsing. Bounded path reads admit at most one byte
  beyond the budget, byte input is checked directly, and failures use
  `LimitError.code == "file_size"`. `set_metadata` must batch and preflight the
  1 MiB source/encoded boundary before PyO3 copying with
  `metadata_input_size`; direct Rust calls repeat the boundary. Inline Info
  dictionaries are moved rather than cloned. Returned maps, prepared write
  batches, direct single-entry input copies, and encoded PDF strings grow
  fallibly; duplicate tracking uses a fixed bitset for the eight public keys.
- Encode non-ASCII metadata strings as UTF-16BE with a BOM.
- Page-label number-tree reads borrow node shapes, visit indirect cycles once,
  release the GIL, and reject the complete result above 4,096 entries/nodes, 32
  levels, or 1 MiB of encoded/decoded style and prefix text. Do not return a
  silent depth-truncated list. Traversal stacks, cycle sets, and returned
  entries grow fallibly, and returned ordering uses an in-place sort with
  explicit value tie-breaks. `set_page_labels` must enforce the same
  entry/text boundary before PyO3 copying with `page_label_input_size`, repeat
  it in direct Rust calls, and invalidate caches only after success.
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
  All CI, documentation, fuzz, and release jobs use standard GitHub-hosted
  runners. Native wheels must install and execute on all five supported
  architecture-matched targets before publication. Set deadlines for each
  workload so compilation, tests, and artifact uploads can finish; there is
  no global one-minute cap. Skipped checks do not constitute validation.
  Do not add personal infrastructure details to this public repository.
  Run actionlint after workflow changes.
  Add `abi3t-py315` only when 3.15t builds can be tested: enabling it alongside
  3.14t breaks maturin's cross-compilation config by raising the implied
  minimum interpreter version. Add size-increasing dependencies cautiously;
  thin-LTO abi3 wheels built from v0.14.0 sources are 6.4–7.4 MiB depending on
  platform, versus 6.8–7.9 MiB for the published v0.14.0 wheels.
- Native wheels and sdist builds share `rust/Cargo.toml`'s release profile:
  thin LTO, one codegen unit, and `panic = "unwind"`, which PyO3 needs to turn
  Rust panics into Python exceptions. The 2026-10-17 comparison against Cargo's
  default release profile built all five GitHub-hosted release targets with
  release.yml's two Cargo jobs: abi3 wheels shrank 0.2–11.3% and installed
  extensions 3.7–18.3%. Interleaved pylopdf workloads from `bench/run.py` on
  the Linux and Windows runners, plus a quiet local macOS arm64 machine, were
  faster for extraction (6–14%), Markdown (4–9%), merge (3–7%), and
  rasterization (2–4%); PNG encoding and `render_pages` stayed within 5%.
  macOS runners were too noisy for single-digit comparisons. Fat LTO saved
  another 0.5–2.3 percentage points of wheel size and 1.5–4 points of
  extraction time, but slowed the merge benchmark by 7–12% on both Linux
  targets and PNG encoding by 7–8% on Linux x86_64. Repeat that comparison
  before switching profiles. Clean compile steps took 3m29s–10m18s per target,
  versus 2m33s–10m25s with the default profile. On a 12-core development
  machine, an incremental extension rebuild took about 35 seconds instead of 10.
- Native release wheels keep their symbol tables. `rust/Cargo.toml` leaves
  `strip` unset, which Cargo resolves to `debuginfo` because release builds have
  no debug info, and release.yml passes no maturin `--strip`. The 2026-10-18
  comparison built the v0.14.0 sources with the thin-LTO profile for all five
  GitHub-hosted release targets, with maturin `--strip` and with
  `strip = "symbols"`: abi3 and cp314t wheels shrank 4.8–7.1% (0.34–0.49 MiB)
  and installed extensions 12.4–17.7% on Linux and macOS. Windows did not change
  because the MSVC linker ignores `strip` and keeps symbols in an unshipped PDB.
  maturin `--strip` left every code section byte-identical, and interleaved
  benchmarks and import time stayed within runner noise. Cargo hashes profile
  settings into crate metadata, so `strip = "symbols"` also changed code layout
  on all five targets, including Windows. Stripping removed the names that
  native diagnostics read. `RUST_BACKTRACE=1` printed no frames;
  `RUST_BACKTRACE=full` showed pylopdf frames as `<unknown>` on Linux and as
  `PyInit_pylopdf_core` on macOS; gdb, perf, and py-spy `--native` lost every
  pylopdf name on Linux; and `sample`, crash reports, and Python 3.14
  faulthandler C stacks lost them on macOS. Panic messages and source locations
  survived; Windows wheels already offer only those. Stripping keeps the Mach-O
  UUID and macOS caches symbolication by UUID, so compare stripped and
  unstripped macOS builds on separate runners; Linux linker stripping also
  changes the GNU build ID. Revisit stripping only together with a way to
  symbolize reports from published wheels.
- Pyodide 0.28.3 uses a static wheel built by `tools/build_pyodide.sh` with
  exact Python 3.13.2, Emscripten 4.0.9 and its Node.js 20.18.0, Rust 1.95.0,
  pyodide-build 0.30.7, maturin 1.14.1, and hashed build dependencies. The
  builder first verifies and smoke-tests the runtime-native
  `cp310-abi3-pyodide_2025_0_wasm32` tag, then uses wheel 0.47.0 under the same
  `SOURCE_DATE_EPOCH` to replace it with the PEP 783
  `cp310-abi3-pyemscripten_2025_0_wasm32` publication tag. Only the latter may
  remain in the release artifact directory because PyPI no longer accepts the
  legacy tag. Rust v0 symbol mangling avoids invalid legacy Emscripten exports.
  The PyEmscripten build pins fat LTO and one codegen unit for the single
  extension module through `tools/build_pyodide.sh` environment overrides,
  independent of the native thin-LTO release profile.
  The wheel must import `env.__cpp_exception` and must not require a
  wasm-bindgen shim. CI and release builds must also pass
  `tools/smoke_cloudflare.py`, which pins workers-py 1.15.0 and Wrangler
  4.114.0, builds the bundle, starts local `workerd`, and requires the
  module-scope `import pylopdf` in the example to serve `/health`. Emscripten
  enables PyO3's foldhash-backed `hashbrown` maps because its normal class
  builder uses Rust `RandomState`, while Cloudflare denies entropy during
  module startup. Native targets retain PyO3's standard backend. Neither target
  enables a lopdf date backend because pylopdf keeps PDF dates opaque; the
  default `chrono-clock` feature would pull js-sys imports into Emscripten.
  Emscripten also excludes lopdf's `rayon` feature because the global rayon
  pool cannot create workers there. `render_pages` keeps its public contract
  but runs serially; native targets retain bounded rayon execution. Emscripten
  also omits RTen inference while exposing an API-compatible `_OcrEngine` stub
  that raises `OcrError` and directs callers to external OCR plus
  `Page.insert_ocr_text_layer()`. Keep artifact sections, staged Pyodide
  startup/workload timing, linear-memory checkpoints, and Wrangler bundle
  measurements in CI. The 2026-07-26 LTO artifact is a 3.772 MiB wheel and
  3.817 MiB compressed Cloudflare bundle; it fits the paid plan, not the Free
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
  `render_pages(workers=1/4)`, virtual-filesystem save, merge/select, typed
  error recovery, and lopdf cross-reference recovery reporting. Add only small
  redistributable PDFs listed in `tests/assets/real_world/README.md`; non-PDF
  inputs such as the existing CJK font may be supplied from already licensed
  repository assets. Do not claim
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
  traverse structure but must not decode streams or invoke hayro. Direct-object
  depth validation and complexity inspection share an iterative traversal
  whose stack grows fallibly, including for wide dictionaries and stream
  dictionaries; allocation refusal raises `PdfError` without partial facts.
  Bounded pylopdf-owned lopdf decode entry points inspect `/Filter` directly
  before its infallible materialization and reject more than 16 sequential
  layers with `stream_filter_count`; Form imports, text replacement,
  attachment/XMP reads, and load-time decompression validation share this
  boundary. Image compression inspects its required single filter without
  materializing a filter vector.
  Page-content decompression indexing reuses the drawing path's bounded,
  cycle-aware `/Contents` inspection rather than lopdf's unbounded reference
  materialization, and grows its unique-ID set fallibly.
  All public page indexing must use the iterative borrowed-frame page-tree
  walker instead of lopdf's `get_pages` or page mutation helpers that call it.
  This includes page count and lookup, complexity, render snapshot validation,
  TOC/link reverse indexes, annotation sharing checks, Form imports, and every
  page-structure edit. The walker rejects reused Page/Pages references and
  cycles, caps indirect `/Kids` resolution at 32 references and internal-tree
  depth at 256 levels, bounds visited edges by the indirect-object count, grows
  ordered and reverse indexes fallibly, and stops `max_pages` traversal at the
  first over-limit page. `delete_pages` plans the complete retained order
  before mutation; append, copy, and import rebuild from a prevalidated order
  so indirect root Kids are preserved rather than silently replaced.
  `_Document.doc` is an `IndexedDocument`: page count, page lookup, and the
  TOC/link reverse page numbers reuse one walk's page index, built lazily by
  the first read, so per-page access is amortized O(1) instead of a complete
  walk per call. Its `DerefMut` drops the index before any mutable document
  access, and the wrapper lives in a private module, so no edit can read or
  retain an index built from a partially edited tree; never bypass it.
  `set_page_entry` is the only mutation that keeps the index, and only for
  `/Rotate` and the five page boxes, which the walk never reads, so rotate- and
  crop-every-page loops stay linear; any other key drops it. Failed walks are
  never cached, so a malformed tree raises on every access. Other edits still
  rebuild the index on the next access, so drawing and annotation loops pay one
  walk per edited page.
  Keep
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
  `Pixmap.samples` is the one-copy portable fallback. `Pixmap.save` streams PNG
  encoding directly to disk while the GIL is released rather than retaining a
  second completed PNG. It uses an unpredictable, exclusively created
  same-directory sibling and atomically replaces the requested path only after
  a complete write. Preserve existing regular-file permissions and final
  symlinks, clean up temporaries on failure, and map errors to `PdfError`.
- Concurrent operations on distinct `Document` objects are supported.
  Concurrent external calls or edits on the same `Document` are not; `Page`
  shares its parent's restriction. `Document.render_pages` is the supported
  bounded same-document parallel read boundary.
## Known pitfalls

- lopdf 0.45 keeps a rebuilt table's newest trailer verbatim, including its
  `/Prev`, and its full writer emits trailer keys as-is. pylopdf removes
  `/Prev` and `/XRefStm` after reconstruction; an upstream fix is a
  contribution candidate.
- lopdf's cross-reference reconstruction rescans the remaining input for every
  unterminated `stream` keyword. Keep the `recovery_scan_size` pre-scan until
  upstream makes that scan linear.
- lopdf's `traverse_objects` tracks visited IDs in a `Vec`, so every lopdf
  helper built on it is quadratic in object count. pylopdf replaces
  `prune_objects` and `renumber_objects_with`; do not call them or the other
  helpers built on it (`delete_object`, `delete_zero_length_streams`, and
  lopdf's own `delete_pages`).
- lopdf's `renumber_objects_with` keeps the number of a reference to a missing
  object, so after an import it resolves to whichever object takes that
  number. pylopdf's import renumbering writes null instead. The target's own
  references are not rewritten: one that names a number above the target's
  `max_id` still resolves to the object that a later import or allocation
  receives. lopdf also derives `max_id` from the highest cross-reference
  entry, while it stores each loaded object under the number in its own header
  and adds object-stream members missing from the cross-reference table. In a
  malformed file such an object can sit above `max_id`, and a later
  allocation replaces it.
- The pre-commit `validate-pyproject` hook with `trove-classifiers` validates
  classifier existence. v0.4.0 was rejected by PyPI because of the invalid
  classifier `Topic :: Text Processing :: Markup :: PDF`.
  Do not add `validate-pyproject-schema-store`; it raises `UnboundLocalError`.
- Synchronize the version manually in three places: `pyproject.toml`,
  `rust/Cargo.toml`, and `src/pylopdf/__init__.py`.
- Release wheels need installation and execution checks on all five supported
  GitHub-hosted target architectures, including free-threaded Python.

## Release procedure

1. Update the version in all three locations, add the changelog entry, commit,
   and push.
2. Run `git tag -a vX.Y.Z -m "..." && git push origin vX.Y.Z`.
3. GitHub Actions (`release.yml`) builds abi3 and cp314t wheels plus the sdist
   for five platforms and publishes through PyPI Trusted Publishing.

Locale/script font wheels have a separate release process. Update the selected
`fonts/pylopdf-fonts-{locale}/pyproject.toml`, then push a
`fonts-{locale}-vX.Y.Z` tag to run `release-fonts.yml`. Supported release codes
are `ar`, `he`, `hi`, `jp`, `ko`, `th`, `zh-cn`, and `zh-tw`. Every PyPI
project requires a Trusted Publisher with workflow `release-fonts.yml` and
environment `pypi`. Publish every wheel referenced by a main-package extra
before releasing that main package.

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

- Current phase: v0.14.0 release preparation on 2026-10-13. The review prioritizes
  page geometry/navigation correctness, predictable rejection before mutation,
  supported-floor typing/rebuilds, and GitHub-hosted verification that finishes.
  `ROADMAP.md` lists stabilization, real-document quality, maintainability, and
  v1.0 readiness in execution order. The 0.13 line completes the fallible-allocation and
  bounded-traversal campaign across the public surface, so allocator refusal
  surfaces as `PdfError` without partial results, unifies public page indexing
  behind one bounded iterative walker, and recovers narrow-column reading
  order with drift-following gutters, raising the bundled 1880 patent
  similarity proxy from 0.320 to 0.581. The documented 0.13 candidate API
  surface is checked deterministically across every native Python test lane,
  with the post-v1.0 SemVer and deprecation contract published in four
  languages. The separately versioned OCR model package remains independently
  gated. Incremental save was rejected after OSS analysis and remains on the
  watchlist. v1.0 has no target date: continue the 0.x line until sustained
  field feedback, meaningful external adoption with roughly 1,000 GitHub stars
  as an indicative signal, and further product refinement justify the API
  freeze. Stars are not a mechanical release trigger.
- lopdf 0.45 includes the upstream lopdf#535 fix (lopdf#537). pylopdf has
  extracted through hayro since v0.7, so the fix matters only to lopdf's own
  content parser.
- See [CHANGELOG.md](CHANGELOG.md) for completed history.
