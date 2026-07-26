# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- `Page.get_drawings()` now rejects more than 131,072 aggregate stroke-dash
  values per page before string materialization. Dash serialization no longer
  builds a temporary string vector, and each path is transformed, bounded, and
  converted to return geometry in one element pass without cloning the complete
  path.
- `Page.get_images()` now encodes every PNG fallback directly into the remaining
  64 MiB page payload budget instead of allocating a completed over-limit PNG
  before refusal. Separate RGB/gray and alpha planes are stream-interleaved
  through a 4 KiB scratch buffer rather than a complete additional raster.
- `Document.compress_images()` now rejects more than 65,536 interpreted
  indirect raster placements per call, bounding repeated references in
  addition to the existing 16,384 unique-object limit.

### Performance
- `Document.compress_images()` now borrows candidate JPEG bytes and Flate
  streams through admission, decode, resize, and re-encode. It releases those
  borrows before mutating the atomic lopdf clone instead of cloning every
  complete encoded source, including sources skipped by the 64-million-pixel
  per-image boundary. JPEG encoding also stops at one byte below the source
  payload instead of materializing a complete result that cannot be adopted.

## [0.12.0] - 2026-07-26

### Added
- The redistributable interoperability corpus now includes four pinned PDFium
  fixtures covering Type 3 stencil glyphs, JPX nested behind LZW, form soft
  masks and blending, and existing Widget/Link/Highlight annotations. A
  runtime-derived truncated classic xref adds controlled-refusal coverage.
  Type 3 SVG conversion is positively covered; the current hayro 0.7.1 raster
  variation is an explicit conditional xfail on affected renderer targets.
- Info metadata, TOC title, and page-label style/prefix writes now preflight
  both aggregate caller UTF-8 and exact ASCII/UTF-16BE PDF text size before
  PyO3 copying. Direct Rust calls repeat all three 1 MiB boundaries, failures
  remain atomic, and stable codes are `metadata_input_size`,
  `toc_input_size`, and `page_label_input_size`.
- Annotation creation now rejects aggregate generated subtype plus
  Contents/URI input above 1 MiB before PyO3 string copying, rectangle
  iteration, or PDF mutation. Direct Rust calls repeat the boundary,
  `add_highlight_annot` stops at rectangle 4,097 without first materializing
  the complete input, and refusals use the stable code
  `annotation_input_size`.
- `peek_metadata(..., *, max_file_size=None)` can now reject oversized path or
  byte input before metadata parsing. Path reads use a one-byte-overrun
  boundary, direct Rust calls repeat the check, exact limits succeed, and
  refusals use `LimitError.code == "file_size"`. The unbounded default preserves
  the existing collection-scanning API.
- `Document.embfile_add(..., max_size=64 * 1024 * 1024)` now bounds attachment
  data before its PyO3 copy, matching the default retrieval limit so a
  successful default write remains readable with default `embfile_get()`.
  Direct Rust calls repeat the boundary, refusals are atomic and use
  `embedded_file_size`, and `None` explicitly opts trusted input out.
- AcroForm field names and values now stop at 1 MiB of UTF-8 before font
  discovery, button-state lookup, file reads, or the Rust boundary. Attachment
  lookup/deletion names and aggregate add-time name, filename, and description
  text use the same pre-copy boundary. Direct Rust calls repeat the checks,
  refusals are atomic, and stable codes are `form_field_input_size` and
  `embedded_file_input_size`.
- `Page.insert_text(..., max_text_size=1024 * 1024)` and
  `Page.insert_textbox(..., max_text_size=1024 * 1024)` now bound aggregate
  generated UTF-8 text before PyO3 copying or PDF mutation. Textbox tab
  expansion is preflighted without materializing the expanded string, direct
  Rust calls repeat the boundary, and exact limits succeed. A configured
  insertion budget also caps physical and final wrapped layout at 4,096 lines;
  AcroForm text and choice appearances always enforce the same layout cap.
  Refusals occur before mutation and use `LimitError.code` values
  `"text_input_size"` or `"text_line_count"`; `None` explicitly opts trusted
  insertion input out of both limits.
- `Page.search_for(..., max_hits=4096)` now rejects result amplification
  instead of returning an unbounded partial geometry list. Search terms stop at
  4,096 UTF-8 bytes before PyO3 copying, direct Rust calls repeat both
  boundaries, and refusals use `search_input_size` or `search_hit_count`.
  `max_hits=None` explicitly opts trusted result sets out.
- Open, authenticate, fast metadata probe, and AES-256 save paths now cap each
  password at 127 UTF-8 bytes before PyO3 copying or password-KDF work. Direct
  Rust calls repeat the boundary, refusals use `password_input_size`, and save
  rejection precedes document mutation or output creation.
- `DocumentLimits.max_interpretation_size` can now bound the complete PDF byte
  snapshot consumed by rendering and extraction. It covers retained original
  input plus bounded reserialization after edits, decryption, or AcroForm state
  selection, preflights state-normalization copies, fails without a partial
  hayro cache using `interpretation_size`, and is repeated by the Rust
  boundary. The compatible default is `None`; `DocumentLimits.web()` sets
  64 MiB.
- `DocumentLimits.max_text_glyphs` now bounds cumulative positioned glyph
  records before text-page caching or Python word/span materialization. Normal
  text and table interpretations of the same page share one admission, failed
  pages consume no budget, direct Rust calls repeat the check, and refusals use
  `text_glyph_count`. The compatible default is `None`;
  `DocumentLimits.web()` sets 65,536.
- When `DocumentLimits.max_text_size` is configured, plain-text extraction now
  preflights exact assembled UTF-8 size and caps one private extraction batch
  at twice that payload budget. The direct Rust boundary also accepts at most
  4,096 page entries, preventing repeated page numbers from amplifying a
  bounded interpretation without bound. Refusals use `text_size`.

### Fixed
- Existing RGB Highlight, Underline, StrikeOut, and Squiggly annotations with
  valid `QuadPoints` now render even when their producer omitted `/AP /N`.
  pylopdf synthesizes bounded appearances only in the immutable hayro snapshot;
  the editable annotation dictionary and saved PDF remain unchanged.
  Aggregate synthesis is atomic above 4,096 quads or 65,536 path segments.
- AES-256 output no longer accepts passwords above the PDF 2.0 127-byte
  boundary that lopdf could write but could not reopen with the same password.
  Exact 127-byte user passwords now have round-trip coverage.

### Changed
- The free-threaded extraction benchmark now writes a standalone
  `bench/results/free-threaded-latest.md` report with its version, environment,
  workload, equality check, and reproduction command. The normal benchmark no
  longer relies on a manually appended section that `bench/run.py` would erase,
  and report formatting has regression coverage.
- Release builds now use architecture-matched GitHub-hosted runners and
  install-smoke all five `abi3-py310` plus all five CPython 3.14t wheels.
  Linux aarch64 and macOS x86_64 are no longer unexecuted cross-builds. A
  non-publishing release dry run verified all ten create, save, reopen,
  extraction, rendering, and free-threaded buffer paths.

### Performance
- `Page.replace_text()` now rejects oversized aggregate caller text by counting
  UTF-8 incrementally instead of first allocating complete encoded copies.
  OCR text-layer aggregation uses the same allocation-free counter within its
  1 MiB boundary.
- `Page.insert_textbox()` now measures the complete paragraph first when it
  fits on one line, avoiding repeated UAX #14 prefix measurement. On the paired
  local 50,000-character Standard 14 wide-line benchmark, median insertion
  fell from 928.7 ms to 31.1 ms (29.9x faster) while preserving layout
  semantics.
- Wrapped paragraphs now collect UAX #14 and grapheme boundaries once, then
  use local galloping probes with binary refinement. Long unbreakable runs no
  longer rescan the remaining paragraph or measure every growing grapheme
  prefix for each emitted line.
- `Page.search_for(max_hits=None)` now advances byte-to-character positions
  once and reads the first/last mapped glyph without allocating a temporary
  vector for every match. On the paired cached 100,000-match single-line
  benchmark, median search fell from 371.1 ms to 79.9 ms (4.6x faster);
  bounded default refusal completed in 3.1 ms.
