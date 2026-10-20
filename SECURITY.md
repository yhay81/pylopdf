# Security Policy

## Supported Versions

Only the latest release on PyPI receives security fixes.

## Reporting a Vulnerability

Please report vulnerabilities privately via GitHub Security Advisories
("Report a vulnerability" on the repository's Security tab):
<https://github.com/yhay81/pylopdf/security/advisories/new>

Please do not open public issues for security reports. You should receive an
initial response within a week.

## Handling untrusted PDFs

pylopdf is written in Rust (lopdf, hayro, krilla, and HarfRust) and has no
mandatory Python dependencies, but parsing hostile PDF input is inherently
risky. When processing untrusted files:

- Pass `limits=pylopdf.DocumentLimits.web()` to `pylopdf.open()`. The profile
  bounds input bytes, pages, indirect objects, direct object nesting, individual
  and cumulative decompression, page-content decompression, and interpreted
  Unicode text. It also caps the complete PDF snapshot passed to rendering and
  extraction at 64 MiB and cumulative positioned text at 65,536 glyph records,
  including reserialization after edits or decryption. `LimitError.code`
  identifies the rejected resource.
- Inspect `doc.complexity` before heavy work. It reports page, object, and stream
  counts, encoded stream bytes, and direct object depth without decoding streams
  or rendering.
- `max_decompressed_size=` remains a compatible per-stream shorthand, but the
  complete policy is preferred for user uploads.
- Rendering is bounded to 64 megapixels per page. Embedded JavaScript is never
  executed (unsupported by design).
- `DocumentLimits.max_interpretation_size` rejects retained or reserialized
  rendering/extraction input before hayro parses it. The stable code is
  `interpretation_size`; `None` is the compatible unbounded default.
- `DocumentLimits.max_text_glyphs` rejects positioned-text collection before
  cached glyph records or Python layout objects can amplify one-byte text.
  The stable code is `text_glyph_count`; normal and table interpretation of one
  page share a single cumulative admission.
- With `DocumentLimits.max_text_size` configured, plain-text extraction
  preflights exact output and caps one call at twice the glyph-payload budget,
  across every internal 4,096-entry chunk. Inferred spaces plus line endings
  cannot outnumber non-empty glyphs, so repeated-page amplification is rejected
  with `text_size`.
- Before hayro interprets a page, pylopdf decodes the content streams that
  page can reach (its `/Contents`, Form XObjects, annotation and widget
  appearances including every state of a check box or radio button, soft-mask
  groups, tiling patterns, and Type 3 glyph procedures) and refuses it with
  `content_nesting_depth` when arrays or dictionaries nest deeper than 64
  levels, a stream object is embedded in content, or inline image boundaries
  are too ambiguous to check. hayro 0.7 parsed such nesting by unbounded
  recursion, so hundreds of thousands of nested `[` used to overflow the stack
  and end the process; hayro 0.8 stops at 64 levels and silently leaves out the
  rest, which the check turns into a refusal. The bound is fixed, applies with
  or without limits, and stays page-granular: other pages remain usable.
- The same check measures the colour spaces, shadings, and functions that the
  page's resources reach (Indexed, Pattern, Separation, DeviceN, ICCBased, and
  stitching and Type 4 functions, found through `/ColorSpace` and `/Shading`
  entries, shading patterns, images, soft-mask groups, and `/TR` transfer
  functions) and refuses them with `resource_nesting_depth` when a chain is
  deeper than 16 levels or a Type 4 function nests procedures deeper than 64.
  hayro 0.7 and 0.8 build them by unbounded recursion, so about 20,000 chained
  objects or 300,000 nested braces used to overflow the stack and end the
  process. The bounds stop stack overflows, not CPU use.
- hayro 0.8 reads no array or dictionary nested more than 64 levels deep and
  treats the whole object as absent. A page whose dictionary, with the
  attributes it inherits, nests deeper is refused with `object_depth` whenever
  an operation interprets it, and other pages remain usable. A trailer,
  catalog, or root page-tree node nested that deeply no longer keeps the
  document from rendering, while other objects nested that deeply read as
  absent. `DocumentLimits.web()` already refuses such documents when they open.
