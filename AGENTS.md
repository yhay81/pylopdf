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
  collection through a bounded, generation-invalidated `TextPage` cache.
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
  text. Hayro normalizes glyph space to 1000 upem, so font size is the transform
  factor × 1000. Vertical bboxes approximate baseline ± a size ratio.
  Overlapping paint runs on one baseline are split into source-order logical
  layers before inline geometry sorting; preserve distinct overprints rather
  than interleaving or deduplicating their glyphs. Run detection compares the
  preceding retained glyph without cloning its text, and line/run/layer
  collections grow fallibly.
  `DocumentLimits.max_text_glyphs` bounds cumulative positioned glyph records
  before caching or structured Python output. Text and table interpretations
  of one page share one admission, failed pages consume no budget, and
  refusals use `text_glyph_count`. The compatible default is `None`;
  `DocumentLimits.web()` sets 65,536.
  When `max_text_size` is configured, plain-text assembly preflights its exact
  UTF-8 size and caps one private extraction batch at twice that payload budget
  because inferred gaps plus line endings cannot outnumber non-empty glyph
  records. The batch accepts at most 4,096 page entries, so repeated page
  numbers cannot amplify a bounded interpretation without bound. Plain-text
  output, multi-page joining, and structured span/word/line/block
  materialization grow fallibly; allocation refusal returns `PdfError` without
  discarding or partially exposing the cached page interpretation. Structured
  word generation reuses borrowed glyph slices instead of building a temporary
  reference vector for every word.
  Sustained whitespace gutters split same-baseline segments into recursive
  left-to-right columns; full-width headings and footers remain outside the
  column regions, and isolated wide gaps stay on one line. Gutter discovery
  uses lightweight segment bboxes and font-size samples; only confirmed columns
  move glyphs into fallibly grown segment/region vectors. Glyph, line, size, and
  gutter ordering uses in-place unstable sorts with explicit source-order or
  geometry tie-breakers so admitted extraction does not require hidden
  stable-sort scratch allocation. `find_tables` uses a
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
  candidates or `None` to disable table conversion. Document conversion stops
  page iterable materialization at 4,096 entries and defaults to a 64 MiB
  cumulative UTF-8 output cap. It uses a page-at-a-time heading-count pass and
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
  unencrypted load first consumes its original input bytes and falls back to a
  lopdf serialization only when hayro rejects them or reports a different page
  count. Editing methods must call `invalidate_hayro_pdf`, which also discards
  the original-byte fast path; edited state must always be reflected in
  rendering. `DocumentLimits.max_interpretation_size` bounds both retained
  original input and the complete reserialization after edits, decryption, or
  state-appearance normalization. Under a finite limit, serialize before
  constructing the state-normalized render copy. The bounded writer must not
  install a partial hayro cache. Original input above a finite limit retains
  only its size and limit until first-interpretation refusal; admitted
  in-memory input copies use fallible exact allocation. Failures use
  `interpretation_size`. Its
  compatible default is `None`, while `DocumentLimits.web()` sets 64 MiB.
- `Document.render_pages` is the supported same-document concurrency boundary:
  it renders an immutable hayro snapshot on a dedicated rayon pool, preserves
  input order, releases the GIL, accepts 1–64 requested workers, and caps actual
  concurrency to roughly 512 MB of estimated raster and conversion buffers.
  Serial and PyEmscripten execution reuse one local hayro `RenderCache` for the
  complete call. Native parallel execution gives each bounded worker its own
  cache and uses an atomic page queue to retain dynamic load balancing; a cache
  never crosses a thread. Pixel preflight does not materialize an intermediate
  collection. Serial results, worker-local indexed results, parallel reduction,
  input-order slots, and the final returned vector all grow fallibly.
  One call accepts at most 4,096 page entries. PNG writer chunks atomically
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
- `delete_pages`, `select`, and `insert_pdf` accept at most 4,096 page entries
  per call in both Python and Rust. Iterable materialization stops at the
  4,097th item, and range insertion checks its size before allocation or graph
  import. `delete_pages([])` is a true no-op: do not invalidate caches or bump
  the Python generation. Imported and selected page orders, duplicate tracking,
  inherited page dictionaries, spliced orders, and root/rebuilt Kids arrays
  grow fallibly. `select` validates and plans all page IDs and duplicate copies
  before cache invalidation or PDF mutation; page-tree rebuilds resolve every
  page and allocate the final Kids array before committing dictionaries.
- Rust defines `PdfError` (a `ValueError`-compatible base) and `PasswordError`;
  Python defines `DocumentClosedError`, `EncryptedDocumentError`, and
  `StalePageError`. Add new errors under the `PdfError` hierarchy instead of
  introducing plain `ValueError` exceptions.
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
  output boundary. Preserve the documented mutation semantics of `garbage`,
  `deflate`, and `object_streams` on output refusal.
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
  decompression limits remains authoritative. The full repaired-input copy is
  reserved fallibly only after confirming that narrow shape; allocation
  refusal surfaces as `PdfError` rather than the original parse failure.
  Repaired bytes feed hayro,
  `PylopdfWarning` makes the event visible, `Document.is_repaired` and
  `MetadataProbe.repaired` retain it, and saving normalizes the xref data.