- Oversized AES-256 passwords are now rejected before KDF work. A pathological
  1 MiB password took about 64 seconds in the paired local save probe, while
  valid 127-byte input took about 11 ms.
- PyEmscripten builds now apply fat LTO with one codegen unit while native
  builds retain Cargo's default release profile. In paired pinned CI runs this
  reduced the wheel by 113,135 bytes (2.78%), the installed extension by
  643,054 bytes (5.83%), and the observed Wasm linear-memory high water by
  851,968 bytes (1.15%) with exact compatibility results. The Pyodide CI job
  increased from 2m03s to 4m40s.

## [0.11.1] - 2026-07-26

### Added
- `Document.render_page()`, `Page.render()`, and
  `Pixmap.tobytes(max_size=64 * 1024 * 1024)` now bound a single encoded PNG
  before Python `bytes` conversion. The shared PNG writer refuses the crossing
  write without retaining output beyond the configured budget; exact
  boundaries succeed, direct core rendering repeats the check, and `None`
  explicitly opts trusted workloads out. Render failures use
  `LimitError.code == "render_output_size"` and direct Pixmap failures use
  `"pixmap_output_size"`. `Pixmap.save()` now streams the same encoder directly
  into its failure-atomic sibling instead of retaining a second completed PNG.
- `OcrEngine(..., max_model_size=64 * 1024 * 1024)` now bounds the
  cumulative detector, recognizer, and dictionary input before RTen parses
  either model. Paths are read with the GIL released through a one-byte-overrun
  boundary, dictionaries stop before a 65,537th materialized entry, and direct
  core construction repeats the checks. Refusals use `LimitError.code ==
  "ocr_model_size"` or `"ocr_dictionary_entries"`; `None` explicitly opts
  trusted custom model sets out.
- Explicit and automatically selected OpenType input for `insert_text`,
  `insert_textbox`, `set_form_field`, and `set_fallback_font` now shares a
  `max_font_size=64 * 1024 * 1024` default. `fontbuffer=` is rejected before
  its PyO3 copy, while `fontfile=` is read through a one-byte-overrun bounded
  Rust path with the GIL released. Direct core byte/path variants repeat the
  boundary, failures use `LimitError.code == "font_input_size"` without
  mutating documents or fallback caches, and `None` explicitly opts trusted
  workloads out. Bundled CJK assets remain paths rather than an unbounded
  Python byte cache, and automatic fallback reads both sans and serif before
  one cache update.
- `Page.insert_image(..., max_size=64 * 1024 * 1024,
  max_pixels=64_000_000)` now bounds JPEG/PNG encoded input and PNG decode
  amplification before mutation. `filename=` is read under the released GIL
  through a one-byte-overrun bounded Rust path rather than Python
  `Path.read_bytes()`, while `stream=` is rejected before its PyO3 copy.
  Limits are repeated at the direct Rust boundary, use stable
  `LimitError.code` values `image_input_size` and `image_pixel_count`, and
  accept `None` as the explicit trusted-input opt-out. `pixmap=` remains
  outside these limits because rendered Pixmaps are already bounded.
- `Pixmap.save(path)` now uses an unpredictable, exclusively created
  same-directory sibling before atomically replacing the
  requested path. Replacement failures preserve existing output and remove the
  temporary file, existing regular-file permissions are retained, and a final
  symlink remains in place while its target is updated. Encoding and I/O still
  release the GIL and errors remain under `PdfError`.
- `Document.save()` now streams normal, object/xref-stream, and AES-256 output
  to a securely created same-directory sibling and atomically replaces the
  requested path only after a complete successful write. Serialization and
  replacement failures preserve an existing target and clean up the temporary
  file; an existing regular file's POSIX mode is carried forward. Errors remain
  under `PdfError` and name the requested path. A final symlink is preserved
  while its resolved target is atomically updated. Save options retain their
  existing in-memory mutation semantics before I/O.
- `Document.tobytes(..., max_size=512 * 1024 * 1024)` now bounds serialized
  PDF output in the normal, object/xref-stream, and AES-256 encrypted paths.
  A shared Rust writer refuses the write that would cross the boundary, so it
  never retains an oversized completed PDF before creating Python `bytes`.
  Direct core calls enforce the same optional boundary, failures use
  `LimitError.code == "pdf_output_size"`, and `max_size=None` explicitly opts
  out for trusted workloads. Existing save options still mutate the document
  before serialization as documented.
- `Page.replace_text(..., max_size=64 * 1024 * 1024)` now bounds decoded page
  content, font encoding data, replacement growth, and the final re-encoded
  stream. Aggregate search/replacement/fallback input stops at 4,096 UTF-8
  bytes. Replacement preparation is linear in input text, releases the GIL,
  and commits one page-owned content stream only after every fallible step
  succeeds. This fixes edits leaking through shared `/Contents` after
  `copy_page()` or repeated selection. No-match calls and all refusals preserve
  the document and caches; stable limit codes are `replacement_input_size` and
  `replacement_output_size`, and `max_size=None` is the trusted-input opt-out.
- `delete_pages()`, `select()`, and `insert_pdf()` now cap one structural batch
  at 4,096 page entries before iterable materialization or graph import. The
  Rust core enforces the same boundary. Generators stop at the 4,097th item,
  over-limit calls preserve existing `Page` views, and `delete_pages([])` is a
  true no-op that no longer invalidates caches or marks views stale.
- Drawing insertions now preflight raw page `/Contents` arrays and reference
  chains before cache invalidation, image decoding, or dependent PDF-object
  creation. The resulting array is capped at 4,096 stream references, including
  the initial `q`/`Q` isolation pair. Verified page IDs retain that state only
  inside the current `_Document`, so untrusted PDF keys cannot spoof it; this
  fixes the previous behavior that nested another pair on later insertions.
  Over-limit calls leave the document byte-for-byte unchanged in memory.
- `Document.render_page_svg(..., max_size=64 * 1024 * 1024)` and
  `Page.render_svg()` now reject UTF-8 output above the configured boundary
  before PyO3 creates the Python string, using `LimitError.code ==
  "svg_output_size"`. `max_size=None` explicitly opts out. hayro-svg 0.7 still
  materializes one internal Rust string before pylopdf can enforce the limit.
- `Document.to_markdown(..., max_size=64 * 1024 * 1024)` and the page form now
  cap UTF-8 output with `LimitError.code == "markdown_output_size"` and stop
  page iterable materialization above 4,096 entries. Document conversion no
  longer retains every page's layout, tables, and words simultaneously: a
  page-at-a-time first pass keeps only heading-size counts, then a second pass
  renders each page into the bounded output accumulator. Each detected table
  receives the remaining aggregate budget before page Markdown is assembled.
  Page headings, paragraphs, lists, and tables are charged as entries are
  retained, so an oversized page is refused before the final join. Paragraph
  and consecutive-list joining now use one linear final assembly instead of
  repeated immutable-string concatenation.
  `Table.to_markdown(..., max_size=64 * 1024 * 1024)` also computes the exact
  escaped UTF-8 size before allocating cell output, including merged-cell
  expansion. `max_size=None` explicitly opts out of either output limit.
- `Document.render_pages(..., max_size=512 * 1024 * 1024)` now stops page
  iterable materialization above 4,096 entries and atomically charges each
  completed PNG against one cumulative encoded-output budget across serial,
  rayon, and PyEmscripten execution. Over-limit batches return no partial list
  and raise `LimitError` with code `render_output_size`; `max_size=None` is the
  explicit unbounded opt-out.
- `Page.insert_ocr_text_layer()` now stops iterable materialization at 4,096
  non-empty words and rejects aggregate UTF-8 text above 1 MiB. Rust enforces
  the same boundaries for direct core calls and stops CID assignment before a
  65,535th distinct character. CID mapping, ToUnicode, and content operators
  are prepared before PDF mutation, while failed input no longer invalidates
  rendering and interpretation caches. Cache invalidation still precedes the
  first PDF mutation so malformed resource failures cannot leave stale views.
