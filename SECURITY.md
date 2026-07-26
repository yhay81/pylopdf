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
  preflights exact output and caps one private batch at twice the glyph-payload
  budget. Inferred spaces plus line endings cannot outnumber non-empty glyphs.
  The batch accepts at most 4,096 page entries and rejects repeated-page
  amplification with `text_size`.
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
- `Document.embfile_get()` bounds every attachment decoding layer to 64 MiB by
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
  are visited once, and writes enforce the same entry/text boundary.
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
  above 4,096 entries/nodes, 8,192 edges, 32 levels, or 1 MiB of key bytes
  instead of silently reporting a truncated tree as unresolved.
  `Page.get_links()` builds one borrowed index per call rather than repeating
  the bounded traversal for every named link.
- TOC reads use an iterative outline walk, visit reference cycles once, release
  the GIL, and reject partial output above 4,096 nodes/entries, 8,192 edges,
  64 levels, 32 destination indirections, or 1 MiB of source/returned text.
  Writes enforce the entry, depth, and source/encoded-title boundaries before
  mutation.
- `Document.metadata` decodes only the eight standard Info fields and rejects
  aggregate source or returned text above 1 MiB; custom dictionary entries are
  not materialized into Python output. `peek_metadata()` caps returned standard
  text too. Writes preflight 1 MiB aggregate source/encoded text and apply the
  complete update atomically.
- Prefer running batch processing of untrusted documents in a sandboxed or
  containerized environment, and enforce CPU deadlines in the host. pylopdf
  resource budgets do not provide in-process time cancellation.

## Dependency auditing

CI runs `cargo audit` against the Rust dependency tree (RustSec advisory
database) on every push.
