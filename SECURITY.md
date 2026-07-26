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
  Unicode text. `LimitError.code` identifies the rejected resource.
- Inspect `doc.complexity` before heavy work. It reports page, object, and stream
  counts, encoded stream bytes, and direct object depth without decoding streams
  or rendering.
- `max_decompressed_size=` remains a compatible per-stream shorthand, but the
  complete policy is preferred for user uploads.
- Rendering is bounded to 64 megapixels per page. Embedded JavaScript is never
  executed (unsupported by design).
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
  32 levels, or 1 MiB of encoded or decoded names.
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
  plus 1 MiB input-value boundary atomically.
- AcroForm button fields reject more than 4,096 widgets, 8,192 normal-appearance
  state entries, 4,096 unique returned state names, or 1 MiB of encoded/returned
  state-name text. Fills budget missing `Off` and on-state keys before mutation.
- Annotation and link reads reject partial output above 4,096 `/Annots` entries
  or 1 MiB of aggregate encoded/returned metadata text per call. Adds enforce
  the same page count, 1 MiB generated subtype plus Contents/URI input, and
  4,096 highlight rectangles before adding dependent objects or invalidating
  caches.
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