- Drawing insertions now compare existing leading/trailing page-content streams
  against the `q`/`Q` isolation sentinels by borrowing their bytes. The shared
  path no longer clones both complete streams merely to test exact three-byte
  content before every image, PDF-page, text, textbox, or OCR-layer insertion.
- `Document.metadata` now decodes only the eight public standard Info fields,
  releases the GIL, and rejects aggregate source or returned text above 1 MiB
  instead of materializing every custom dictionary entry. `peek_metadata()`
  applies the returned-text boundary too. `set_metadata()` now sends one
  preflighted Rust batch with 1 MiB source/encoded limits, moves inline Info
  dictionaries without cloning them, and preserves every existing field and
  cache when any update is rejected.
- `Document.get_toc()` now replaces lopdf's recursive outline parser with an
  iterative, cycle-aware walk that releases the GIL and rejects partial output
  above 4,096 nodes/entries, 8,192 edges, 64 levels, 32 destination
  indirections, or 1 MiB of source/returned text. Named destinations are
  indexed once per call under their existing limits. `set_toc()` preflights the
  same entry, depth, and title-text boundaries before mutation, so failed
  replacements preserve the existing outline.
- Named-destination lookup for `Page.get_links()` now uses an iterative,
  cycle-aware `/Names/Dests` walk and rejects silent unresolved results above
  4,096 entries/nodes, 8,192 edges, 32 levels, or 1 MiB of scanned key bytes.
  One borrowed index is built lazily per page call, so up to 4,096 named links
  no longer rescan up to 4,096 destinations each. The previous recursive lookup
  stopped at depth 16 without distinguishing an absent destination from a
  truncated or cyclic tree.
- `Page.annots()` and `Page.get_links()` now borrow `/Annots` arrays, release
  the GIL, and reject partial output above 4,096 array entries or 1 MiB of
  aggregate encoded/returned subtype, Contents, URI, file, and destination
  text per call. Named-destination resolution shares the same budget.
  Annotation creation preflights the page count, 1 MiB aggregate generated
  subtype plus Contents/URI input, and 4,096 highlight rectangles before adding
  appearance objects or invalidating caches, so failed additions preserve the
  document and successful output remains readable under the same budget.
- AcroForm button handling now bounds field expansion at 4,096 widgets and
  normal-appearance dictionaries at 8,192 state entries, 4,096 unique returned
  names, and 1 MiB of encoded or returned state-name text. Boolean state lookup
  releases the GIL and uses linear deduplication. Appearance synchronization
  validates every missing `Off`/on key before mutation, so a fill cannot create
  a state dictionary that the next call must reject. Indirect `/Kids` arrays
  are now resolved consistently.
- AcroForm field-tree reads now borrow object shapes, visit indirect cycles
  once, release the GIL, and reject partial output above 4,096 entries/nodes,
  8,192 edges, 64 levels, 1 MiB of encoded, decoded, or returned names/values,
  or 4,096 choice-value items. Inherited values are shared during traversal and
  charged for each returned leaf instead of being cloned without a complete
  result budget. `Document.set_form_field()` enforces the tree and 1 MiB value
  boundaries atomically.
- Page-label number-tree reads now borrow node shapes, visit indirect cycles
  once, release the GIL, and reject partial output above 4,096 entries/nodes,
  32 levels, or 1 MiB of encoded or decoded style/prefix text. The previous
  depth-only walk cloned nodes and could repeat a cycle before silently
  truncating it. `Document.set_page_labels()` enforces the same entry/text
  boundary before mutation, so failed edits preserve document bytes and caches.
- `Document.get_pdfa_claim(*, max_size=1024 * 1024)` now bounds every XMP
  metadata decoding layer before inspecting a PDF/A self-declaration. Known
  larger packets can raise the positive limit or use `None` as an explicit
  unbounded opt-out; rejection uses `LimitError.code == "xmp_metadata_size"`.
  Decode failures no longer fall back to encoded bytes, work releases the GIL,
  and token-aware matching rejects lookalike prefixes, quoted attribute values,
  comments, and CDATA instead of misreporting them as `pdfaid` claims.
- `Document.embfile_get(name, *, max_size=64 * 1024 * 1024)` now bounds every
  attachment decoding layer before materializing Python bytes. Callers can raise
  the positive limit for a known large file or pass `None` as an explicit
  unbounded opt-out; rejection uses `LimitError.code == "embedded_file_size"`.
  Decode failures no longer masquerade as raw attachment contents, PDF filter
  abbreviations are normalized, and decompression releases the GIL. EmbeddedFiles
  name-tree reads now borrow direct object shapes, visit reference cycles once,
  and reject traversal above 4,096 entries/nodes, 32 levels, or 1 MiB of names
  instead of returning partial metadata. Adding the 4,097th entry is refused
  atomically. Attachment edits now preflight the Catalog write target, avoid a
  whole-document rollback clone, and bound the new key/filename/description at
  1 MiB of aggregate input text. Existing inline FileSpecs are validated before
  cloning at 4,096 direct objects, 32 levels, and 1 MiB of direct string/name/
  stream data; indirect references remain cheap leaves.
- `Page.get_images()` now rejects per-page output amplification above 4,096
  placements, 64,000,000 cumulative source pixels, or 64 MiB of returned image
  payloads instead of materializing a partial list. The Flate-to-DCT JPEG
  passthrough path stops decompression at the remaining byte budget. Regressions
  cover repeated shared images, oversized declared dimensions, cumulative
  passthrough bytes, and a highly compressed oversized JPEG payload.
- Lenient opening now repairs one narrowly defined malformed-PDF case: an
  incorrect final `startxref` offset when the final revision still contains an
  intact classic xref table and a full lopdf retry succeeds under the original
  limits. It never guesses object offsets, repairs xref streams, or falls back
  to an earlier revision. `Document.is_repaired`, the `repaired` key from
  `peek_metadata()`, and `PylopdfWarning` make the recovery visible; saving
  rewrites canonical cross-reference data. A CC BY 4.0 PDF 2.0 fixture protects
  open, extraction, probing, prefixed-input, save/reopen, and refusal cases.
- `Document.compress_images()` now also converts safe, unmasked, single-filter
  8-bit DeviceGray/DeviceRGB Flate raster XObjects to smaller JPEG payloads.
  Absent and consistent PNG predictors use lopdf's bounded decoder before the
  existing placement-aware Lanczos3 path. Unsupported predictor/decode
  semantics are skipped, malformed streams roll back atomically, and the
  existing per-image and document-wide pixel limits still apply.
- `Page.insert_text()` and `Page.insert_textbox()` now discover the optional
  `pylopdf[cjk]` JP-subset fonts when Japanese or Han text has no explicit font
  source. Times aliases select Noto Serif JP and other aliases select Noto Sans
  JP, then krilla embeds only the used glyphs. The boundary remains
  honest: this is whole-run font selection rather than per-glyph fallback, and
  Hangul, locale-specific Chinese typography, other scripts, and alternate
  typefaces still require `fontfile=` or `fontbuffer=`.

### Fixed
- The PyEmscripten wheel no longer requests OS entropy while PyO3 registers
  classes during a module-scope import. Emscripten alone uses PyO3's
  foldhash-backed `hashbrown` class-builder maps, allowing Cloudflare
  `workerd` to initialize a Worker before request-scoped entropy is available;
  native targets retain their existing backend. The Cloudflare release gate
  now starts local `workerd` after bundling and requires the example's
  module-scope `import pylopdf` to serve `/health`, rather than stopping at a
  Wrangler dry run.

### Performance
- `Document.render_pages()` now reuses one hayro render cache across serial and
  PyEmscripten batches and one worker-local cache per native worker task. Native
  workers dynamically claim page indexes before the completed PNGs are restored
  to input order in linear time, retaining load balancing across pages of
  unequal complexity without sharing hayro's non-thread-safe cache. An
  interleaved before/after benchmark over eight paired runs on the first 12
  `usrguide.pdf` pages at 2x reduced 1/2/4/8-worker medians from
  362.9/196.7/111.7/91.3 ms to 324.4/178.7/104.6/87.5 ms
  (10.6%/9.2%/6.3%/4.2%). Rendering bytes and public behavior are unchanged.