- CJK fallback replaces hayro's `font_resolver`
  (`pick_cjk_fallback` in `rust/src/document.rs`). Detect CJK through
  `CIDSystemInfo` or the `BaseFont` name. Serif-like names use the serif slot;
  other names use sans. Font files come from
  `fonts/pylopdf-fonts-cjk/`, an uv workspace member exposed through the `[cjk]`
  extra and auto-detected during rendering. Explicit and auto-selected fallback
  font input shares the 64 MiB default OpenType boundary described below.
  Auto-discovery keeps package assets as paths and reads both sans and serif
  successfully before one cache update. Failed path reads or size checks must
  preserve configured fonts and caches.
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
  safely source itself without serialization or mutable aliasing. Annotations
  must always include an appearance stream at `AP /N`, because hayro does not
  render annotations without one. The immutable rendering snapshot
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
  Japanese/Han text auto-selects the optional `pylopdf[cjk]` JP-subset sans
  font, or serif for Times aliases. This selects one font for the complete run;
  it is not per-glyph fallback. Hangul, locale-specific Chinese typography, and
  other scripts need an explicit font. `insert_text`, `insert_textbox`,
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
  measure complete tails. `max_text_size=None` explicitly opts trusted
  insertion input out, failures use `text_input_size` or `text_line_count`, and
  refusal must not mutate the document. Paragraph layout remains outside
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
  walk and count once per returned leaf. Tree stacks, cycle sets, inherited
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
  once, and rejects traversal above 4,096 entries/nodes, 8,192 edges, 32
  levels, or 1 MiB of scanned key bytes. Do not turn a truncated lookup into an
  ordinary unresolved destination. Legacy catalog `/Dests` remains a direct
  dictionary lookup after a bounded name-tree miss. `Page.get_links` builds one
  borrowed index lazily per call; do not rescan the tree for each link.
  Traversal stacks, cycle sets, and the borrowed index grow fallibly.
- TOC reads use pylopdf's iterative outline walk rather than lopdf's recursive
  parser. They visit indirect cycles once, release the GIL, index named
  destinations once, and reject partial results above 4,096 nodes/entries,
  8,192 edges, 64 levels, 32 destination indirections, or 1 MiB of
  source/returned text. Page indexes, destination and outline cycle sets,
  outline stacks, and returned entries grow fallibly. `set_toc` preflights the
  entry, depth, and source/encoded-title boundaries before PyO3 copying, using
  `toc_input_size`; direct Rust calls repeat the text boundary, then prepare
  entries and the parent stack fallibly before PDF mutation.
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
  Release CI builds all five native targets on architecture-matched standard
  hosted runners and install-smokes every abi3 and cp314t artifact; do not
  return Linux aarch64 or macOS x86_64 to an unexecuted cross-build.
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
  The PyEmscripten-only release profile uses fat LTO and one codegen unit for
  the single extension module; native maturin builds retain Cargo's default
  release profile.
  The wheel must import `env.__cpp_exception` and must not require a
  wasm-bindgen shim. CI and release builds must also pass
  `tools/smoke_cloudflare.py`, which pins workers-py 1.15.0 and Wrangler
  4.114.0, builds the bundle, starts local `workerd`, and requires the
  module-scope `import pylopdf` in the example to serve `/health`. Emscripten
  enables PyO3's foldhash-backed `hashbrown` maps because its normal class
  builder uses Rust `RandomState`, while Cloudflare denies entropy during
  module startup. Native targets retain PyO3's standard backend. Emscripten
  excludes lopdf's `chrono` and `rayon` features: browser
  clocks pull in js-sys imports, and the global rayon pool cannot create
  workers there. `render_pages` keeps its public contract but runs serially;
  native targets retain bounded rayon execution. Emscripten also omits RTen
  inference while exposing an API-compatible `_OcrEngine` stub that raises
  `OcrError` and directs callers to external OCR plus
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

- Current phase: v0.12.0 was released on 2026-07-26 after the v0.11 line
  shipped earlier that day. The 0.12 line extends the end-to-end untrusted-input
  policy across metadata, annotations, attachments, generated text, search,
  passwords, interpretation snapshots, and positioned glyphs. Its release gate
  executes every native wheel, keeps PyEmscripten and Cloudflare deployment
  covered, expands the licensed interoperability corpus, and renders bounded
  fallback appearances for existing text-markup annotations. The documented
  0.12 candidate API surface is checked deterministically across every native
  Python test lane, with the post-v1.0 SemVer and deprecation contract published
  in four languages. The separately versioned OCR model package remains
  independently gated. Incremental save was rejected after OSS analysis and
  remains on the watchlist. v1.0 is targeted no earlier than 2026-08, after
  field feedback and further product refinement rather than as a
  deadline-driven API freeze.
- lopdf#535 no longer affects pylopdf since the v0.7 hayro extraction engine.
  An upstream fix remains a parallel contribution candidate.
- See [CHANGELOG.md](CHANGELOG.md) for completed history.
