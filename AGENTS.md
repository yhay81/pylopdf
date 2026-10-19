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
- `uv run pytest` / `uv run ruff check .` / `uv run ty check`
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
  font with no ToUnicode CMap, upstream gap LaurenzV/hayro#1331, which hayro
  `main` narrows after 0.8.0 with a glyph-name fallback, LaurenzV/hayro#1512)
  are skipped;
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
- Native OCR uses RTen 0.27 with only its `rten_format` feature. RTen 0.27
  deprecates the `.rten` format (robertknight/rten#1470) and no longer
  enables it by default; a release that removes it would require ONNX model
  files and RTen's ONNX parser in the core wheel. The core wheel
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
  full-page display coordinates. Clipping reduces the rasterized area and the
  OCR detector input; hayro still interprets the complete page. The first
  engine returns
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
  input bytes when `object_recovery` keeps them, as described below, unless the
  next bullet prefers the serialization, and falls back to a lopdf
  serialization when hayro rejects them or resolves other pages than the
  page-tree walker, compared by object ID in page order. When the
  serialization still disagrees, or when hayro could not read the trailer,
  catalog, or root page-tree node (the next bullet), rendering uses a copy
  whose root lists exactly the walker's pages with inherited attributes
  materialized: hayro 0.8 reads untyped or direct kids as pages, which the
  walker skips, and leaves out a kid it cannot read, and hayro 0.7 also ended
  `/Kids` at the first entry that is not a dictionary. The editable and saved
  page tree never change for this. A recovered load retains no original bytes:
  hayro would recover the damaged input independently and can disagree, for
  example by rolling an incremental file back to its first revision.
  Editing methods must call `invalidate_hayro_pdf`, which also discards
  the original-byte fast path and the link-resolution cache; edited state must
  always be reflected in rendering and link reads. An edit that cannot change
  an object that drawing placed calls `invalidate_hayro_pdf_keeping_placements`
  instead, as described with placement reuse below. The page index needs no
  call: mutable document access drops it, as described with the walker below.
  `DocumentLimits.max_interpretation_size` bounds both retained original input
  and the complete reserialization after edits or decryption, or for a
  rendering copy (missing text-markup appearances, the flattened page tree, or
  the cut nesting). Under a finite limit, serialize before constructing the
  rendering copy. The bounded writer must not
  install a partial hayro cache. Original input above a finite limit retains
  only its size and limit until first-interpretation refusal; admitted
  in-memory input copies use fallible exact allocation. Failures use
  `interpretation_size`. Its
  compatible default is `None`, while `DocumentLimits.web()` sets 64 MiB.
- hayro-syntax 0.8 reads no array or dictionary nested more than 64 levels
  deep, counting the object that holds it (`MAX_OBJECT_NESTING_DEPTH`), and
  treats the whole object as absent; hayro 0.7 recursed without a bound, and
  lopdf reads objects nested up to 100 levels. A page dictionary nested that
  deeply drops out of hayro's page list, a trailer or catalog keeps hayro from
  opening the document, and a root page-tree node makes hayro collect pages by
  scanning every object, which can list the walker's pages in order without
  the attributes they inherit (hayro alone renders the 300-point pages of such
  a test document as A4). `rust/src/read_depth.rs` sends a document whose
  trailer, catalog, or root nests that deeply straight to the flattened
  rendering copy, and that copy drops every entry that nests one of them or a
  page too deeply, counting levels as hayro does: the dictionary is the first,
  a container inside it the second, and a scalar or reference no level.
  `_Document.pages_too_deep` records the pages that lost an entry, and
  `hayro_view_for` refuses every selection that includes one with `LimitError`
  code `object_depth`, the code of `DocumentLimits.max_object_depth`, before
  the content guard runs. Other pages stay usable, and the editable and saved
  document keep everything lopdf read. Any other object nested that deeply
  reads as absent, as in hayro, and the guards see it as hayro does.
  `DocumentLimits.web()` already refuses such documents at load, because
  `max_object_depth` counts a scalar as a level. `tests/test_read_depth.py`
  pins the 64-level boundary and the trailer, catalog, root, and
  inherited-resource cases.
- hayro-syntax 0.7 and 0.8 (and its `main` of 2026-10-18) parse an object
  stream's member table, the pairs of object number and offset that head its
  data, anew for every lookup of a member and keep nothing, at about 15 ns per
  member of the container. Reading most of the members of a container of N
  therefore costs about N² of those, in `Pdf::new` (every page lookup), in the
  content guard's walk, and in hayro's interpretation. Measured on macOS arm64
  with the thin-LTO build: pdf.js `bug1978317.pdf` (one page, 32,768
  annotations that are all members of one 65,542-member container) took 61 to
  77 s for its first `get_text`, and a 723-page, 4.9 MB book whose objects
  share one 14,947-member container spent 0.68 s in `Pdf::new` alone, took
  0.66 to 0.83 s for its first page, and 16 to 20 s for all pages (2.8 s of
  them in the walk); the ranges are run-to-run variation on a busy machine. A
  graph written by lopdf has no object streams, so hayro finds every member in
  constant time: 0.19 to 0.24 s, 0.03 to 0.05 s, and 1.5 to 1.9 s for the same
  three calls, and 1.7 s against 0.17 s for the 74 pages of the 7,421-member
  manual. `rust/src/object_stream_work.rs` reads the declared `/N` of every
  object stream, and `hayro_view` serializes the graph first, within
  `max_interpretation_size`, and parses that when the largest is at least
  4,096 (every lookup then costs about 60 µs, many times the lookup itself)
  and the squares of all of them sum to at least eight times the input length
  (15 ns per rebuilt entry against about 7 ns per byte to serialize, with only
  some members looked up). The retained original bytes stay the fallback when
  the serialization is refused or lists other pages than the walker, so a
  document that parsed before still parses. Of 5,707 distinct local PDFs three
  qualify (the two above and a 74-page manual with a 7,421-member container).
  The other 5,704, among them 758 with a container of more than 100 members
  and 18 of more than 1,000, keep their original bytes, as does the bundled
  corpus (at most 149 members per object stream). The largest-container
  condition keeps a tagged PDF of 101 containers of 500 members on its
  original bytes: its squares sum to those of one 5,000-member container, yet
  it costs hayro no measurable time, because each lookup rebuilds only 500
  entries and most of its members are never looked up. Text, words, drawings,
  and images of the first three pages were identical on both snapshots for all
  33 local files with a container of 580 to 14,947 members, and the serialized
  snapshot was never slower for the first page or for 100 pages (1.0 to 18 and
  1.0 to 9.7 times faster). Pixmaps at scale 0.5 differed for two of them: one
  pixel by one level, and 3,495 of 124,740 pixels by up to 255 along the thin
  lines of the book's cover page, identical at scale 1.0. lopdf reads reals as
  `f32`, as for every edited, recovered, or encrypted document. The choice
  changes only what hayro reads, never what the guard checks, because the walk
  follows the snapshot that hayro will interpret; the trace oracle below
  agrees. Cold first calls on the bundled corpus (`get_text`, `to_markdown`,
  `render_pages`, and `get_drawings`; 15 interleaved rounds of CPU-time
  minimums, the same extras installed) took 757.4 ms on `main` and 754.9 ms
  with this choice, so reading every document's object stream counts once
  costs nothing measurable. `_Document.snapshot_source()` reports `"none"`,
  `"original"`, or `"serialized"` so that tests can pin the choice, which
  `tests/test_object_stream_snapshot.py` does at 4,095 and 4,096 members, for
  a hundred containers of 500, and on either side of the input-length
  condition. An upstream fix is a contribution candidate: keeping the table
  per object stream in `XRef` (about 50 lines) made the first `get_text` of
  `bug1978317.pdf` 0.17 s and of the book 1.5 s on the original bytes. Remove
  `object_stream_work.rs` and its tests once the pinned hayro keeps the table.
- `rust/src/object_recovery.rs` decides at load whether the original bytes
  describe lopdf's graph. hayro resolves those bytes through the
  cross-reference data: it reads a number that no entry lists as null, and the
  first mismatched or unparseable entry it reads makes it rebuild its whole
  table by scanning object headers, so its view of a malformed file depends on
  read order. lopdf stores an uncompressed object under its header number even
  when the entry names another, skips objects it cannot parse, and adds
  object-stream members that no entry lists, raising `max_id` over them. A
  load whose objects match the in-use entries one for one keeps its bytes
  after a single merge pass; lopdf records no free entries, and well-formed
  files, incremental updates, and object streams all match. Otherwise loading
  re-parses each skipped uncompressed object whose header matches its entry
  with a bounded, iterative parser that reads a bare keyword value such as
  `/Type XObject` as null, stops at lopdf's 100-level nesting bound, never
  reads past the next entry's offset, and takes stream data only from a valid
  `/Length` or a sole `endstream` that `endobj` follows. It then walks every
  reference reachable from the trailer, drops objects that no entry locates
  and nothing reaches, such as members that a damaged object stream decodes
  under arbitrary numbers, and lowers `max_id` to the highest entry or
  remaining object when no reachable reference names a higher number, because
  lopdf's cross-reference writer runs in time linear in `max_id`. The bytes
  stay only when the walk meets no recovered object, no object that no entry
  locates, and no in-use entry that lopdf could not load. lopdf skips objects
  nested deeper than 100 levels, so a reachable one also kept them from hayro
  0.7's unbounded recursive parser; hayro 0.8 reads none deeper than 64.
  Audit and recovery buffers grow fallibly,
  and refusal raises `PdfError`. Encrypted and recovered loads skip the pass
  and already render from the graph. The pass runs before
  `normalize_object_numbers`, because it compares lopdf's numbers with the
  cross-reference entries; a renumbering load also renders from the graph, and
  dropping unreachable debris first spares most such loads the move.
- Content streams are cleared for nesting depth before hayro interprets them.
  hayro 0.7 skipped and read arrays and dictionaries in content streams by
  unbounded recursion (LaurenzV/hayro#1347), so a stream with about 300,000
  nested `[` overflowed the stack and killed the process on the first
  extraction, rendering, drawing or image read, search, Markdown conversion, or
  image compression. It did so in page content, Form XObjects, annotation and
  widget appearances, tiling patterns, Type 3 glyph procedures, and soft-mask
  groups, and `DocumentLimits.web()` did not help because lopdf never parses
  content at load. hayro 0.8 stops at 64 levels and silently leaves out the
  deeper operands, so the check now turns that truncation into a typed
  refusal, and its walk still finds the roots of the colour space and function
  check below, which hayro 0.8 needs as much as 0.7 did. Every path that lets hayro
  interpret a page must start with `_Document::hayro_view_for`, which builds the
  snapshot and has `ContentGuard::check` (`rust/src/content_guard.rs`) clear
  the pages first: `One`, `Many` for explicit batches, `All` for
  `compress_images`. A refusal raises `LimitError` with the stable code
  `content_nesting_depth` before any interpretation, cache insertion, or
  output. The bound is `MAX_NESTING_DEPTH` = 64 levels of arrays and
  dictionaries, the one hayro 0.8 uses, and is unconditional rather than a
  `DocumentLimits` field: it prevented a crash on hayro 0.7 and prevents a
  silent truncation on 0.8, rather than bounding resource use.
  The guard decodes with hayro's own `Stream::decoded`, never with lopdf: the
  two decoders and stream-extent rules can disagree on damaged input, and only
  hayro's bytes reach its parser. Page content comes from `Page::page_stream`,
  which hayro caches, so interpretation reuses that decode; other streams
  decode once more. The walk mirrors the four places where hayro 0.7 and 0.8
  parse content operators: the joined `/Contents`, Form XObjects (`Do`,
  annotation `/AP /N`, ExtGState `/SMask /G`), tiling pattern streams, and
  Type 3 `/CharProcs`, including fonts that only an ExtGState `/Font` entry
  selects. hayro 0.8 draws an `/AP /N` state dictionary as the state that
  `/AS` names, else `/Off`. The walk follows `/N` as `NormalAppearance` and
  queues every stream of a state dictionary (`AppearanceStates`, each state as
  `ContentStream`): a superset of hayro's choice that needs neither `/AS` nor
  hayro's reader chain to resolve it, and costs real forms nothing because a
  check box or radio button has two states. hayro 0.8 resolves a name in the
  `/Resources` of the stream it interprets; a form or glyph procedure without
  any falls back to the resources it is drawn with (a glyph procedure to its
  font's first), and a pattern has only its own, where hayro 0.7 fell back
  through every enclosing stream and the page. Either way the walk visits
  every entry of every reachable resource dictionary instead of the names a
  stream uses; only `/Subtype /Form`
  entries of `/XObject` count, because images are costly to decode and never
  content. The check is lazy and per page. Streams, fonts, graphics states,
  and dictionaries shared with an earlier page are skipped. A dictionary is
  skipped only in the role it was walked in (a `/Resources` dictionary, an
  `/XObject`, `/Pattern`, `/Font` or `/ExtGState` category, or one font),
  because one object may serve several roles, as in
  `/Resources << /XObject 5 0 R /Font 5 0 R >>`, and is identified by the
  address of its bytes, stable because decoded object streams live as long as
  the snapshot. Never identify one by `Dict::obj_id`, which names the object
  that contains a direct dictionary: all direct fonts in one indirect `/Font`
  object share it.
  The walk records every reference it follows in `followed`, by `Role` and
  before it parses the object that the reference names (`first_follow`, behind
  `follow_dictionary`, `follow_stream`, `follow_object`, and `follow_array`,
  the only ways the walk reads one), whether or not that object turns out to
  be readable. Parsing a dictionary builds a hash map of its keys, and the walk
  lists every entry whether or not content draws it, so parsing the object once
  for each entry that names it multiplies their sizes. 3,000 entries that name
  one object of 20,000 keys (0.23 MB) kept the first `get_text` busy for 3.3 s
  (a font), 3.8 s (a form), 5.8 s (a tiling pattern), and 0.7 s (an ExtGState
  `/Font` array), because the walk recorded a stream or font only when it
  processed the queued item, after every entry had parsed the object and
  queued a copy of it. All 3,000 copies waited in the queue at once: the form
  peaked at 3.0 GB and the font at 3.3 GB of memory, against 41 and 47 MB
  now. Time and memory grow with the product of the two sizes, so a few
  megabytes take minutes and exhaust a small host, where `main` needs
  milliseconds.
  Annotations show the cost of the walk alone only when they are hidden
  (`/F 2`): hayro reads the `/AP /N` of every annotation it draws and resolves
  every `/Annots` element itself, so the walk costs about as much as
  interpreting the page there, and the `/Annots` loop is left as it was.
  hayro-syntax rebuilds an object stream's member table on every lookup of a
  member (see the snapshot bullet above), so a page with thousands of
  annotations in one large container is quadratic in both passes: pdf.js
  `bug1978317.pdf` (1.5 MB, one page) lists 32,768 annotations that are all
  members of one 65,542-member stream, and its first `get_text` took 61 to
  77 s, half of it in the walk's `/Annots` loop (33.8 s of 67.7 s in the run
  that timed the walk) and half in hayro's own interpretation. A generated page
  whose N annotations are members of one N-member stream shows the same shape:
  at N = 4,096, 8,000, and 16,000 the first `get_text` took 0.6, 2.2, and 8.6 s
  of CPU time, the walk alone 0.3, 1.1, and 4.3 s, each time four times the
  step before. Such a document now reads from its serialized graph, which has
  no object streams, and takes 0.19 to 0.24 s (0.02, 0.04, and 0.07 s for the
  generated pages, the walk alone 0.01, 0.01, and 0.03 s). The loop keeps
  resolving every element through hayro and not through
  lopdf's graph, although the graph holds the same dictionaries without a
  rebuild: `object_recovery` verifies only that a compressed entry exists, not
  the container and index that hayro reads, and the object streams' contents
  could be parsed differently by lopdf, so a walk that took `/AP` from the
  graph could leave unscanned a stream that hayro interprets. Reading the
  elements through a table of its own would speed up only the walk's half and
  would have to replicate hayro's table parser and xref lookup exactly. Refusing
  a page after its last annotation measures the walk alone, because the
  refusal comes before hayro interprets anything; poison the last appearance
  and time the refusal to separate the two passes. A role is what the walk does
  with the object, never where it was found, and a reference is skipped only in
  the role it was followed for. A
  stream that was cleared as content from a tiling pattern entry says nothing
  about the colour space it gives an image: the first version of the `/XObject`
  loop skipped any stream that was already cleared, so a 20,000-deep chain
  behind such an image still crashed the process. `streams` stays the record of
  decoded content, because several roles can queue one stream and the walk
  decodes it when it takes the first of them from its queue.
  Resolve every reference through `XRef::get` (`Fresh` in
  `content_guard.rs`) instead of through a dictionary obtained by an earlier
  step. hayro keeps the chain of objects being read in a dictionary's reader
  context and refuses a reference back into it as a cycle, so the same
  dictionary resolves fewer references the longer the path that reached it. A
  walk that visits each object once and lets the first path decide records a
  dictionary whose references failed on a long path and then overlooks what
  hayro's shorter path resolves when it draws. Iterate arrays with the typed
  `iter::<T>()` that hayro uses, never `Array::raw_iter`, which unwraps the read
  of every element that skipping accepted and so panics on `[+ ]`; the one
  element that is read before its reference resolves, the first of a graphics
  state's `/Font` array, comes from `graph_guard::Elements`, which steps over
  such an element. State
  belongs to the snapshot: `invalidate_hayro_pdf` and every refusal or allocation failure
  reset it, so nothing recorded before a failed walk can vouch for a stream
  the walk did not finish. No
  decompression limit is re-applied: load-time validation already measured
  every stream of an unencrypted document under the configured budgets, and
  the guard decodes only streams that interpretation would decode anyway or
  that a resource dictionary lists. Cold first-call cost on the bundled corpus
  is 1.0% in total (interleaved 5-round minimums, thin-LTO builds) and at most
  9% on a two-page form-heavy document (`f1040.pdf`), where hayro resolves
  annotation dictionaries from object streams once for the walk and again to
  draw them.
  `rust/src/content_scan.rs` decides without recursion whether the decoded
  bytes can drive hayro's parser deeper than 64 levels. One small lexer per
  hypothesis about hayro's tokenization models whitespace, comments, literal
  and hexadecimal strings, names, `[ ]`, and `<< >>`, and never abandons input
  that hayro would reject. A leading sign or dot that no digit follows is the
  number 0 to hayro, which starts the next token behind it, so `-.BI` is a
  number and the keyword `BI`; the lexer ignores such leading bytes and the
  fast path looks back over them. Two constructs hide bytes from tokenization.
  Inline images: after `BI` hayro reads key/value pairs up to `ID` at a key
  position, skips one whitespace byte, and searches for an `EI` followed by
  whitespace with a heuristic that parses the rest of the stream, so every
  `EI` that hayro does not certainly reject (a non-ASCII byte before the next
  `EI`) starts a fresh lexer behind it. That search also looks for the bytes
  `BI` wherever they occur, inside a string or comment as well, and reads an
  inline image dictionary behind each pair, discarding the result. Behind the
  first candidate every such pair therefore starts a trial lexer, and the
  trial ends where hayro's read must end: at a key position anything but
  whitespace, a comment, a name, or the tag `ID` fails the read, so prose and
  binary bytes cost a byte or two. When no `EI` is accepted hayro keeps
  going: it tokenizes from the data start if the stream has no `EI`, and
  otherwise from behind the last `EI` it examined, which is the last one or one
  byte later when a regular byte follows the one before it. Those positions
  start lexers as well. Stream objects: a dictionary followed by `stream`
  makes hayro skip an opaque body, and content never holds one, so such a
  stream is refused. At most eight lexers stay alive and equal ones merge; a
  stream that needs more is refused as ambiguous. All three refusals share the
  code. `scan_alone` runs the only lexer through the stream until the first
  image and skips every byte that cannot change it, about 1 GB/s against
  140 MB/s byte by byte; it must stay equivalent to `Lexer::step`, which is the
  specification, and must reset the token in progress when it recognizes `BI`
  because the bytes it skipped leave a stale one. Pathological 64 MiB inputs
  (repeated `BI ID`, `BI/`, or `EI`, endless comments after `>>`) scan in under
  two seconds, so keep every search bounded by the next scheduled position.
  The model was validated against hayro-syntax 0.7.2 itself with scratch
  harnesses, not stored in the repository. hayro-syntax 0.8.0 tokenizes
  content as 0.7.2 does apart from its 64-level bound and how it reads a
  number with more digits than an `i64` holds, so the model still applies; the
  scanner oracles were not rerun against 0.8.0, whose own bound turns a gap
  into a truncation rather than a crash. They include `content_scan.rs` by
  path and a vendored copy of the crate whose recursive container parses
  (`Array::skip`, `Dict::skip`, and `<<` dictionary reads) count their depth in
  a thread-local, and they require that any stream whose parse reaches 65
  levels was refused. Count the depth rather than follow the operators that
  hayro yields: the dictionary reads of the `EI` search discard their result,
  so a marker operator behind a deep array, or an overflow of a 128 KiB thread
  stack, can miss them or find them only by chance. The harnesses also require
  the optimized `scan_alone` path and a byte-wise reference derived from the
  same file to agree on every input. Gaps surfaced one by one while the model
  grew: without the fall-through resumption 17 of 3,000 random streams got
  through, reading `-.BI` as one token let 1 of about 700,000 through, and not
  modeling the `BI` search let 2 of 400,000 through (found by the
  stack-overflow campaign, then pinned by the depth oracle in 32 of 5.4
  million short sequences). So keep growing the generators and rerun every
  oracle after any change to the lexer. The final scanner passes 8.7 billion
  exhaustive token sequences, every sequence of up to four to ten tokens
  (depending on the vocabulary) over vocabularies of inline image keywords,
  strings, comments, signs, dots, brackets, and dictionary delimiters followed
  by a balanced 70-deep array, dictionary, or mixed structure, hayro reaching
  65 levels in 829 million of them, and 16 million random, structure-aware, and
  mutated corpus streams, with no violation and no disagreement between the
  optimized and the byte-wise scan. Two end-to-end checks agree: 400,000
  streams on a 128 KiB thread stack, 92,907 of which overflowed hayro, were all
  refused, and 40,000 streams through the Python API on a 512 KiB thread stack
  never crashed the guarded build, while `main` crashed on 2,354 of 20,000.
  Those oracles test the scanner, not the walk. Two more harnesses test the
  walk. The first checks the property the guard exists to provide: copies of
  hayro-syntax and hayro-interpret log a hash of every content stream hayro
  starts to interpret (page, form, tiling pattern, glyph procedure), the guard
  logs each stream it scans, and every non-empty interpreted stream must have
  been scanned. It needs no deep content and no crash, so it runs on real
  documents, where hayro interpreted 48,350 content streams in the first six
  pages of 3,000 varied PDFs and the guard had scanned all of them, and on
  mutated document graphs, thousands per minute. The second captures the
  document builders of `tests/test_content_nesting.py` and the bundled corpus
  as object graphs and applies random rewrites that keep what hayro can reach
  (indirect and direct swaps, merging dictionaries across roles and into
  `/Resources`, hoisting resources into the page tree, decoy fonts and forms,
  clones, self references, object-stream storage). With a deep leaf stream it
  runs on a 512 KiB thread stack, where a crash of the guarded build means a
  path hayro takes and the walk missed; `main` crashes on about 90% of the
  graphs, so the oracle has power. Both found the shared-role, direct-font,
  and reader-chain bypasses: the first guard left a stream unscanned in 27% of
  the mutated graphs and crashed on 254 of 1,000, the role and font fixes
  alone still crashed 3 of 5,000, and the final walk left no stream unscanned
  in 183,000 graphs and crashed on none of 46,000. Rerun both after any change to
  the walk. The read-once rewrite (`followed`) passed them again: hayro
  interpreted 51,314 streams in 26,000 mutated graphs (20,000 from the test
  placements, 6,000 with the bundled corpus as extra seeds) and the guard had
  scanned every one, and none of 4,000 graphs crashed. A variant whose
  `first_follow_id` ignores the role, one record shared by all of them, leaves
  a stream unscanned in 343 of 2,000 graphs (17%), so these oracles have power
  against the mistake that rewrite could make. The resource walk's harness
  (below) gave the same verdict on `main` and on the rewrite for each of
  1,500,000 random documents (1,000,000 as generated and 500,000 with only
  well-typed slots), compared through a hash of every verdict. Moving large
  object streams to the serialized graph (the snapshot bullet above) passed
  the stream oracle as well: hayro interpreted 9,732 streams in the first six
  pages of 700 real documents, 98 in the 15 openable corpus files, 91 in the
  three documents that choice selects, 11 in eight generated pages with
  4,096 to 20,000 members, and 21,720 in 11,000 mutated graphs (8,000 from the
  test placements, 3,000 with the corpus as extra seeds), and the guard had
  scanned every one. The hayro 0.8 port reran the stream oracle with a copy of
  hayro-interpret 0.8.0 that logs every non-blank content stream it starts,
  rendering the first six pages of each document from its original bytes:
  hayro started 189,858 such interpretations in 2,641 pages of the 1,437
  pdf.js corpus files it opens and 1,417 in the bundled corpus and the 691
  documents that the nesting tests build, and the guard had scanned every
  stream; a guard that skips `/AP /N` state dictionaries left 113 of those
  1,417 unscanned, so the oracle has power against the gap the port closed.
  The mutated-graph harness was not rerun.
  Keep a deterministic Python regression for each construct a harness found.
  The Python regressions in `tests/test_content_nesting.py` cover each place
  hayro interprets content, every public operation, the 64-level boundary, the
  inline image evasions, edits, shared streams, and the dictionary-sharing
  shapes above. A sweep of 2,821 varied
  real-world PDFs (59,611 pages) raised no refusal, and none of 60,111 page
  streams, 99 of which hold inline images, was refused.
  On a hayro upgrade, re-read every `TypedIter::new`, `UntypedIter::new`, and
  `Stream::decoded` call site; a new content-parsing site must join the walk.
  hayro 0.8.0 parses content at the same four places as 0.7 and adds only the
  `/AP /N` state selection above. The structures that are not content streams
  but recurse without a bound in hayro 0.7 and 0.8 alike are guarded by the
  next bullet.
- Colour spaces, shadings, and functions are cleared for chain depth and Type 4
  nesting before hayro builds them. hayro 0.7 and 0.8 build a colour space
  (`ColorSpaceType::new_inner`) and a function (`Function::new`) by recursion
  over the objects they name, with no bound: an Indexed or Pattern space names
  a base, a Separation or DeviceN space an alternate space and a tint
  transform, an ICCBased space the `/Alternate` of a profile it cannot parse,
  a stitching function (type 3) every entry of `/Functions`, and
  `parse_procedure_inner` calls itself for every `{` of a Type 4 program.
  Conversion, evaluation, and drop recurse to the same depth. About 20,000
  indirect objects that name each other in a chain, or 300,000 nested braces,
  overflowed the stack and killed the process on the first extraction,
  rendering, drawing or image read, search, Markdown conversion, or image
  compression, with or without `DocumentLimits.web()`. The ICCBased chain
  surfaced while building this guard and is the costliest per level. Every
  such path starts at `_Document::hayro_view_for`, where `ContentGuard` walks
  the roots (below) and hands each to `GraphGuard` (`rust/src/graph_guard.rs`);
  a Type 4 stream goes to `rust/src/postscript_scan.rs`. A refusal raises
  `LimitError` with the stable code `resource_nesting_depth` before any
  interpretation, cache insertion, or output. The bounds are unconditional,
  like the content bound: `MAX_CHAIN_DEPTH` = 16 nodes and `MAX_BRACE_DEPTH` =
  64 levels. Real documents stay far below both: while hayro interpreted the
  first 40 pages of 5,683 real PDFs (54,088 pages, 1,453 of them the pdf.js
  corpus), its colour space and function builders nested at most 3 deep and
  its Type 4 procedures at most 3, and the node count here is at most about
  twice the builder count because an ICCBased space is an array plus its
  profile stream. The chain bound was 32, the indirection bound that pylopdf
  follows elsewhere, until hayro 0.8, whose ICCBased fallback spends about
  4 KiB of stack per space in the thin-LTO build: the deepest accepted 32-node
  ICCBased chain needed a 116 KiB thread with hayro 0.8 against 52 KiB with
  0.7, close to the 128 KiB that musl gives spawned threads. At 16 nodes the
  ICCBased chain builds and draws on a 68 KiB thread and every other deepest
  accepted structure on the 52 KiB that a plain page needs (thin-LTO builds,
  macOS arm64, Python 3.13, 4 KiB steps); the regression runs them on
  256 KiB. With hayro alone and cargo's default release profile, the deepest
  chain that survives a 512 KiB thread puts hayro 0.8.0 at 1.55 KiB of stack
  per stitching level, 0.94 per Indexed, 0.87 per Separation, 5.69 per
  ICCBased space, and 0.36 per brace, against 2.10, 1.02, 0.93, 5.75, and
  0.36 for 0.7.1, so how pylopdf's build inlines hayro matters as much as the
  version. Re-measure through pylopdf's own build after a hayro upgrade or a
  profile change.
  The roots are every colour space and function that interpretation can
  build: the `/ColorSpace` and `/Shading` entries of every reachable resource
  dictionary, the `/Shading` of shading patterns (dictionary entries of
  `/Pattern`), the `/ColorSpace` and `/CS` of image XObjects (never their
  data), the `/Group /CS` of soft-mask groups, and the `/TR` and `/TR2`
  transfer functions of graphics states and soft masks, singly or as arrays.
  Shading `/Function` entries are single functions or arrays. A shading is not a
  node. Like the content walk, it visits every entry of a dictionary instead of
  the names the content uses, because hayro resolves names through the enclosing
  resources, and `first_visit` keeps its role in the key.
  The walk is iterative and kind-agnostic. A node is any array, dictionary, or
  stream; its children are the array, dictionary, and stream elements of an
  array, the entries of `/Functions`, and the `/Alternate` of a dictionary or
  stream. That is a superset of what hayro reads, whatever the role of an
  entry: hayro's typed `flex_iter` reads consume a reference that does not
  resolve to the requested type (`[/Pattern N 0 R]` reads `N 0 R` as a
  missing name and then finds no base), `Shading::new` builds the colour space
  before the function and skips the function when the space is invalid, and a
  Separation whose alternate space fails to build never builds its tint
  transform, so any model of the roles would undercount in some malformed
  case. Elements are enumerated with `graph_guard::Elements` and resolved one
  by one through `XRef::get`, so an element that fails to resolve is skipped
  without ending the array, as hayro's typed reads can step over it.
  `Elements` reads as `Array::raw_iter` does but steps over an element that
  does not read: `raw_iter` unwraps the read of every element that
  `Array::skip` accepted, so a bare `+` or a dictionary followed by `stream`
  panics it (205,000 of 1,188,000 random accepted arrays), while hayro's own
  typed iteration just stops there. The graph walk iterates arrays that hayro
  never does, so `raw_iter` could turn a renderable page into a
  `PanicException`; the content walk reads only what hayro reads and uses its
  typed iteration for `/Annots`, and `Elements` for the first element of the
  `/Font` array so that it can classify a reference before it parses it. No
  reachable panic was
  found (lopdf drops the page object of every one of 143,000 panicking arrays
  in four roles), and `Elements` equals `raw_iter` on every array where that
  does not panic. Every reference is read afresh
  (`resolve`, like `Fresh` in `content_guard.rs`): hayro's reader chain would
  cut a reference back into the path that reached a dictionary, and this walk
  remembers an object after the first path. Depth counts nodes: a Separation
  over a Type 2 function is two, and an ICCBased space with its profile stream
  is two. A `/Functions` array that is an object of its own is a node like any
  other array, one level more than a direct one, which costs real documents
  nothing and lets the walk remember it. The name that ends a chain is no node,
  though hayro still enters one frame for it, so hayro's frame count can be
  one higher than the node count. An object is identified by its reference,
  or, when direct, by the address and length of its bytes in the snapshot
  (valid for the snapshot's lifetime, as for dictionaries above); never by
  `Dict::obj_id`. Shared sub-graphs are measured once: the depth of every
  object whose complete sub-graph was measured is remembered, so a graph
  that names the same function twice per level costs the same as one that
  names it once, where hayro itself needs 2^levels builds.
  hayro cuts a chain where it comes back to an object it is still building
  (`ReaderContext::parent_chain`), so a self-reference or a short ring renders
  and only a long ring overflows. The walk is Tarjan's algorithm for strongly
  connected components, run iteratively: a simple path visits each node of a
  component at most once and cannot return to a component it left, so the
  depth below any node of a component is at most its size plus the depth of
  the deepest node that the component names outside it, and a node outside
  every cycle is a component of one with its exact depth. A component's bound
  depends on the graph alone, never on the order in which roots were walked,
  and is remembered like an exact depth. The walk refuses a path of 17 nodes
  at once and a component whose bound passes 16, which is over-conservative
  only for malformed cyclic graphs: a hub that 17 spokes name and that names
  each spoke back is 3 deep but one component of 18.
  A walk must never read an object twice. A document can name one large array,
  dictionary, or string from thousands of places, and the check lists every
  entry of every resource dictionary whether or not content draws it, so
  reading the object for each entry multiplies sizes where hayro, which never
  touches the unused entries, needs milliseconds. A reference is therefore
  classified by its identity (`Child::Reference`) against the walk and the
  remembered depths before the object is parsed, an object that cannot have
  children is remembered as a leaf of depth zero, and `ContentGuard` follows
  every reference by role before it parses the array, dictionary, or stream
  that it names (`followed`, described with the content walk above), so the
  shadings, shading patterns, and soft masks it hands to this walk, and the
  streams, fonts, and dictionaries of its own walk, are each parsed once. The
  first version of the walk remembered nothing for a cycle and parsed every
  referenced child before it looked the child up: 3,000 stitching functions
  that name one array of 20,000 sub-functions (0.6 MB), or that array as the
  `/Function` of 3,000 shadings, kept the first `get_text` busy for 50 s on a
  loaded machine, 3,000 `/Shading` entries that name one 20,000-key shading
  dictionary for 4 s, and 4,000 colour space entries that name a
  self-referencing array of 20,000 elements for 7 s. The same files take 2 to
  60 ms. `test_an_object_that_many_entries_name_is_read_once` pins nine such
  shapes with a deadline that the first version missed by factors of 4 to 17,
  and `test_a_content_object_that_many_entries_name_is_read_once` fifteen for
  the content walk (3.7 to 12); add a shape for every new kind of root and for
  every new reference the content walk follows. Memory is bounded by the
  objects reached, the path stack by the limit, and every collection grows
  fallibly.
  The small values that a visited object keeps behind a reference follow the
  same rule where the walk reads them itself: a form's `/Subtype`
  (`ContentGuard::is_form`, remembered in `form_subtypes`) and a stream's
  `/FunctionType` (`is_program`, remembered in `GraphGuard::programs`). Each
  reference is read for the first stream that names it and the outcome, `/Form`
  or not and 4 or not, is remembered by reference. Parsing the object behind a
  reference costs as much as the object is large however small the value the
  walk wants, and a mismatch costs as much as a match, because hayro skips the
  whole object before it answers none: 3,000 forms whose `/Subtype` names one
  array of 500,000 numbers (1.4 MB in all) kept the first `get_text` busy for
  17 s, a dictionary of 111,000 keys for 4.8 s, a string of 1 MB for 2.3 s, a
  number padded with a million zeros for 1.3 s, and a name of 1 MB for 1.2 s;
  3,000 Type 4 functions whose `/FunctionType` names the same array took 17 s,
  a padded number 3.6 s, and a name 0.9 s. They take 3 to 11 ms now. The read
  follows no further reference, so the chain of objects being read cannot
  change what the value holds (a stream that names itself holds neither a name
  nor an integer) and every stream that names the reference gets the same
  answer; a direct value belongs to the stream's own dictionary, which is
  parsed anyway. `test_a_value_that_many_streams_name_is_read_once` pins six
  shapes (5,000 streams, 3.1 to 29 s before), and four tests pin that a
  remembered answer is still applied to the second stream, which the walk
  reaches through the first one's resources or a stitching function so that
  the order is fixed: a mutant that negates the remembered answer fails all
  four.
  Outside this rule stays what hayro reads while it parses and decodes a
  stream: `/Length` in `Stream::read`, and `/Filter`, `/DecodeParms`, `/DP`,
  and the parameters inside them in `Stream::decoded`. The walk can only call
  those two methods, because the decoded bytes must be hayro's own, so it
  cannot remember the values: every stream it reads or decodes pays for its own
  copy. 3,000 forms that name one object of about 1 MB through `/Length` took
  3.6 s for a number padded with zeros, 4.8 s for a dictionary of 111,000 keys,
  and 17 s for an array of 500,000 numbers; through `/Filter` 2.1 s for a name,
  14 s for 125,000 unknown names, 8.3 s for 62,500 `/ASCIIHexDecode` names, and
  34 s for the array of numbers; through `/DecodeParms` or `/DP` 12 s for the
  dictionary and 17 s for the array; and a `/Predictor` that names the padded
  number 3.6 s. Tiling patterns, glyph procedures, appearance streams, Type 4
  functions, and (for `/Length` only, since the walk never decodes an image)
  images cost the same. hayro pays the same for every stream it draws, but it
  draws only the streams that content uses and the walk reads every listed one:
  the 125,000-name file takes 14 s with content that draws nothing and 28 s with
  content that draws all 3,000 forms. hayro 0.8.0 reads the same entries the
  same way. The options that were weighed and left: decoding a stream rewritten
  with direct values needs hayro's filter and parameter semantics reproduced
  exactly (which keys each filter reads, how `/DecodeParms` lines up with
  `/Filter`), and the guard exists to scan hayro's bytes; refusing a large value
  bounds CPU rather than stack, which belongs in a `DocumentLimits` field;
  skipping streams that content does not draw needs the operands of `Do`, `scn`,
  `gs`, `Tf`, and `sh` from the ambiguous tokenization that `content_scan`
  models; and reading an image as a dictionary first would skip its `/Length`
  but also admit a dictionary that is no stream, which hayro never draws. A
  cache of parsed objects in hayro-syntax (`XRef::get_with` parses the object
  again on every call, and `ObjectStream::new` rebuilds the index of an object
  stream on every lookup) would close this for every user, drawn streams
  included.
  The Type 4 scan needs no model beyond hayro's trivia rules: whitespace
  (NUL, tab, LF, FF, CR, space) and comments (`%` to CR or LF) are skipped
  before a token, the program must begin with `{`, `{` and `}` are delimiters
  that no other token contains, and the parse ends where the first procedure
  closes. Continuing past input that hayro rejects (an unknown operator, a
  string) can only count more levels than hayro reaches.
  This guard bounds stack use, not time. hayro can need exponential CPU for
  graphs that stay under the limit: a stitching function that names the next
  one twice per level takes 2^levels builds (20 levels take one second), and
  painting through nested Separation spaces takes about 3^levels (a 16-level
  chain needs 0.2 s for one rectangle, 18 levels 1.5 s), so tests list such
  chains without painting. Hosts must keep enforcing CPU deadlines.
  hayro-interpret 0.8.0 builds colour spaces and functions with the same code,
  apart from renamed enum payloads, and at the same construction sites, so the
  validation below carries over; it was not rerun against 0.8.0.
  The model was validated against hayro itself with a scratch harness that
  is not stored in the repository: a copy of hayro-interpret 0.7.0 whose
  `ColorSpaceType::new_inner`, `Function::new`, and `parse_procedure_inner`
  count their nesting in a thread-local, a crate that includes the real
  `content_guard.rs`, `graph_guard.rs`, `postscript_scan.rs`, and
  `content_scan.rs` by path (with a two-line pyo3 stub), and a generator of
  random one-page PDFs whose resources name colour spaces, shadings, transfer
  functions, soft masks, images, and shading patterns in spines, cycles,
  shared graphs, inline nests, mistyped slots, missing objects, and Type 4
  programs with comments and stray braces. A document is a violation when the
  guard accepted it and hayro's builders nested deeper than the limit plus
  the name frame (33 while the limit was 32) or its procedures deeper than 64. Count the depth
  rather than watch for overflow, as for content: depth is exact and fast.
  The first run found the name frame. 11,500,000 documents (6,000,000 on
  earlier versions of the walk and 5,500,000 on the final one, of which
  1,000,000 had only well-typed slots and 2,500,000 had dense cycles) produced
  no violation. Of the first 4,000,000, 5,915 ended early at a 400,000-call
  budget or a hayro panic (the depth seen until then still counts), hayro
  nested up to 144 levels, and 8,021 had procedures past 64, all refused;
  840,917 were refused, most of them although hayro stayed within the limits,
  because the kind-agnostic walk follows mistyped slots and counts every
  object of a cycle. A direct oracle that parses 40,000,000 random programs
  with hayro's own `Function::new` found 3,937,787 past 64 levels, all
  refused, and 1,078,165 refused although hayro stopped earlier at a token it
  rejects. A sweep of 5,707 distinct local PDFs
  (the first 40 pages of each) raised no `resource_nesting_depth` refusal;
  its one content refusal, pdf.js `issue16454.pdf`, is the same as before.
  The bundled corpus (19 documents, 95 pages, 1,083 hashes of pixmaps, clipped
  pixmaps, text, words, dict, rawdict, drawings, images, both table
  strategies, SVG, Markdown, and `render_pages`) is identical to `main`; cold
  first calls cost 1.0% more than `main` in total against 1.4% for the content
  check alone in the same run, so the walk is within noise (11 interleaved
  rounds of CPU-time minimums, local builds without LTO), and the component
  walk that remembers cycles and reads each object once measured 0.7% below
  that first revision (9 rounds, 653.5 ms to 649.0 ms); and the thin-LTO
  extension grew from 16,146,752 to 16,218,784 bytes unstripped with both
  checks.
  The Python regressions in `tests/test_resource_nesting.py` cover each root,
  each structure at 300,000 braces or 20,000 objects in a child interpreter,
  every public operation at the smallest refused depth, the exact limits, ten
  places that resources can live, saved variants (compressed, object
  streams, encrypted), cycles, sharing, and breadth. The Pyodide contract has
  `resource_nesting_*` and the fuzz seeds have `deep-type4-program`,
  `deep-function-chain`, and `deep-color-space-chain`. On a hayro upgrade,
  re-read every
  `Function::new`, `ColorSpace::new`, and `parse_procedure_inner` call site; a
  new construction site must join the walk.
- hayro and hayro-svg 0.8.0 replaced 0.7 on 2026-10-19 (the rendering,
  extraction, and guard changes are described in the bullets above). Measured
  against the hayro 0.7 build of the preceding commit, both thin-LTO wheels on
  macOS arm64: rendering every page of the bundled corpus (19 documents, 165
  pages, scale 1 over white) changed 14 pages, 540,222 of 110,838,393 pixels,
  by at most 30 per channel on scanned pages (image resampling), at most 3 on
  vector pages (anti-aliasing), and up to 15 in 60 pixels of the Type 3
  fixture. Text, words, dict, rawdict, Markdown, tables, drawings, and images
  hashed identically. hayro-svg 0.8 writes rectangles as `<rect>` and reuses
  each glyph outline through a transform, so the corpus's SVG shrank from
  322 MB to 48 MB, 6 to 8 times on text-heavy pages, with image-only pages
  unchanged. The abi3 wheel grew from 7,179,543 to 7,479,319 bytes (+4.2%)
  and the unstripped extension from 16,962,880 to 17,580,944 bytes (+3.6%),
  mostly through skrifa 0.46 and read-fonts 0.43, a third read-fonts beside
  krilla's 0.39 and HarfRust's 0.45. Cold first calls on the bundled corpus
  (`get_text`, `to_markdown`, `render_pages`, and per-page `get_drawings` on
  freshly opened documents; 15 interleaved rounds of CPU-time minimums, one
  process per round, no extras) fell from 2,309.0 ms to 1,727.6 ms in total
  (−25.2%): `get_text` −27.3%, `to_markdown` −16.8%, `render_pages` −29.9%,
  and `get_drawings` −30.1%.
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
  creates the Python string; `max_size=None` explicitly opts out. hayro-svg 0.8
  returns only a completed `String`, so this boundary does not cap the
  converter's one internal Rust allocation.
- `Page.get_pixmap(clip=)` accepts rotation-resolved display coordinates,
  intersects them with the page, and rounds outward to pixel boundaries.
  hayro 0.8's `render_into` draws into a target of the clip's size through a
  transform shifted by the clip origin, so only the clipped region is
  rasterized; hayro still interprets the complete page, and the full-page
  render-size limits still apply. `Pixmap::take_rgba8` converts the raster to
  straight alpha and hands over its buffer, and the completed `Vec<u8>` moves
  into an `Arc<Vec<u8>>` without copying the raster.
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
  `copy_page`, and `set_toc`) and `garbage=True` saves remove unreachable
  objects with `prune_unreachable_objects` in `rust/src/prune.rs`.
  Never call lopdf's `Document::prune_objects`: it records visited IDs in a
  `Vec` and scans it for every reference and every object, which is quadratic
  in object count. The replacement keeps lopdf's semantics, following
  references from the trailer through arrays, dictionaries, stream
  dictionaries, and reference-valued indirect objects but never through stream
  content, and visits each existing object once with a hash set and an explicit
  stack. The set is reserved for the current object count and the stack grows
  fallibly; refusal raises `PdfError` before any object is removed. Pruning
  still runs after the edit it follows has committed, so a refusal there leaves
  that edit and its unreachable objects in place. Form-XObject imports
  (`show_pdf_page`, embedded-font text, and widget appearances) never sweep
  the target: before mutation, `prepare_page_form_import` keeps only the
  source objects that `reachable_object_ids` reaches from the Form dictionary,
  with source Catalog, Pages, and Page objects treated as absent. A placement
  therefore costs time in its source rather than in every target object, and
  objects the target already could not reach stay until a garbage save, as
  after every other drawing edit.
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
- `rust/src/placement_reuse.rs` places an image or page that a document
  already holds again instead of embedding another copy. `insert_image` keys
  Image XObjects by the SHA-256 digest of the accepted encoded bytes, or of a
  Pixmap's dimensions and RGBA samples; the rectangle and rotation stay in the
  placement matrix. `show_pdf_page` keys Form XObjects by the source
  document's revision and one-based page. Every `_Document` takes a
  process-unique revision at construction and a new one on every edit, so a
  Form is reused only while its source is unchanged and never for another
  document, including the one that `authenticate` or a reopen creates. A hit
  places the remembered XObject without parsing the input or cloning the
  source. Before a hit, the remembered image, soft mask, or Form stream must
  still exist with its subtype and content length: pruning is the only change
  that the keeping edits below make to a placed object, and a Form that
  survives pruning was reachable, as are the objects it reaches.
  `invalidate_hayro_pdf` forgets every placement, so it stays the safe default
  for an editing method. Edits that only add objects; replace page
  dictionaries; rewrite the page tree, the catalog, the information
  dictionary, or navigation structures (outlines, name trees, annotations); or
  remove unreachable objects call `invalidate_hayro_pdf_keeping_placements`:
  drawing, annotations, page geometry, structural edits, outline, label,
  attachment, and metadata edits, `prune_objects` for `garbage=True`, and
  object-stream saves. `compress`, `decompress`, `compress_images`,
  `replace_text`, and `set_form_field` forget. Both replace the revision.
  Classify a new editing method before giving it the keeping variant. A
  same-document `show_pdf_page` re-keys its Form under the revision of its own
  edit unless it placed the source page on itself, so consecutive placements
  of one page on other pages share one Form; any other edit imports the page
  again. Each document remembers at most 4,096 images and 4,096 Forms, and a
  full map starts over, which keeps output deterministic. Room for an entry is
  reserved before mutation, and a refused allocation leaves the placement
  unremembered instead of failing the edit. Nothing is remembered across save
  and reopen, and generated text and widget appearances import their own
  Forms. `tests/test_placement_reuse.py` pins each of these behaviors.
- lopdf numbers new objects above `Document::max_id`, so a loaded reference to
  a higher number would resolve to whatever a later import or edit adds.
  `_Document.load` and `load_bytes`, which `authenticate` reuses, therefore
  call `normalize_object_numbers` in `rust/src/unallocated.rs` before
  structural validation. It raises `max_id` over the highest loaded object,
  then replaces every reference to a higher number, of any generation, with
  null in the trailer, arrays, dictionaries, and stream dictionaries, never in
  stream content, using a fallibly grown stack. Every reference pylopdf adds
  later names an allocated object or comes from a renumbered import, so the
  pass keeps the document alias-free for its lifetime. ISO 32000-2 §7.3.10
  and hayro already read these references as null, so the original-byte
  rendering path stays valid. Missing numbers at or below `max_id` are never
  allocated and stay untouched, so documents without higher references keep
  their objects and save unchanged; readers treat such a missing object as
  null, as described below. Never raise `max_id` to a referenced
  number instead: numbers need u32 headroom, and once new objects take such
  numbers, lopdf's cross-reference writer loops over every number up to the
  highest object.
- lopdf increments `max_id` without an overflow check in `add_object`,
  `new_object_id`, `Document::encrypt`, and its object-stream writer, and its
  cross-reference writer visits every number up to the highest object on each
  save. A file whose objects or in-use cross-reference entries reached past
  8,388,607, the ISO 32000-1 indirect-object limit, could therefore leave no
  headroom: near u32::MAX, release builds wrapped new objects onto object 0 and
  then object 1, often the catalog, and a file of a few hundred bytes took about
  19 seconds per save. When `max_id` exceeds the limit,
  `normalize_object_numbers` moves every object above it to consecutive numbers
  after the highest object at or below it, in ascending ID order with
  generations kept, and ends `max_id` at the last of them. The same traversal
  points each reference above that highest object at its moved object, matched
  by number and generation in one exactly reserved sorted table, or replaces it
  with null. Lower numbers stay unchanged, an over-limit entry without an object
  only lowers `max_id`, and documents within the limit keep the behavior above.
  Moving any object drops the original-byte rendering path, because image
  compression maps hayro's object IDs back to lopdf's and hayro reads object
  numbers as i32. Later numbers come only from allocations, so the remaining
  headroom outlasts memory. lopdf's reconstruction already skips headers
  numbered above 1,000,000, so such numbers come from standard cross-reference
  resolution, object headers that disagree with their entries, or object-stream
  members.
- Readers treat a null value, an indirect null, and a reference to a missing
  object alike, as an absent entry, following ISO 32000-2 §7.3.7 and §7.3.10.
  `non_null_object` and `non_null_value` in `rust/src/document.rs` resolve a
  reference chain this way, and lopdf's reference-limit error for a cyclic or
  over-long chain still surfaces. Annotation appends start a new `/Annots`
  array, `set_metadata` replaces trailer `/Info`, both page-attribute
  inheritance walks end at such a `/Parent`, Font and XObject registration
  starts an empty category, `get_toc` returns no entries for such an
  `/Outlines`, attachment writes replace catalog `/Names`, and the
  EmbeddedFiles walker skips such a FileSpec, so attachment rewrites drop it.
  Page cleanup reads such navigation entries as absent. Drawing drops such
  `/Contents` entries, resolving array entries one level so the cost per entry
  stays constant, because hayro stops reading page content at the first entry
  that is not a stream. Use these helpers instead of mapping lopdf's
  `ObjectNotFound` to `PdfError` wherever a reader already skips null.
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
  unparseable objects, which `object_recovery` re-parses where it can) are not
  reported. Reconstruction never runs for damaged xref-stream files without a
  classic trailer; they remain rejected.
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
  described below. Auto-discovery keeps package assets as paths. Before its
  first render, every new `Document` stats each selected sans/serif path and
  applies that boundary, then reuses the process-wide bytes in
  `rust/src/bundled_font_cache.rs` while the path's size and modification
  time (plus device, inode, and change time on Unix) match the last read for
  its package/style slot; only other files are read. The cache holds at most
  one file per slot, shared by every document rather than copied into each,
  and keeps it after those documents close. It locks only to look up or swap
  slots, never across file I/O, so a hung filesystem cannot stall other
  threads and a fork cannot inherit it held; concurrent first renders may
  therefore each read once. New reads are published, and the document
  updated, only after every selected pair succeeds. Failed path reads or size
  checks must preserve configured fonts and caches, including those slots.
  The private `set_fallback_font_locale_files` returns how many files it read,
  which the tests use to pin reuse. Explicit `set_fallback_font` paths are
  still read on every call. With all four CJK extras (54 MiB), interleaved A/B
  on the five GitHub-hosted release targets measured fresh first-page renders
  of the 1 KB `pdfium-type3.pdf` 90–95% faster and 14 open rendered documents
  at 100–157 instead of 803–860 MiB; the retained slots add up to 54 MiB after
  the last document is released where the allocator would have returned it.
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
  the collection of Form-reachable source objects before cache invalidation or
  target mutation; commit then has no recoverable source-side work.
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
  Sharing comes from the page index's per-array page counts, described with
  the walker below, rather than a scan of every page.
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
  pinned to 0.8.2 with all default features disabled; HarfRust 0.14 supplies
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
  an explicit font. Python passes a face selected from an installed package to
  the `_file` core variants as `package_font=(language, kind)`, and the face
  shares its package/style slot in `rust/src/bundled_font_cache.rs` with CJK
  fallback configuration. Every call still stats the path and applies the
  font boundary below before reusing the slot's bytes, and rereads a replaced
  file; explicit `fontfile=` paths and `fontbuffer=` input never use the
  slots. Generation and widget appearances take shared `Arc<Vec<u8>>` font
  bytes, so a multi-widget fill does not copy the font once per widget, and
  `_Document.package_font_reads()` counts slot reads for tests. Interleaved
  A/B on the five GitHub-hosted release targets measured a short Japanese
  `insert_text` on a fresh document 46–59% faster, Simplified Chinese serif
  66–83%, and a Japanese `set_form_field` 55–79%, with byte-identical output;
  each used face then stays resident after the last document closes
  (11.0 MiB for the Simplified Chinese serif face on Windows). `insert_text`,
  `insert_textbox`, `set_form_field`, and `set_fallback_font` share a 64 MiB
  default OpenType boundary. Python rejects buffer input before PyO3 copying;
  file input uses a one-byte-overrun bounded Rust read with the GIL released and
  fallible 64 KiB retained-buffer growth. Direct byte/path core variants repeat
  the check, `max_font_size=None` explicitly opts trusted input out, and
  failures use `font_input_size` without mutation.
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
  Generated PDFs retain it for compliant viewers, but hayro 0.8.0 does not
  expose that property to extraction devices (hayro `main` does since
  LaurenzV/hayro#1513), so some reordered or context-dependent
  complex-script clusters remain an explicit pylopdf extraction limitation.
  Keep third-party acknowledgements in `NOTICE.md` and include both license
  files through PEP 639. The Windows abi3 wheel measured 5.42 MB after
  integration, up from 4.44 MB.
- `pylopdf.Font` follows pymupdf's `Font(fontname, fontfile, fontbuffer)` and
  wraps `_Font` (`rust/src/font.rs`): either an OpenType face, held as shared
  bytes plus the HarfRust font that shapes them and checked by krilla as
  well, or a Standard 14 font. `text_length` measures OpenType text with
  `generate::shaped_line_width`, the sum of shaped advances that places every
  glyph krilla draws and that textbox and form layout wrap with, and Standard
  14 text with `draw::standard_text_width`, which Standard 14 layout uses;
  keep generation and measurement on those so a measured line stays the drawn
  width. Drawing with an OpenType `font=` passes the same bytes and index as
  `fontbuffer` and produces byte-identical output; a Standard 14 `font=`
  follows the `fontname` path. `language=` and `family=` look up only the
  faces of installed font extras, by the family names in
  `_FONT_EXTRA_FAMILIES`. There is no font registry or other process-global
  font state (F0 G6), and pylopdf never scans system font directories, so
  output depends only on the fonts passed in. A face that does not match
  `is_bold`, `is_italic`, or `weight` raises unless `allow_regular_fallback`
  is set, which warns; never synthesize a weight by stroking, overprinting, or
  render-mode changes. The `fontfile` and `fontbuffer` drawing arguments,
  and `Font(fontfile=)` or `Font(fontbuffer=)` without `weight`, load a
  variable font at its default instance, which need not be regular (Noto
  Sans CJK JP's is Thin). The keyword-only `weight` extends a loaded face
  (F0 R2.5): `_face_at_weight` selects that instance with
  `_Font.with_weight`, so `weight=400` differs from an omitted weight, and
  refuses a weight outside the axis; a static face must already declare the
  requested weight class, and pylopdf never relabels it. `is_bold`,
  `is_italic`, and `is_serif` keep pymupdf's lookup meaning and raise with
  `fontname` and font files (R2.7) instead of selecting an instance there.
- The `ar`, `he`, `hi`, and `th` extras ship variable fonts with `wght` axes
  (100 through 900; Noto Naskh Arabic 400 through 700), and the default
  instance of Noto Sans Hebrew is Thin (weight class 100).
  `generate::GenerationFace` is the face and weight that every generation path
  draws: krilla embeds it through `Font::new_variable` and HarfRust shapes it
  through `instance_builder().variations()` at the same user-space `wght`
  value, so subsetter 0.2.6 instantiates the outlines and advances that
  HarfRust measures. Both normalize through read-fonts'
  `fvar.user_to_normalized` (skrifa 0.42 through read-fonts 0.39, HarfRust
  through 0.45), which agree for `avar` 1.0; re-check after a krilla, skrifa,
  or HarfRust upgrade. A static face, a face drawn as given, and a variable
  face at its default location carry no coordinates, so their output stays
  byte-identical, and a weight outside the axis fails instead of being
  clamped by krilla and HarfRust. Python passes the regular weight for faces
  of installed extras on every drawing path, path or bytes, and
  `Font(language=...)` and `Font(family=...)` select another weight, from
  `weight=` or `is_bold=True`, with `_Font.with_weight`, which shares the
  face's bytes and package slot. An instance reports its axis value as
  `weight`, counts as bold from 600, and takes its named instance's style
  in `name` (`Noto Sans Hebrew Bold`, else `650wght`). krilla names a subset
  after name ID 6, the default instance's PostScript name, so
  `GenerationFace::name_subset` renames an instance's Type0 font, CID font,
  and descriptor after loading the generated PDF, following Adobe TN #5902
  (the named instance's `postScriptNameID`, else name ID 25 or the family
  plus its subfamily, else `NotoSansHebrew_650wght`); extraction infers the
  bold flag from that name. The embedded program's name table and the
  descriptor's `/StemV` still describe the default instance. skrifa
  truncates HVAR deltas where HarfRust rounds them, so a `/W` width could
  differ from the shaped advance by one font unit while glyph positions
  follow HarfRust; every Hebrew and Thai letter of the bundled faces matched
  at the weights measured. The Pyodide contract has no font extras; it
  covers instancing through `Font(fontbuffer=..., weight=...)` with the
  repository's Noto Sans Hebrew, and native tests cover the extras.
- Generated text and widget appearance PDFs import through
  `generated_pdf::load` in `rust/src/generated_pdf.rs`; never pass krilla
  output to `Document::load_mem` directly. Native lopdf's `rayon` feature
  parses every object with `par_iter` on the global pool, so each load from a
  thread outside the pool injected a job, woke a sleeping worker, and blocked,
  with the GIL held, until the pool finished. For a generated document of
  about a dozen objects that hand-off cost more than the parse: about 31% of a
  fresh Japanese `insert_text` on an Apple M2 Pro. The module reads the subset
  pdf-writer emits on the calling thread (one classic cross-reference section
  with lopdf's lenient entry terminators, compact direct objects, and direct
  stream lengths) and builds exactly the document lopdf 0.45 returns, including
  the header version, binary mark, `xref_start`, the `/Size` correction, and
  `max_id`. Comments, other cross-reference forms, `/Prev` and `/XRefStm`,
  encryption, object streams, indirect stream lengths, nesting beyond 32
  levels, and anything lopdf would repair or skip decline and fall back to
  `Document::load_mem`. Emscripten always uses `load_mem`, user documents keep
  lopdf's parallel parse, and the Form-import bounds apply to the result
  unchanged. `_Document.generated_pdf_loads()` counts sequential, fallback, and
  mismatched loads, and `compare_generated_pdf_load` checks synthetic syntax
  against lopdf. `tests/conftest.py` enables `verify_generated_pdf_loads` for
  the whole suite and fails any test whose import differs from lopdf's parse of
  the same bytes; after a lopdf, krilla, or pdf-writer upgrade the suite must
  still report no fallbacks or mismatches. Interleaved A/B on the five
  GitHub-hosted release targets measured a fresh Japanese `insert_text` 6–38%
  faster and a twelve-widget `set_form_field` 17–31% faster, with loads
  unchanged and byte-identical output.
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
  14 or krilla generation paths. When the bounded `/Parent` walk finds no `/Q`,
  `field_quadding` uses the AcroForm dictionary's document-wide `/Q`
  (ISO 32000-2 Table 225), read through `inheritable_field_value`, and
  otherwise aligns left. A field's own or inherited `/Q`, including `/Q 0` and
  an unusable value, still wins; an out-of-range or non-integer value aligns
  left. Comb text fields resolve inherited `/MaxLen`, count Unicode graphemes,
  position them individually, and reject overlength or incompatible flag
  combinations. Preserve non-empty authored button states and synthesize only
  missing/empty Off/on states. Widget `/MK /R`, `/BG`, `/BC`, `/BS /W`, and
  legacy `/Border` feed the appearance. Keep updates atomic by restoring the
  document clone on error. hayro 0.8 draws an `/AP /N` state dictionary as the
  state that `/AS` names, else `/Off`, so the rendering snapshot no longer
  substitutes the selected state stream as it did for hayro 0.7, and an
  unedited form keeps the original-byte fast path. hayro reports a missing
  state appearance, such as a check box with `/AS /Off` and no `/Off` stream,
  which ISO 32000 allows, as `UnresolvedAnnotationAppearance`; pylopdf drops
  that warning.
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
  reading through the same helper, and fall back to the AcroForm dictionary's
  `/DA` as `field_quadding` does for `/Q`. Tree stacks, cycle sets, inherited
  names/types, choice values, joined values, returned entries, field-widget
  lists, and button-state names grow fallibly; returned ordering uses an
  in-place sort with object-ID tie-breaking.
  Inherited names and types are shared like values: a push clones an
  `Arc<String>` around the text that a visit allocated fallibly, and returned
  fields share their inherited type. `/Kids` can push one kid once per edge,
  while only its first visit is charged to the text budgets, so a copy per
  push would let a long name or type grow with the edge count.
  `set_form_field` must reject caller
  field names and values above 1 MiB before font discovery, button lookup, or
  file reads, repeat the check in Rust with `form_field_input_size`, enforce the
  same tree limits, and preserve its atomic rollback contract.
  The walk and `field_widgets` classify `/Kids` entries through
  `classify_field_kid`, so `visible_field_widgets` and button-state handling
  see the widgets that the walk classifies. A kid dictionary whose `/T` is
  absent, null, an indirect null, or a reference to a missing object is
  nameless, following ISO 32000-2 §7.3.7 and §7.3.10; any other `/T`,
  including an unusable value or a cyclic or over-long reference chain, makes
  it a child field. A nameless kid is a widget of its parent field unless its
  `/Kids` resolves to a non-empty array. A field merged with its only widget
  omits `/Kids` (§12.7.4.1), so such a kid is a nameless intermediate field,
  another representation of the field above it (§12.7.4.2). Intermediates,
  including nested ones, fold into the nearest field above them that is not
  an intermediate: their widget kids become its widgets, their named kids
  become its child fields under its full name, and leaf detection counts both.
  The walk visits each intermediate like a child field that adds no name
  component, and each field reserves its leaf entry before reading its kids so
  that an intermediate holding a child field can withdraw it. An intermediate
  that also looks like a widget (`/Subtype /Widget` or a `/Rect`) stays a
  widget, so a malformed merged widget with stray kids is still drawn. A
  nameless kid with an absent, null-equivalent, non-array, or empty `/Kids`
  stays a widget, as a field with such a `/Kids` is its own widget. Folding
  never moves attributes up: an intermediate's own `/FT`, `/Ff`, and `/V`
  reach only the named fields below it, and a fill leaves its `/V`, like a
  widget kid's, unchanged.
  Intermediates count as nodes and levels, their `/Kids` entries as edges, and
  their materialized names, types, and values toward the text budgets. A
  direct `/Fields` or `/Kids` entry, an indirect null, and a reference to a
  missing object cost only their edge. Every other `/Kids` entry that is
  neither a child field nor an intermediate counts toward the 4,096-widget
  walk budget, as does an intermediate that is also a widget, so that budget
  covers every widget that `field_widgets` can return. `field_widgets` returns
  widgets in document order, expands each intermediate once, and enforces the
  walk's node, edge, and widget limits, which a successful walk has already
  met. It has no depth limit: the node limit bounds its frame stack, and a
  depth limit could reject an intermediate that the walk reached at a smaller
  depth through another field.
- AcroForm button handling rejects more than 4,096 widgets, 8,192 `/AP /N`
  state entries, 4,096 unique returned names, or 1 MiB of encoded/returned
  state-name text. Borrow and preflight state dictionaries before cloning them.
  Appearance synchronization must budget missing `Off`/on keys before mutation
  so a successful fill remains readable under the same limits. Resolve indirect
  field `/Kids` arrays consistently.
- `insert_pdf` registers the AcroForm fields of imported pages through
  `rust/src/acroform_import.rs`, following qpdf's page-copy conventions.
  `transplant_pages` takes the source `/AcroForm` out of the disposable
  renumbered copy, edits that copy, and reserves receiving capacity before the
  imported objects move in; `merge_pages` commits after the page-tree rebuild
  and before pruning, and the commit only moves prepared values into reserved
  space. Imported top-level fields join the receiving `/Fields`, and a
  top-level partial name that the receiving form already uses gains the
  smallest unused `+N` suffix, one per distinct name, appended in the name's
  own text encoding. Source `/DR` entries are shared when they structurally
  equal the receiving entry of their name (both graphs followed, at most
  4,096 objects and 1 MiB compared; more counts as different) and otherwise
  gain the smallest unused `_N`; every imported `/DA` string, wherever a
  dictionary of the copy holds one, then renames the operand of `Tf`, `gs`,
  `cs`/`CS`, and `scn`/`SCN` and copies every other byte. When the receiving
  document-wide `/DA` or effective `/Q` differs from the source's, the source
  value moves onto imported top-level fields without their own. A new
  receiving form takes the source `/DR`, `/DA`, and `/Q` as they are;
  `/NeedAppearances` carries over when true, `/CO` keeps imported entries, and
  `/XFA` and `/SigFlags` never move. `/Fields` entries that are no field
  dictionaries (no `/FT`, `/T`, `/Kids`, or `/Subtype /Widget`) are skipped,
  as are page-tree and catalog dictionaries. A partial range reads every
  source page's `/Annots`: a widget is imported when an imported page lists
  it, or when no page lists it and its `/P` names an imported page. It then
  walks the field tree with the field-tree reader's node, edge, widget, and
  level limits and type inheritance, removes non-imported widgets and fields
  whose widgets are all elsewhere from every walked `/Kids`, removes the same
  indices from a button field's own `/Opt` when its length matches, keeps
  widgetless fields below a kept parent, drops top-level fields without an
  imported widget, and points a kept widget whose `/P` names no imported page
  at the imported page that lists it. A complete import does not walk the
  tree, so only the 1 MiB top-level name budget, which every import shares
  when the receiving document has a form, can refuse one.
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
- GIL-enabled CPython 3.10–3.15 uses one `abi3-py310` wheel per platform.
  Free-threaded CPython 3.14 and 3.15 use version-specific `cp314-cp314t` and
  `cp315-cp315t` wheels. CI runs the full suite on 3.10, on 3.14 on all three
  operating systems, on 3.15, and on 3.14t and 3.15t; release CI smoke-tests
  each abi3 wheel on 3.14 and 3.15 and each free-threaded wheel on its own
  interpreter. `tests/test_ci_policy.py` requires CI lanes for the oldest and
  newest classified CPython and for every free-threaded release interpreter.
  All CI, documentation, fuzz, and release jobs use standard GitHub-hosted
  runners. Native wheels must install and execute on all five supported
  architecture-matched targets before publication. Set deadlines for each
  workload so compilation, tests, and artifact uploads can finish; there is
  no global one-minute cap. Skipped checks do not constitute validation.
  Do not add personal infrastructure details to this public repository.
  Run actionlint after workflow changes.
  Keep PyO3's `abi3t-py315` feature off. PEP 803's `abi3t` stable ABI, new in
  CPython 3.15, loads in GIL-enabled and free-threaded builds alike, and PyO3
  0.30.1 with maturin 1.15.0 builds pylopdf as a `cp315-abi3.abi3t` wheel that
  passed `tools/smoke_artifact.py` on CPython 3.15.0 and 3.15.0t, with the GIL
  still disabled and the zero-copy Pixmap buffer intact. But `cp315-abi3`
  outranks `cp310-abi3` on GIL-enabled 3.15+, so publishing that wheel would
  move those installs to the abi3t binary, which no GIL-enabled CI lane builds
  and whose cost there is unmeasured. Enabling the feature in `Cargo.toml`
  would also tie each build's ABI to its interpreters. Since 1.14.1, maturin
  builds one stable ABI per invocation, abi3t whenever a CPython 3.15+
  interpreter takes part, and version-specific wheels for the others:
  `-i python3.14 -i python3.15` produced `cp315-abi3.abi3t` and `cp314-cp314`
  and no `cp310-abi3`. The abi3 release jobs pass no `-i`. maturin 1.15.0
  discovers interpreters only up to 3.14 (`MAXIMUM_PYTHON_MINOR`), so the
  manylinux job found CPython 3.10–3.14 although its image ships 3.15 and
  3.15t, and the macOS jobs build with the runner's default `python3`; a
  maturin or runner image that finds 3.15 would silently replace those jobs'
  `cp310-abi3` wheels. Adopting abi3t therefore needs the feature passed to
  the free-threaded 3.15 build alone, CI lanes for that binary on GIL-enabled
  and free-threaded 3.15, and a measurement of its cost on GIL-enabled builds;
  one such wheel would then also serve every later free-threaded release. The
  July 2026 reason, a maturin cross-compilation failure for 3.14t, no longer
  applies because every release target builds natively. Add size-increasing
  dependencies cautiously; thin-LTO abi3 wheels built from v0.14.0 sources are
  6.4–7.4 MiB depending on platform, versus 6.8–7.9 MiB for the published
  v0.14.0 wheels.
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
  subset-embedded OpenType generation, weight instances of a variable font
  passed as bytes, textbox layout, pixmaps,
  `render_pages(workers=1/4)`, virtual-filesystem save, merge/select, typed
  error recovery, lopdf cross-reference recovery reporting, and unchanged
  extraction after an edit for a skipped bare-keyword form and a non-page
  page-tree kid. Add only small redistributable PDFs listed in
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
  Page-tree-neutral edits keep the index, so loops that rotate, crop, draw on,
  or annotate every page stay linear. Besides the page IDs, the walk records
  the objects on `/Kids` reference chains and the absent objects that skipped
  `/Kids` entries name, and each neutral edit checks those facts or drops the
  index. New objects, including `self.doc.add_object` through
  `draw::ObjectSink` and renumbered Form imports, keep it only at IDs that
  neither exist nor are recorded as missing entries; lopdf's `add_object`
  wraps past `u32::MAX` into existing IDs. `replace_page` keeps it only for an
  indexed page whose `/Type`, `/Pages` (a malformed page can be the catalog),
  and indirect `/Annots` reference stay the same. `set_page_entry` and
  `set_page_object_entry` keep it only for `/Rotate`, the five page boxes, and
  `/Contents`. An in-place annotation-array edit keeps it unless the walk read
  that array as `/Kids`. No neutral edit removes objects, so the object count
  that bounds walked edges only grows; anything that prunes goes through
  `DerefMut`. Because Form imports null references to missing source objects,
  current edits cannot turn a missing entry or a `/Kids` alias into a page, so
  those checks are defensive; keep them so each obligation stays locally
  checkable. The first append to an indirect `/Annots` array counts the pages
  that reference each such array; detaching a shared array decrements its
  count, and a page object that is a reference to another dictionary disables
  the counts in favor of the scan. Failed walks are never cached, so a
  malformed tree raises on every access. Serialization, including the
  renderer snapshot, still takes `&mut Document` and drops the index, which
  the next access rebuilds.
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
- lopdf's `rayon` feature parses even tiny documents on the global pool, so
  every native load from a thread outside the pool pays a worker wake-up.
  Without the feature, opening 7- to 29-object PDFs was 10–65% faster on the
  five release targets, but 445- to 5,343-object documents were 62–257%
  slower, so pylopdf keeps it for user documents and parses only its own
  generated PDFs on the calling thread. A lopdf change that parses small
  cross-reference tables on the calling thread is an upstream contribution
  candidate.
- lopdf's `renumber_objects_with` keeps the number of a reference to a missing
  object, so after an import it resolves to whichever object takes that
  number. pylopdf's import renumbering writes null instead, and loading nulls
  a document's own references above `max_id`. lopdf stores each loaded object
  under the number in its own header and adds object-stream members missing
  from the cross-reference table. lopdf 0.44 derived `max_id` from the table
  alone, so a later allocation could replace such an object; 0.45 raises it
  over every loaded object, and pylopdf's load pass repeats that. hayro reads
  the original bytes through the table instead, so `object_recovery` drops
  such objects when nothing reaches them and otherwise renders from lopdf's
  graph.
- lopdf allocates object numbers with unchecked `max_id += 1`, so `max_id`
  must keep its headroom. Loading renumbers objects above 8,388,607 for that
  reason; never set `max_id` from untrusted input elsewhere. Checked
  allocation upstream is a contribution candidate.
- hayro's typed array iteration ends at the first element that does not
  resolve to the requested type, so `/Contents` stops at a missing, null, or
  mistyped entry. hayro 0.7 ended `/Kids` there as well; hayro 0.8 skips such
  page-tree kids, reads untyped ones as pages, and leaves out a kid it cannot
  read. hayro also answers the first mismatched or unparseable
  cross-reference entry it reads by rebuilding its whole table. Keep the
  page-identity check and `object_recovery` until upstream changes both.
  hayro 0.7 parsed nested arrays and dictionaries recursively without a bound,
  so deep nesting overflowed the stack and killed the process
  ([LaurenzV/hayro#1347](https://github.com/LaurenzV/hayro/issues/1347));
  hayro 0.8 reads none deeper than 64 levels, treating the object as absent
  and truncating content, which `read_depth` and `ContentGuard` turn into
  page-level refusals. hayro 0.7 and 0.8 also build colour spaces and
  functions, and parse Type 4 braces, by recursion with no bound at all;
  `GraphGuard` and `postscript_scan` bound them before every interpretation,
  and nothing else in hayro stops them.
- hayro 0.8.0 panics in `DeviceRgb::convert` when colour values reach a
  conversion with a different number of components than the colour space
  that converts them, such as a Separation or DeviceN tint transform with the
  wrong number of outputs or a shading in a Pattern colour space
  ([LaurenzV/hayro#1546](https://github.com/LaurenzV/hayro/issues/1546),
  filed with a tested fix); hayro 0.7 drew the same pages. hayro has had panics
  on malformed input before, and some remain open upstream. Every operation
  that lets hayro read or interpret pages therefore runs inside
  `_Document::contain_renderer_panics`, which catches the unwind, drops
  everything an edit drops, and raises `PdfError` instead of PyO3's
  `PanicException`, a `BaseException`. Rust's panic hook still prints the
  message to standard error. A new entry point that reaches hayro must run
  inside it too; `tests/test_renderer_panics.py` pins the cases of #1546 and
  the Pyodide contract checks the containment under Emscripten.
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
3. GitHub Actions (`release.yml`) builds abi3, cp314t, and cp315t wheels plus
   the sdist for five platforms and publishes through PyPI Trusted Publishing.

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

[ROADMAP.md](ROADMAP.md) is the canonical medium-term plan. Its 2026-10-19
rewrite organizes the pymupdf parity program: the goal and baseline,
principles, a definition of done for every feature pull request, workstreams
with module ownership, waves, foundations F0–F9, the M0 API reset, milestones
M1–M8, v1.0 criteria, non-goals, the watchlist, and a dated evidence log. The
pre-rewrite plan, with its release narratives and 2026-07 surveys, remains in
Git history (`git show a233814:ROADMAP.md`).

- API rule: one API, in which the same name means the same thing. A
  pymupdf-named API keeps pymupdf's meaning; a redesign takes a new name and
  leaves the pymupdf name out. Do not add aliases, synonyms, or compatibility
  layers; migration is served by the parity manifest and its tools (F9). There
  are no deprecation windows before 1.0.
- Current phase: wave 0 of the parity program (2026-10-19). The hayro 0.8
  migration (#233), AcroForm registration on `insert_pdf` (#234), XObject
  reuse for repeated insertion (#231), save-time merging of identical
  objects, CPython 3.15 support (#230), the F3 parity manifest (#235), and
  the F0 API design draft are in flight. The F1 module split runs alone after
  they merge; the M0 API reset follows F0 approval.
- Every feature pull request meets the definition of done in ROADMAP.md and
  implements against the F0 API design. Concurrent pull requests stay inside
  one workstream's modules.
- Incremental save is scheduled in milestone M7 instead of the watchlist.
- v1.0 has no target date. It requires a complete parity manifest, independent
  field reports across the new surfaces, and the published stability policy.
  Roughly 1,000 GitHub stars is an indicative adoption signal, not a
  mechanical release trigger.
- lopdf 0.45 includes the upstream lopdf#535 fix (lopdf#537). pylopdf has
  extracted through hayro since v0.7, so the fix matters only to lopdf's own
  content parser.
- See [CHANGELOG.md](CHANGELOG.md) for completed history.