## [0.11.0] - 2026-07-26

### Documentation
- Published a four-language API stability policy that defines the public
  boundary, SemVer impact, post-v1.0 deprecation lifecycle, behavioral
  compatibility, runtime support changes, and emergency exceptions. The 0.11
  surface is explicitly a field-tested candidate baseline rather than a
  premature v1.0 freeze.
- Updated all four pymupdf migration guides for native offline OCR and direct
  `Pixmap.save(path)` output, removing the stale classification of OCR as an
  unimplemented ecosystem-only feature.

### Added
- A checked-in, deterministic public API snapshot now detects unreviewed
  changes to exports, callable signatures and defaults, documented members,
  TypedDict keys, type aliases, NamedTuple fields, enum and constant values,
  and exception inheritance across every native Python test lane. The maintenance
  command emits a unified diff and requires an explicit reviewed refresh,
  making API freeze preparation enforceable without pretending that a machine
  diff can decide SemVer.
- `DocumentLimits` now applies one immutable untrusted-input policy across file
  bytes, pages, indirect objects, direct object nesting, per-stream and
  cumulative decompression, page-content decompression, and cumulative
  interpreted Unicode glyph payload. `DocumentLimits.web()` provides a
  conservative bounded-worker profile; the compatible
  `max_decompressed_size=` shorthand remains available. Limit violations raise
  the `PdfError` subclass `LimitError` with a stable machine-readable `code`.
  `Document.complexity` reports cheap page/object/stream/encoded-byte/depth
  facts without decoding streams or rendering. Native and Pyodide share policy
  regressions, representative vector/scan cases, and rejection codes; scheduled
  Atheris fuzzing adds generated bad-xref, cycle, deep-object, broken-stream,
  page-count, and compression-bomb seeds. A reproducible native/Wasm trend
  benchmark records bounded open/extract/rejection time plus process or Wasm
  linear-memory high-water evidence in `bench/results/limits-latest.md`.
- A reproducible Pyodide 0.28.3 builder now produces a static,
  WebAssembly wheel. It pins Python 3.13.2,
  Emscripten 4.0.9 and its Node.js runtime, Rust 1.95.0, pyodide-build, maturin,
  the Pyodide cross-build environment checksum, and every Python build
  dependency hash. The builder first checks the legacy
  `pyodide_2025_0_wasm32` artifact in Pyodide 0.28.3, then deterministically
  retags the same binary as the PEP 783
  `pyemscripten_2025_0_wasm32` artifact accepted by PyPI. The verifier checks
  filename and embedded metadata tags, the WebAssembly exception import, and
  absence of wasm-bindgen shims; the runtime smoke test covers byte-stream
  loading, text extraction, batch rendering, Python exception recovery, and
  reuse after malformed input. Pull requests build and bundle the final wheel
  with pinned Cloudflare `workers-py` and Wrangler versions. Tagged releases
  attest and publish it with the native artifacts, then resolve it back from
  PyPI and dry-run a Cloudflare Workers bundle before creating the immutable
  GitHub release. Emscripten builds omit lopdf's native clock and rayon features
  and execute `render_pages` serially, while native builds retain their existing
  bounded worker pools.
- Native Python and Pyodide now run one shared WebAssembly compatibility suite
  and compare stable logical results exactly. It covers bytes-only PDF 2.0
  loading, plain text and document Markdown, embedded Japanese text, inferred
  vertical CJK, multicolumn order, bordered and borderless tables, right-angle
  rotation, vector drawings, image-only pages, AES-256 authentication,
  document generation with a subset-embedded OpenType font, textbox layout,
  pixmaps, ordered batch rendering, virtual-filesystem save, merge/select, and
  the public exception hierarchy. The corpus uses only small redistributable
  fixtures. A four-language WebAssembly reference records the tested surface,
  Emscripten's intentional serial `render_pages` behavior, virtual-filesystem
  boundaries, and the unsupported direct-PyPI path in Pyodide 0.28.3's older
  `micropip`.
- PyEmscripten builds now omit the unsupported RTen OCR inference runtime while
  retaining an explicit `OcrEngine` stub that raises `OcrError` and directs
  callers to external OCR plus `insert_ocr_text_layer`. The pinned wheel fell
  from 4.522 to 3.834 MiB (-15.21%), its Wasm code section fell 21.92%, and the
  tested Cloudflare bundle fell from 4.570 to 3.882 MiB compressed. CI now
  records wheel/Wasm sections, staged startup and workload time, linear-memory
  checkpoints, and Wrangler bundle sizes as a retained JSON artifact. The
  exact repository Worker example, four localized deployment guides, and
  `bench/results/wasm-latest.md` document the paid-plan Cloudflare boundary.
  The complete core fits that boundary, so a fragmented lightweight
  distribution was rejected for now.
- `Document.compress_images(dpi=150, quality=75)` now downsamples and
  recompresses safe JPEG XObjects for smaller attachment-oriented PDFs. hayro
  measures every placement and preserves the pixels required by the largest
  reuse; lopdf applies the edit atomically. The initial boundary accepts direct
  8-bit DeviceGray/DeviceRGB DCT streams without masks or custom decode
  semantics, skips outputs that are not smaller, records a private quality
  marker for repeat-call idempotence, releases the GIL, and returns typed
  compression statistics. Per-image, document-wide pixel, and unique-object
  limits bound untrusted inputs.
- `Page.get_drawings()` now extracts interpreted vector fill, stroke, and
  combined paths through hayro in rotation-resolved display coordinates. Pymupdf-style
  typed dictionaries expose self-contained line/cubic commands, path bounds,
  RGB/opacity, fill rule, width, cap, join, and dashes. Pattern paints retain
  geometry with `None` color, quadratic curves become exact cubics, and
  adversarial output is rejected above 8,192 paths or 131,072 commands instead
  of returning a partial result. Synthetic style/rotation cases and ten
  real-world PDFs cover the native extractor.
- `Pixmap.save(path)` now encodes and writes PNG output directly while
  releasing the GIL. It accepts strings and path-like objects, retains the
  fast render-oriented compression used by `tobytes()`, and reports filesystem
  failures through the `PdfError` hierarchy. Non-PNG extensions are rejected
  instead of silently writing mismatched content.
- `Page.show_pdf_page()` now accepts its own `Document` as the source, including
  the target page itself. Native lopdf cloning provides a stable pre-edit
  snapshot before Form-XObject import, eliminating the previous
  serialize-and-reopen workaround without retaining unreachable duplicate
  source objects.
- `Page.insert_image(..., pixmap=)` now embeds an immutable rendered `Pixmap`
  directly from its straight-alpha RGBA8 storage. The Rust path avoids PNG
  encoding and decoding, preserves transparency through a soft mask, and omits
  that mask for fully opaque input. `rotate=` turns JPEG, PNG, and Pixmap
  sources clockwise in 90-degree steps, composes with page rotation, and
  preserves the rotated aspect ratio.
- Native OCR field validation now includes a licensed, image-only Japanese
  archival scan with manually verified ground truth at 150 and 300 dpi. The
  reproducible report also checks two distinct documents through one shared
  engine at admission limits 1 and 2, verifies exact agreement with sequential
  recognition, and publishes the lack of a throughput gain alongside the
  increased live-buffer risk.
- `Page.find_tables()` now refines coarse vector-grid spans when at least three
  evenly led text records densely occupy the same cross-axis cell slots. The
  conservative inference is symmetric across right-angle page rotations,
  assigns hybrid grids a 0.95 ranking score, and leaves multiline merged
  headers intact when their slot signatures differ. Rotated word boxes now
  follow their baseline geometry, and Markdown expands merged cells from exact
  anchor mappings instead of ambiguous neighboring `None` values. Independent
  public-domain FBI NICS and US Senate PDFs add dense numeric, sparse-rule,
  merged-header, borderless-body, and rotated-table regressions. The refreshed
  nine-file benchmark publishes the full tradeoff: pylopdf leads all nine
  first-page renders, five extraction cases, and the combined merge, while
  pymupdf leads the other four extraction cases.