- hayro 0.8 panics on some malformed colour data, such as a tint transform that
  returns the wrong number of components
  ([LaurenzV/hayro#1546](https://github.com/LaurenzV/hayro/issues/1546)).
  Every operation that lets hayro interpret a page catches the panic and raises
  `PdfError` instead of PyO3's `PanicException`, a `BaseException`, and drops
  the renderer state as an edit does, so the document stays usable. Rust still
  prints the panic message to standard error.
- Page batches cover every page by default. Explicit page sequences stop at the
  first entry beyond the larger of the page count and 4,096 with
  `page_entry_count`, so repetition never costs more than one complete document
  pass; direct core calls repeat the check before mutation.
- `Page.insert_text()` and `Page.insert_textbox()` default to 1 MiB of UTF-8
  and 4,096 physical or final wrapped lines. Python rejects physical-line
  amplification before splitting or font resolution, and Rust stops wrapped
  layout before mutation. `max_text_size=None` opts trusted insertion input out;
  refusals use `text_input_size` or `text_line_count`. AcroForm text and choice
  appearances retain the 4,096-line layout cap.
- `Page.replace_text()` caps aggregate search/replacement/fallback input at
  4,096 UTF-8 bytes and counts it incrementally before PyO3 copying rather than
  allocating complete encoded copies. Its decoded content, encoding data,
  growth, and final stream share the configured output budget.
- Open, authenticate, fast metadata probe, and AES-256 output passwords stop at
  127 UTF-8 bytes before PyO3 copying or password-KDF work. Refusals use
  `password_input_size`; encryption refusal precedes document mutation and
  output creation.
- `Page.get_images()` rejects partial results above 4,096 placements,
  64,000,000 cumulative source pixels, or 64 MiB of returned payloads per page;
  Flate-wrapped JPEG passthrough is decompressed only to the remaining budget.
- `Attachment.read()` bounds every attachment decoding layer to 64 MiB by
  default and raises `LimitError` with code `embedded_file_size`. Raise
  `max_size=` for a known large attachment; `None` is an explicit unbounded
  opt-out. Attachment name trees are also rejected above 4,096 entries/nodes,
  32 levels, or 1 MiB of encoded or decoded names. Caller lookup/deletion names
  and aggregate add-time name/filename/description text stop at 1 MiB before
  tree traversal or attachment-data copying, using `embedded_file_input_size`.
- `Document.get_pdfa_claim()` bounds every XMP metadata decoding layer to 1 MiB
  by default and raises `LimitError` with code `xmp_metadata_size`. Raise
  `max_size=` for a known large packet; `None` explicitly accepts unbounded
  materialization.
- Page-label number-tree reads reject partial output above 4,096 entries/nodes,
  32 levels, or 1 MiB of encoded or decoded style/prefix text. Reference cycles
  are visited once, and writes enforce the same entry/text boundary before
  PyO3 copying with `page_label_input_size`.
- AcroForm field-tree reads reject partial output above 4,096 entries/nodes,
  8,192 edges, 64 levels, 1 MiB of encoded, decoded, or returned names/values,
  or 4,096 choice-value items. Reference cycles are visited once, inherited
  values are charged for every returned leaf, and fills enforce the same tree
  plus 1 MiB caller name/value input atomically. Caller input is rejected before
  font discovery, button lookup, or file reads with `form_field_input_size`.
- AcroForm button fields reject more than 4,096 widgets, 8,192 normal-appearance
  state entries, 4,096 unique returned state names, or 1 MiB of encoded/returned
  state-name text. Fills budget missing `Off` and on-state keys before mutation.
- Annotation and link reads reject partial output above 4,096 `/Annots` entries
  or 1 MiB of aggregate encoded/returned metadata text per call. Adds enforce
  the same page count, 1 MiB generated subtype plus Contents/URI input, and
  4,096 highlight rectangles before adding dependent objects or invalidating
  caches. Caller text is rejected before PyO3 copying or rectangle iteration
  with `annotation_input_size`; highlight iteration stops at item 4,097.
- Named-destination lookup visits reference cycles once and rejects traversal
  above 65,536 entries/nodes or 131,072 edges with `named_destination_count`,
  above 16 MiB of key bytes with `named_destination_text_size`, or above 32
  levels, instead of silently reporting a truncated tree as unresolved.
  `get_outline()`, `Page.get_links()`, link import in `insert_pdf()`, and the
  rewrite of the name tree and legacy `/Dests` during page deletion and
  selection share these limits, so every well-formed tree that resolves can
  also be cleaned and imported. The rewrite copies retained destination values
  within 1,048,576 direct objects (16 per entry) and 16 MiB of text. A
  looked-up destination name costs the budget of the structure being walked:
  TOC text for an outline, key text for a name tree, or one page's 1 MiB of
  link metadata. `Page.get_links()` indexes the tree on its first named
  lookup and reuses that index and the page index until the next edit, so
  reading every page stays linear. The retained index holds each distinct key
  once within the same entry and key-text limits, and a refused tree is never
  cached.
- TOC reads use an iterative outline walk, visit reference cycles once, release
  the GIL, and reject partial output above 65,536 nodes/entries or 131,072
  edges with `toc_entry_count`, above 16 MiB of source/returned text with
  `toc_text_size`, or above 64 levels or 32 destination indirections. Writes
  enforce the entry, depth, and source/encoded-title boundaries before PyO3
  copying and mutation with `toc_entry_count` and `toc_input_size`, and page
  deletion/selection shares the outline limits.
- `Document.metadata` decodes only the eight standard Info fields and rejects
  aggregate source or returned text above 1 MiB; custom dictionary entries are
  not materialized into Python output. `peek_metadata()` caps returned standard
  text too. Writes preflight 1 MiB aggregate source/encoded text before PyO3
  copying with `metadata_input_size` and apply the complete update atomically.
- Prefer running batch processing of untrusted documents in a sandboxed or
  containerized environment, and enforce CPU deadlines in the host. pylopdf
  resource budgets do not provide in-process time cancellation.

## Dependency auditing

CI runs `cargo audit` against the Rust dependency tree (RustSec advisory
database) on every push.