- `Document.to_markdown()` and `Page.to_markdown()` now insert complete bordered
  tables in document reading order by default. Cell text is removed from the
  surrounding prose and from heading-size inference while words outside a
  table on the same physical line are retained. Empty grids retain their
  geometric position, merged cells expand for Markdown, and right-angle page
  rotations normalize table rows to logical text direction. Pass
  `table_strategy="text"` to add conservative non-overlapping borderless
  candidates, or `None` to retain the previous text-only conversion. Synthetic
  bordered, borderless, merged-order, empty-grid, and 0/90/180/270-degree cases
  plus the public-domain IRS Form 1040 corpus cover the integration.
- Optional offline OCR through `pylopdf[ocr]`: a reusable `OcrEngine`,
  `Page.get_text_ocr()`, and idempotent-by-default `Page.apply_ocr()` run
  PP-OCRv6 small locally through the pure-Rust RTen runtime. Detection uses
  bounded overlapping tiles, reading order retains sustained columns, results
  use rotation-resolved display coordinates, optional region clips support
  mixed digital/scanned pages, and complete render-and-recognize calls are
  limited to one per engine by default so accidental outer concurrency cannot
  multiply the measured per-call memory. Explicit `rotation=90 / 180 / 270`
  corrections recognize sideways or upside-down scans without changing PDF
  page rotation, map result boxes back to original display coordinates, and
  orient the invisible text baseline for extraction and search. Direction-aware
  line/column ordering preserves multi-line logical text at every right-angle
  orientation, and rotated search boxes match OCR geometry. Invisible layers
  remain searchable without changing rendered pixels. The separately versioned,
  data-only `pylopdf-ocr-models` wheel includes the multilingual detector,
  recognizer, and dictionary with pinned provenance and artifact hashes.
  English and Japanese integration tests cover recognition and searchable
  save/reopen behavior. Arbitrary skew, automatic page-orientation detection,
  and mixed-orientation typography remain outside this first native engine.
  Model release CI now requires byte-identical repeated builds, validates PyPI
  metadata, and smoke-tests isolated wheel and sdist installations, including
  dependency freedom, license payloads, typing markers, exact resource names,
  and streamed artifact hashes.
- Public mapping-shaped APIs now expose importable `TypedDict` contracts:
  nested text layout, images, annotations, links, AcroForm fields, page labels,
  metadata updates/results, and fast metadata probes. `WordEntry`,
  `BlockEntry`, and `FormFieldType` are also runtime-importable type aliases.
  Runtime values and pymupdf-compatible dictionary keys are unchanged.
- `Page.insert_textbox()` now wraps paragraphs inside display-coordinate
  rectangles and returns the remaining vertical space without drawing when the
  value is negative. Standard 14 text uses canonical Adobe AFM widths;
  arbitrary OpenType fonts use HarfRust shaping and krilla subsetting. UAX #14
  line breaks cover CJK text without spaces, overlong words break at grapheme
  boundaries, and left, center, right, and justified alignment work on rotated
  pages. Explicit newlines, tab expansion, custom leading, overlay order,
  missing-glyph errors, and save round-trips are covered.
- `Document.set_form_field()` now regenerates native appearances for text,
  combo/list choice, checkbox, and radio widgets. Text auto-fits with inherited
  alignment and multiline flags; widget rotation, background, and border
  styling are retained. WinAnsi uses Helvetica, while `fontfile=` /
  `fontbuffer=` subset-embed arbitrary OpenType text through HarfRust and
  krilla; `pylopdf[cjk]` is selected automatically for non-WinAnsi text.
  Missing button states receive vector appearances, non-empty authored states
  are preserved, and hayro's lack of appearance-state dictionary support is
  bridged in rendering without changing the canonical saved PDF. Updates are
  atomic on generation errors, release the GIL, preserve multiline control
  characters through UTF-16BE, complete other representable missing widget
  appearances before clearing `NeedAppearances`, and render after save/reopen.
- AcroForm comb text fields now resolve inherited `MaxLen` and alignment,
  center each Unicode grapheme in its assigned position with either Helvetica
  or a subset-embedded OpenType font, and reject overlength or malformed flag
  combinations atomically.

## [0.10.0] - 2026-07-25

### Documentation
- Rebuilt the English, Japanese, Simplified Chinese and Korean documentation
  with Zensical 0.0.51 and a custom
  responsive Living Document theme, including instant navigation, search,
  same-page language switching, light/dark palettes, reproducible benchmark and
  security pages, `llms.txt`, and an Open Graph social card
- Defined English as the canonical language and Japanese, Simplified Chinese
  and Korean as first-class translations, with shared anchors and strict builds
  for every locale
- Standardized repository-facing documentation, configuration comments,
  docstrings, test descriptions, and benchmark reports on English, with an
  automated check that preserves localized documentation and multilingual test
  fixtures
- Updated the GitHub Pages upload and deployment actions to their Node.js
  24-native v5 releases

### Performance
- The first render or text extraction of an unedited, unencrypted document now
  parses the original input directly instead of serializing the recovered
  lopdf object graph first. Hayro-incompatible inputs still fall back to the
  normalized representation, and edits discard the original-byte fast path.
  A minimized damaged-input fuzz case improved from 9.3s to 0.06s; the original
  15-second timeout reproducer improved from 23.1s to 0.04s
- `render_page` and `Pixmap.tobytes()` now encode PNG with
  `Compression::Fast` (fdeflate) and release the GIL during encoding and
  alpha-unpremultiply. Profiling showed the previous default (Balanced +
  adaptive filtering, ~11 MB/s on photographic RGBA) accounted for up to 85% of
  render time. Measured on the real-world corpus (2x scale, medians): worst
  case 278→43 ms; **rendering now beats pymupdf on all 7 corpus files**
  (previously 0/7 wins on the larger files). PNG output grows ~10-15% but stays
  smaller than pymupdf's; re-compress externally if size matters.
  `get_images()` keeps the higher-compression encoder for stored artifacts
- `Document.render_pages()` renders an ordered page selection from one
  immutable hayro snapshot on a dedicated rayon pool while the GIL is released.
  It preserves duplicates and all `render_page` scale/DPI/background semantics,
  defaults to at most four workers, accepts 1–64 explicitly, and further caps
  estimated live raster and conversion buffers to roughly 512 MB. On the
  published 12-page `usrguide.pdf` 2x workload, 1/2/4/8 workers measured
  400.8/200.5/118.5/83.6 ms, reaching 4.80x at eight workers
- `save()` / `tobytes()` now compile `flate2` against the `zlib-rs` backend
  instead of the default Rust `miniz_oxide` implementation. Measured on a 3x
  merge of the full real-world corpus (554 pages) saved with `garbage=3` +
  `deflate` + `object_streams`: median 74ms → 66ms (13% faster), output size
  within 0.01%

### Fixed
- Overlapping text paint runs on one baseline now retain their source-order
  phrases before geometric ordering. Exact overprints, distinct strings,
  partial overlaps, and slight offsets no longer interleave glyphs in plain
  text, words, blocks, layout dictionaries, search, or Markdown; separate
  overprints remain separate instead of being discarded as duplicates
- `max_decompressed_size=` now validates page content and other streams that
  hayro would otherwise decompress lazily. Image streams are bounded by decoded
  RGBA size, and filter chains that cannot be bounded safely are rejected while
  the limit is enabled
- `insert_pdf()` and `show_pdf_page()` now prune source objects that are not
  reachable from the imported page or Form XObject, preventing hidden
  attachments and metadata from leaking into saved output
- Adding an annotation to a page made by `copy_page()` / `select()` now
  clone-on-writes a shared indirect `/Annots` array instead of modifying every
  duplicate
- Reading an embedded-file name tree containing inline FileSpec dictionaries no
  longer mutates the document or grows its serialized output
- Malformed, truncated JPEG SOF segments now raise `PdfError` instead of
  panicking in Rust
- Page boxes and new-page dimensions outside PDF's finite real-number range are
  rejected instead of becoming infinities during the Python-to-Rust conversion
- The repository's documented strict Clippy and default mypy commands now pass:
  the complex destination result has a named alias, and optional interoperability
  imports are covered when that dependency group is absent
- Extraction, search, positioned layout (words/blocks/dict) and image bboxes on
  **rotated pages** now come out in display space with the rotation resolved,
  matching rendering: the extraction Context receives the same
  `initial_transform` as hayro's renderer instead of a manual y-flip. Reading
  order on rotated pages is fixed as a result (previously each glyph landed on
  its own line, bottom-to-top), and pages with a non-zero CropBox origin get
  correctly offset coordinates too. The OCR text layer and `to_markdown` benefit
  on rotated scans as well

### Added
- Native `cp314-cp314t` wheels for free-threaded Python 3.14 now keep the GIL
  disabled, run the full suite in CI, and are built for all five release
  targets alongside the existing Python 3.10–3.14 abi3 wheels. Distinct
  documents support concurrent operations; same-document external calls must
  be serialized, with `render_pages()` as the supported parallel rendering
  boundary. Two independent full-document extractions measured 1.74x at two
  threads on CPython 3.14.6t
- `Pixmap` is now immutable. Version-specific wheels expose its RGBA8 storage
  through a read-only, one-dimensional, zero-copy buffer suitable for
  `memoryview()` and NumPy. The `abi3-py310` wheel retains the portable
  one-copy `samples` fallback because `Py_buffer` is not in its stable ABI.
  Local Windows wheels measured 4.43 MB for cp314t and 4.44 MB for abi3
- `Page.get_pixmap(clip=)` crops a render in rotation-resolved display
  coordinates, clamps clips to the page, rounds fractional edges outward to
  pixel boundaries, and rejects non-intersecting rectangles. hayro 0.7 cannot
  offset its viewport, so the first implementation still rasterizes the full
  page and keeps the existing full-page size limits
- Documentation site (EN/JA) at <https://yhay81.github.io/pylopdf/> —
  mkdocs-material with static-i18n, deployed from CI on every push to main.
  Includes a hand-written **pymupdf migration guide** (API mapping table,
  behavioral differences, ecosystem answers for the deliberately-unimplemented
  parts) plus getting-started, ecosystem-recipe and API-overview pages
- Extraction spans now carry the font's PostScript name (`"font"`) and
  pymupdf-compatible `"flags"` (italic=2, serif=4, monospace=8, bold=16),
  sourced from embedded-font metadata (weight / italic bits, with name-based
  fallback). `to_markdown` turns bold / italic body spans into `**` / `*`
  emphasis (headings stay plain). Standard-14 (Type1) fonts report empty
  name / zero flags because hayro does not expose Type1 metadata yet
  (upstream candidate)
- Reproducible benchmark harness (`bench/run.py`, optional `bench` dependency
  group): same corpus / same tasks / medians against pymupdf, pypdf and
  pdfplumber, with extraction similarity vs pymupdf as a correctness proxy.
  Wins and losses are published as-is to `bench/results/latest.md` together
  with environment details
- SECURITY.md (private reporting via GitHub Security Advisories, guidance for
  handling untrusted PDFs with `max_decompressed_size=`) and a RustSec
  `cargo audit` job in CI
- CI job exercising the abi3 lower bound: the full test suite now also runs on
  Python 3.10
- `Page.get_links()` reads link annotations: both `/A` actions (URI, GoTo,
  GoToR, Launch, Named) and direct `/Dest` entries. GoTo named destinations
  resolve through the `/Names` name tree (nested `Kids`, cycle-guarded) and the
  legacy `/Dests` dictionary; destinations report a 0-based page number plus
  the target's display-space point (`/XYZ`, `/FitH`, `/FitV`) and zoom.
  Returns pymupdf-style dicts with `LINK_GOTO` and related type constants and a
  `Point` type
- Weekly coverage-guided public-API fuzzing exercises bounded open, positioned
  extraction, search, rendering, editing, object-stream saving, and reopening
  over the redistributable real-world corpus
- Text extraction, positioned layout, and search now share a bounded
  generation-invalidated `TextPage` interpretation cache. Line dictionaries
  report the transformed baseline direction instead of a hard-coded value,
  laying the geometry foundation for multicolumn and vertical text
- Table interpretation now has its own bounded page cache, so vector-rule and
  borderless-table analysis is paid only by `find_tables()` and does not burden
  ordinary text extraction or search
- Text extraction now detects sustained whitespace gutters and orders
  multicolumn pages top-to-bottom within each column, then left-to-right across
  columns. Full-width headings and footers retain their page-level position,
  while isolated wide gaps such as a header plus page number stay on one line
- `Page.find_tables()` detects complete, axis-aligned bordered grids without
  rasterization and returns pymupdf-style `TableFinder` / `Table` objects.
  Tables expose display-space bboxes, row-major cells, `extract()`, and
  `to_markdown()`. Rules may be strokes or thin filled rectangles. Rectangular
  row/column spans are reconstructed from missing internal dividers;
  continuation slots are `None`, and Markdown can fill them from above or the
  left. The intentionally high-confidence detector still rejects broken outer
  grids and compact filled decorations
- `Page.find_tables(strategy="text")` adds an explicit borderless-table path.
  It requires at least three consecutive rows with the same segment count,
  aligned left or right column edges, compatible leading, and clear
  inter-column gaps. The default remains the stricter vector-rule strategy;
  text strategy documents its unavoidable ambiguity with aligned multicolumn
  prose
- `Page.find_tables(clip=)` now filters complete table candidates inside a
  rotation-resolved display-coordinate region without synthesizing partial
  grids. Every `Table` exposes a deterministic 0–1 ranking heuristic through
  `confidence` and `TableDiagnostics`; borderless results retain em-normalized
  alignment error, minimum gutter, and row-gap variation. The score is
  inspectable evidence for ranking, not a calibrated probability
- Text extraction now assembles transformed vertical baselines as one
  searchable line and conservatively recognizes CJK vertical writing from
  glyph geometry when the font's WMode is hidden by hayro. Vertical columns
  read top-to-bottom and right-to-left between horizontal headings and footers;
  line dictionaries report `wmode=1`. Ordinary CJK rows and rotated horizontal
  text remain `wmode=0`
- `Page.insert_text()` now accepts `fontfile=`, `fontbuffer=`, and
  `fontindex=` to subset-embed arbitrary OpenType fonts through HarfRust 0.12
  and krilla 0.8.2. Unicode CJK text remains extractable and searchable;
  multiline baselines, rotated display coordinates, color, and `overlay=` are
  preserved through the existing Form-XObject import boundary. Fonts missing
  any required glyph are rejected instead of emitting `.notdef`. RTL glyph
  shaping works, while extraction currently follows visual rather than logical
  order. A 4.5 MB Noto Sans JP source yields a 3.3 KB edited PDF for the
  regression phrase. The Windows abi3 wheel grew from 4.44 MB to 5.42 MB
  (+0.98 MB). PEP 639 metadata includes `LICENSE` and third-party
  acknowledgements in `NOTICE.md`, alongside the generated wheel SBOM

### Changed
- PyPI classifier moved from Alpha to `Development Status :: 4 - Beta`
- Release CI now installs and smoke-tests every natively runnable wheel plus the
  sdist, exercising PDF creation, extraction, rendering, and saving before
  uploading artifacts

## [0.9.0] - 2026-07-23

### Added
- Markdown conversion (first cut): `Document.to_markdown(pages=None)` and
  `Page.to_markdown()` convert extracted layout to Markdown for RAG / LLM
  preprocessing. Headings are inferred from font sizes (the size with the most
  characters is body text; larger sizes map to `#`..`####` by rank), CJK line
  wraps join *without* spaces (Japanese paragraphs stay intact), leading bullet
  characters (・• etc.) and "1." / "1)" normalize to Markdown lists, and pages
  with an `insert_ocr_text_layer` convert too. Documented limits: no bold/italic
  (no font names in spans yet), no tables, no multi-column reading order, no
  vertical writing
- AcroForm read & fill: `Document.get_form_fields()` lists fields as `{"name",
  "type", "value"}` (dotted full names, inherited FT/Ff/V resolved; types:
  text / checkbox / radio / button / combobox / listbox / signature) and
  `Document.set_form_field(name, value)` fills text/choice fields (UTF-16BE for
  non-ASCII) and buttons (state name or bool — True resolves the on-state from
  the widget appearance dictionary, widgets' /AS kept in sync). Filling sets
  /NeedAppearances so viewers render the values; appearance streams are not
  generated (documented limitation). Signature fields refuse with a pointer to
  the pyHanko recipe
- Page labels: `Document.get_page_labels()` / `set_page_labels(labels)` read and
  write the PageLabels number tree as `{"startpage", "style", "prefix",
  "firstpagenum"}` ranges (kid-split trees read recursively, written back flat;
  an empty list removes the tree), and `Page.get_label()` computes the display
  label ("iv", "A-2", …) including roman/letter styles and the spec-mandated
  startpage-0 validation
- File attachments: `Document.embfile_add(name, data, filename=, desc=)` /
  `embfile_names()` / `embfile_get(name)` / `embfile_del(name)` manage the
  EmbeddedFiles name tree (kid-split trees are read recursively and rewritten
  flat; sibling name trees under /Names are preserved). Unicode filenames and
  descriptions are stored as UF/Desc text strings; attachments survive
  `garbage=/deflate=/object_streams=` saves
- `Page.insert_ocr_text_layer(words)`: write external OCR results as an
  invisible text layer (searchable PDFs). Takes `(x0, y0, x1, y1, text, ...)`
  sequences — `get_text("words")` shapes and typical OCR API output feed in
  directly. Uses a non-embedded CID font (Identity-H + ToUnicode, ocrmypdf-style)
  with invisible render mode, so extraction and search work — CJK included, with
  no fallback-font dependency and near-zero size cost — while rendering shows
  nothing. The neutral primitive under any OCR engine (cloud APIs, Tesseract,
  the future `[ocr]` extra)
- `Document.get_pdfa_claim()`: read the XMP PDF/A declaration
  (`pdfaid:part` / `conformance`, e.g. `(2, "B")` for PDF/A-2b; PDF/A-4 yields
  an empty conformance). Explicitly a self-claim read, not validation —
  verified against typst's krilla-validated PDF/A output in the interop tests

## [0.8.0] - 2026-07-23

### Added
- `Page.insert_image(rect, filename=/stream=, keep_proportion=, overlay=)`:
  draw a JPEG (embedded as-is, no recompression) or PNG (decoded, alpha kept as
  a soft mask) into a display-space rect — the same top-left coordinate system
  as `search_for` / `get_text`, so search hits can be stamped directly. Existing
  page content is never re-encoded: drawing only appends new content streams
  (the existing stream list is wrapped once in `q`/`Q` to isolate its graphics
  state). Rotated pages take display coordinates too
- `Page.show_pdf_page(rect, src, pno=0, keep_proportion=, overlay=)`: overlay a
  page from another document as a Form XObject — text and vectors stay intact
  (extractable afterwards), fonts stay embedded. Source rotation and CropBox are
  resolved so the page lands in the rect exactly as displayed. This is the
  universal adapter for the ecosystem recipes: a one-page stamp typeset with
  typst (e.g. a Japanese watermark using the pylopdf-fonts-cjk fonts via
  `font_paths`) burns onto every page as vectors, covered by an integration test
- `Page.replace_text(search, replacement, default_char=None)`: thin wrapper over
  lopdf's `replace_partial_text` returning the replacement count. Simple-encoded
  fonts only (no CID/CJK); page attributes are baked first so inherited
  Resources work
- Annotations: `Page.annots()` reads `{"type", "rect", "contents", "uri"}` dicts
  (rect in display coordinates, rotation-aware); `Page.add_highlight_annot(rects,
  color=, opacity=, content=)` highlights one or more rects — `search_for`
  results feed in directly ("search & mark"). QuadPoints use the Acrobat zigzag
  convention AND an appearance stream (Form XObject with Multiply blend) is
  always generated, because hayro (and thus pylopdf's own rendering) only draws
  annotations that carry an /AP — pixel-verified in tests, including rotated
  pages; `Page.add_link_annot(rect, uri)` adds a borderless URI link
- `Page.insert_text(point, text, fontsize=, fontname=, color=)`: print text with
  a PDF standard-14 font (pymupdf-style abbreviations "helv" / "tiro" / "cour" /
  bold-italic variants / "symb" / "zadb"; nothing is embedded). WinAnsi range
  only — CJK input raises with a pointer to the typst + `show_pdf_page` recipe.
  `\n` makes multiple lines (1.2 × fontsize leading); text stays upright on
  rotated pages via the display-space text matrix. Headers / footers / page
  numbers / Bates stamps are a documented loop over pages
- Ecosystem interop recipes, documented in both READMEs and guarded by
  integration tests (`tests/test_interop.py`, optional `interop` dependency
  group installed in CI): typesetting and PDF/A output for new documents via
  typst (`typst.compile()` bytes feed straight into `pylopdf.open(stream=)`),
  and PAdES signatures via pyHanko (incremental signing keeps pylopdf's output
  bytes untouched as a prefix — asserted byte-for-byte). veraPDF is documented
  as the external answer for PDF/A validation

## [0.7.0] - 2026-07-23

### Added
- Positioned text extraction: `Page.get_text(option)` / `Document.get_page_text(pno,
  option)` accept pymupdf-style `"words"` (8-tuples with bbox + block/line/word
  numbers), `"blocks"`, and `"dict"` (blocks → lines → spans with bboxes, sizes,
  origins) in addition to the default `"text"`. Coordinates are top-left origin;
  vertical extents are approximated from the font size (not real font metrics)
- `Page.search_for(needle)`: case-insensitive text search returning `list[Rect]`,
  including matches across word gaps and CJK text (works even for non-embedded
  CJK fonts, since Unicode comes from the CMap machinery). Line-spanning matches
  are not detected
- `Page.get_pixmap(scale, dpi=, background=)`: renders to a `Pixmap` object with
  straight-alpha RGBA8 pixels (`samples` bytes plus `width` / `height` / `stride`
  / `n` and `tobytes()` for PNG), ready for
  `np.frombuffer(pix.samples, np.uint8).reshape(h, w, 4)`. The buffer protocol
  (zero-copy) is not implemented because `Py_buffer` only joined the stable ABI
  in Python 3.11 while our wheels are abi3-py310; `samples` costs one copy
- Interpreter warnings surface as Python warnings: font-resolution and
  image-decode failures reported by hayro during rendering or extraction are
  emitted as `PylopdfWarning` (deduplicated per operation, cleared between
  operations)
- `Page.get_images()`: extracts images drawn on the page as
  `{"width", "height", "bbox", "ext", "image"}` dicts. Images whose filter chain
  ends in DCTDecode (including `[FlateDecode, DCTDecode]`) return the original
  JPEG bytes unmodified (verified against the JPEG magic, no recompression);
  everything else (CCITT / JBIG2 / Flate / stencils) is decoded and re-encoded
  as PNG. `bbox` is the drawn position on the page (top-left origin)

### Changed
- Text extraction now runs on a hayro-based engine (`rust/src/extract.rs`): the
  interpreter collects per-glyph Unicode + positions and assembles them into
  reading order (top-to-bottom, left-to-right with word-gap detection). This
  fixes two known limits at once — content streams with `%` comments
  ([lopdf#535](https://github.com/J-F-Liu/lopdf/issues/535)) and non-embedded
  CJK fonts via predefined CMaps (90ms-RKSJ-H etc.) both extract correctly now —
  and covers invisible text (OCR layers) explicitly. Extraction no longer
  mutates the document (the inherited-attribute baking step became unnecessary).
  Vertical writing order is not reconstructed yet

## [0.6.0] - 2026-07-23

### Added
- Page views: `doc[i]` (negative indices too), iteration, and `load_page` return
  a `Page` with `number` / `parent`, `rotation` / `set_rotation`, `mediabox` /
  `cropbox` / `rect` (inheritance-resolved; `rect` is rotation-aware),
  `set_mediabox` / `set_cropbox`, `get_text`, `render`, and `render_svg`.
  Structural changes invalidate previously obtained pages (`StalePageError`),
  matching pymupdf's re-fetch semantics
- Page operations: `insert_pdf(other, from_page=, to_page=, start_at=)` merges
  ranges (negative / reversed) at an insertion position, `new_page(pno, width,
  height)` inserts a blank page, `copy_page(pno, to=)` duplicates a page, and
  repeating a page number in `select` now duplicates it instead of raising
- Table of contents: `get_toc()` / `set_toc()` with pymupdf-compatible
  `[[level, title, 1-based page], ...]` lists; non-ASCII titles are written as
  UTF-16BE; an empty list removes the outline
- Encrypted saving: `save` / `tobytes` accept `user_pw` / `owner_pw` /
  `permissions` and write AES-256 (PDF 2.0, V5/R6) output while the in-memory
  document stays unencrypted; `Permissions` IntFlag exported. The 256-bit file
  key comes from `os.urandom`
- Typed exceptions: `PdfError` (ValueError-compatible base), `PasswordError`
  (wrong/missing password), `DocumentClosedError`, `EncryptedDocumentError`,
  and `StalePageError`; existing `except ValueError` code keeps working
- `peek_metadata()`: metadata / page-count / encryption probe that does not
  parse the whole document (for scanning large collections), and
  `max_decompressed_size=` on `Document` / `open` bounding per-stream
  decompression (bomb protection)
- `render_page(pno, scale=1.0, *, dpi=None, background=None)`: resolution-based
  sizing via `dpi` (alternative to `scale`; combining both raises) and an RGB(A)
  `background` fill color (rendering stays transparent by default)
- Save options on `save` / `tobytes`: `garbage=` (prune unreferenced objects),
  `deflate=` (compress streams), and `object_streams=` (write object streams +
  cross-reference streams in PDF 1.5+ form; 13% smaller on the already-compressed
  110-page corpus document, more on object-heavy files)
- Scanned-PDF coverage in the real-world corpus: `patent-us223898.pdf`
  (Edison's electric-lamp patent, 1880, public domain) exercising CCITTFaxDecode
  images and an OCR text layer, and `wdl6812-manuscript.pdf` (World Digital
  Library illuminated manuscript, public domain) exercising DCTDecode + JBIG2Decode
  color scans with no text layer
- `ROADMAP.md`: mid-term plan (strategy, v0.6–v1.0 themes, explicit non-goals)
  based on the 2026-07 survey of lopdf, hayro, and the Python PDF ecosystem

### Changed
- Rendering now caches the parsed hayro document and invalidates it on edits,
  instead of re-serializing and re-parsing the whole document on every page
  render (hayro parses lazily, so the win is small for typical files and grows
  with document size; the cached view is also the groundwork for the planned
  hayro-based text extraction)
- Heavy operations (load, save, render, text extraction, merge, compression)
  release the GIL; concurrent rendering on two threads now scales near-linearly
  (measured 1.9x) where it previously serialized
- The content-stream comment bug behind the pdf20 empty-extraction xfail is now
  reported upstream as [lopdf#535](https://github.com/J-F-Liu/lopdf/issues/535)

### Fixed
- Built a valid Catalog and empty page tree for newly created documents, so
  saving a zero-page document no longer emits a PDF without a trailer `/Root`
- Recomputed page-tree `/Count` from reachable pages when appending, repairing
  stale counts in input PDFs instead of propagating them to merged output
- Validated complete metadata updates before applying them, preventing partial
  changes when a later key or value is invalid
- Made `validate-pyproject` UTF-8-safe on Windows, enabled complete dependency
  validation, and added the metadata check to CI
- Prevented object-ID collisions when inserting real-world PDFs into an empty
  document, including the empty-source edge case
- Rejected cyclic inherited page parents instead of hanging indefinitely
- Bounded PNG rendering to finite positive scales, 65,535 pixels per side, and
  64 million total pixels to avoid unbounded allocations
- Decoded metadata with the PDF-standard PDFDocEncoding mapping
- Kept `needs_pass` false for PDFs whose empty user password requires no
  authentication, regardless of the supplied `password` argument
- Enforced closed/encrypted document checks for empty `delete_pages([])` and
  `select([])` calls

## [0.5.0] - 2026-07-22

### Added
- Encrypted PDF reading: `password` argument on `Document`/`open`, `needs_pass` /
  `is_encrypted` properties, and pymupdf-compatible `authenticate(password)`
  (0=failed / 1=not needed / 2=user / 4=owner / 6=both). Supports RC4-40/128,
  AES-128, and AES-256 (R6); PDFs with an empty user password keep opening
  transparently. Operating on a still-encrypted document now raises a clear
  ValueError instead of silently appearing to have 0 pages
- CJK fallback fonts for rendering: `Document.set_fallback_font(font, kind, index)`
  supplies a TTF/OTF/TTC for non-embedded CID fonts (detected via CIDSystemInfo or
  BaseFont name; Mincho-like names pick the "serif" slot). The new optional extra
  `pylopdf[cjk]` installs `pylopdf-fonts-cjk` (Noto Sans/Serif JP, SIL OFL 1.1,
  built from `fonts/pylopdf-fonts-cjk/` in this repo) which is auto-detected at
  render time, so non-embedded Japanese PDFs render out of the box
- Real-world PDF regression test suite (`tests/test_real_world.py`) with a vendored
  redistributable corpus (`tests/assets/real_world/`, ~1.4 MB) covering PDF 1.5/1.7/2.0,
  AcroForm, CJK embedded CID fonts, and a 110-page document; each file's source and
  license are documented in the corpus README, and known lopdf limits are tracked via
  strict xfail
- Encrypted-PDF test fixtures (`tests/assets/encrypted/`, regenerable via `generate.py`)

### Fixed
- Corrected the recorded root cause of the empty text extraction on the PDF 2.0
  sample: lopdf fails on `%` comments inside content streams (fonts without
  /Encoding decode fine via the StandardEncoding fallback)

## [0.4.1] - 2026-07-22

### Fixed
- Removed the invalid `Topic :: Text Processing :: Markup :: PDF` classifier that
  caused PyPI to reject the 0.4.0 upload; added `Typing :: Typed`
- Added a `validate-pyproject` pre-commit hook to catch invalid metadata earlier

## [0.4.0] - 2026-07-22

### Added
- `Document.select(page_numbers)` — keep/reorder pages (pymupdf-compatible)
- CI workflow: rustfmt / clippy / ruff / mypy / pytest on Linux, macOS, and Windows
- Release workflow: abi3 wheels for manylinux (x86_64, aarch64), macOS (arm64, x86_64),
  and Windows (x64), published to PyPI via Trusted Publishing
- English README (`README.md`); Japanese version moved to `README.ja.md`

## [0.3.0] - 2026-07-22

### Added
- Page rendering via [hayro](https://github.com/LaurenzV/hayro) 0.7:
  `Document.render_page(pno, scale)` (PNG) and `Document.render_page_svg(pno)` (SVG),
  with the standard-14 font set embedded (`embed-fonts`)

## [0.2.0] - 2026-07-22

### Added
- Editing core built on [lopdf](https://github.com/J-F-Liu/lopdf) 0.44:
  open/save (path & bytes), page count, metadata read/write (UTF-16BE aware),
  page deletion, text extraction, and document merging
- pymupdf-style Python API (`Document`, `open()`) with type stubs and `py.typed`
- Page-attribute inheritance (Resources, MediaBox, CropBox, Rotate) is resolved
  and baked into pages during merge and text extraction

### Changed
- Dependencies modernized: lopdf 0.33 → 0.44, PyO3 0.22 → 0.29 (abi3-py310),
  maturin 1.14, Rust edition 2024, requires-python >= 3.10
