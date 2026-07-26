---
title: Security
description: Supported versions, private vulnerability reporting and guidance for handling untrusted PDFs with pylopdf.
---

# Security

Only the latest release on PyPI receives security fixes.

## Report a vulnerability { #report-a-vulnerability }

Report vulnerabilities privately through
[GitHub Security Advisories](https://github.com/yhay81/pylopdf/security/advisories/new).
Do not open a public issue. You should receive an initial response within one
week.

## Handle untrusted PDFs { #untrusted-pdfs }

pylopdf is written in Rust and has no mandatory Python dependencies, but
hostile PDF input remains inherently risky.

!!! warning "Use a complete resource policy"
    Pass `limits=pylopdf.DocumentLimits.web()` to `pylopdf.open()`. The
    preset is a conservative starting point for user uploads in memory-bounded
    web and queue workers.

```python
import pylopdf

try:
    with pylopdf.open(
        "upload.pdf",
        limits=pylopdf.DocumentLimits.web(),
    ) as doc:
        facts = doc.complexity
        preview = doc[0].get_pixmap(dpi=144)
except pylopdf.LimitError as error:
    reject_upload(error.code)
```

The web profile currently applies these independent budgets:

| Resource | Limit |
|---|---:|
| Input file | 10 MiB |
| Pages | 200 |
| Indirect objects | 100,000 |
| Any decoded stream, including image RGBA estimates | 64 MiB |
| One page-content stream | 10 MiB |
| Cumulative decoded or estimated stream bytes | 128 MiB |
| Direct array/dictionary nesting | 64 |
| Cumulative UTF-8 glyph payload across interpreted pages | 1 MiB |

Create `DocumentLimits(...)` directly when the workload needs different
budgets. Every non-`None` value must be a positive integer.
`max_decompressed_size=` remains a compatible shorthand for its one
per-stream budget and cannot be combined with `limits=`.

`LimitError` is a `PdfError` subclass. Its stable `code` is one of
`file_size`, `page_count`, `object_count`, `object_depth`,
`decompressed_size`, `page_content_size`, `total_decompressed_size`,
`text_size`, `embedded_file_size`, `xmp_metadata_size`, `render_output_size`,
`markdown_output_size`, `svg_output_size`, `replacement_input_size`,
`replacement_output_size`, `pdf_output_size`, or
`decompression_unverifiable`.
The same code is also
`error.args[0]`. A filter chain that cannot be bounded safely is rejected
instead of being decoded optimistically.

`doc.complexity` reports page, object, and stream counts, encoded stream bytes,
and maximum direct object depth. It neither decodes streams nor invokes the
renderer, so it is suitable for routing work before extraction. Structural and
decompression budgets validate the opened source; reopen generated output with
the same policy when it must cross another trust boundary.

Lenient opening performs one bounded repair: it may replace an incorrect final
`startxref` only when an intact classic xref table exists in the same final
revision and a complete parse succeeds under the original limits. It does not
scan object headers, repair xref streams, or roll back to an earlier revision.
The event emits `PylopdfWarning`, sets `doc.is_repaired` (and the metadata
probe's `repaired` key), and saving rewrites normalized xref data.

- Rendering is capped at 64 megapixels per page.
- `Document.tobytes()` defaults to a 512 MiB serialized-output limit across
  normal, object/xref-stream, and encrypted output. Its Rust writer refuses
  the write crossing the boundary before Python `bytes` conversion;
  `max_size=None` explicitly opts out. `save()` streams to a file and is not
  governed by this in-memory output budget. Save options such as `garbage`,
  `deflate`, and `object_streams` retain their documented mutation semantics
  even when later serialization is refused.
- `render_page_svg()` and `Page.render_svg()` default to a 64 MiB UTF-8 output
  limit and reject over-limit output before PyO3 creates the Python string;
  `max_size=None` explicitly opts out. hayro-svg 0.7 materializes one internal
  Rust string before pylopdf can enforce this boundary.
- Drawing insertions preflight raw page `/Contents` arrays and reference chains
  before cache invalidation, input decoding, or dependent object creation. Raw
  arrays stop at 4,096 entries, chains at 32 levels, and the final array at
  4,096 stream references including the one-time `q`/`Q` isolation pair.
  Failures do not mutate the document.
- `Page.replace_text()` caps aggregate search/replacement/fallback input at
  4,096 UTF-8 bytes and defaults decoded page content, font encoding data,
  replacement growth, and final stream output to 64 MiB. It prepares a
  page-owned stream before committing, so copied pages cannot edit shared
  content and no-match/error calls preserve the document and caches.
  `max_size=None` explicitly opts out for trusted input.
- `delete_pages()`, `select()`, and `insert_pdf()` accept at most 4,096 page
  entries per call in both Python and Rust. Iterable reads stop at item 4,097
  before graph mutation. Empty deletion preserves caches, generation, and
  existing `Page` views.
- `Page.get_images()` rejects partial results above 4,096 placements,
  64,000,000 cumulative source pixels, or 64 MiB of returned payloads per page.
  Flate-wrapped JPEG passthrough stops decompression at the remaining budget.
- `Document.embfile_get()` defaults to a 64 MiB decoded-size limit applied to
  every filter layer. Raise `max_size=` for a known large attachment;
  `max_size=None` explicitly accepts unbounded materialization. Attachment name
  trees are rejected above 4,096 entries/nodes, 32 levels, or 1 MiB of encoded
  or decoded names. Adds cap aggregate key/filename/description input at 1 MiB.
  Edits validate inline FileSpecs before cloning above 4,096 direct objects,
  32 levels, or 1 MiB of direct string/name/stream data, and preflight the
  Catalog write target instead of cloning the complete document for rollback.
- `Document.get_pdfa_claim()` defaults to a 1 MiB XMP decoded-size limit applied
  to every filter layer. Raise `max_size=` for a known large packet;
  `max_size=None` explicitly accepts unbounded materialization.
- `Page.insert_ocr_text_layer()` stops iterable materialization above 4,096
  non-empty words or 1 MiB of aggregate UTF-8 text. Direct core calls enforce
  the same limits, CID assignment stops before 65,535 distinct characters, and
  all input-derived buffers are prepared before PDF mutation.
- Page-label number-tree reads reject partial output above 4,096 entries/nodes,
  32 levels, or 1 MiB of encoded or decoded style/prefix text. Reference cycles
  are visited once; writes enforce the same entry/text boundary.
- AcroForm field-tree reads reject partial output above 4,096 entries/nodes,
  8,192 edges, 64 levels, 1 MiB of encoded, decoded, or returned names/values,
  or 4,096 choice-value items. Reference cycles are visited once, inherited
  values are charged per returned leaf, and fills enforce the same tree plus
  1 MiB input-value boundary atomically.
- AcroForm button fields reject more than 4,096 widgets, 8,192 normal-appearance
  state entries, 4,096 unique returned state names, or 1 MiB of encoded/returned
  state-name text. Fills budget missing `Off` and on-state keys before mutation.
- Annotation and link reads reject partial output above 4,096 `/Annots` entries
  or 1 MiB of aggregate encoded/returned metadata text per call. Adds enforce
  the same page count, 1 MiB generated subtype plus Contents/URI input, and
  4,096 highlight rectangles before creating dependent objects or invalidating
  caches.
- Named-destination lookup visits reference cycles once and rejects traversal
  above 4,096 entries/nodes, 8,192 edges, 32 levels, or 1 MiB of key bytes
  instead of silently reporting a truncated tree as unresolved.
  `Page.get_links()` builds one borrowed index per call rather than traversing
  the tree again for every named link.
- TOC reads use an iterative outline walk, visit reference cycles once, release
  the GIL, and reject partial output above 4,096 nodes/entries, 8,192 edges,
  64 levels, 32 destination indirections, or 1 MiB of source/returned text.
  Writes enforce compatible entry, depth, and title-text limits before mutation.
- `Document.metadata` decodes only the eight standard Info fields and rejects
  aggregate source or returned text above 1 MiB; custom entries do not become
  Python output. `peek_metadata()` caps returned standard text too. Writes
  preflight 1 MiB aggregate source/encoded text and apply atomically.
- Embedded JavaScript is never executed; it is unsupported by design.
- `render_pages()` accepts at most 4,096 page entries and defaults to a 512 MiB
  cumulative encoded-PNG limit. Completed parallel results share one atomic
  budget and failures return no partial list; `max_size=None` explicitly opts
  out. Worker admission separately caps estimated live raster/conversion
  buffers, so do not add unbounded application-level parallelism around it.
- `Document.to_markdown()` accepts at most 4,096 page entries and defaults to a
  64 MiB cumulative UTF-8 output limit. It keeps only one page's interpreted
  layout, tables, and words at a time across a heading-count pass and a render
  pass. Each table receives the remaining aggregate budget before page output
  is assembled. Headings, paragraphs, lists, and tables are charged as retained
  entries, and the final page uses linear assembly after the complete size has
  been admitted. Direct `Table.to_markdown()` calls default to the same limit
  and preflight exact escaped UTF-8 size, including merged-cell expansion.
  Over-limit conversions return no partial string; `max_size=None` explicitly
  opts out.
- Enforce CPU deadlines in the Worker, process, or container host. Resource
  budgets prevent documented allocations and output growth; they do not
  interrupt an in-progress parser or interpreter by wall-clock time.
- Run batch processing of untrusted files in a sandbox or container when
  possible. Native and Pyodide CI share the same hostile-input regression
  contract, and scheduled Atheris fuzzing seeds malformed xrefs, cycles,
  deep objects, broken streams, and compression bombs.

## Dependency auditing { #dependency-auditing }

CI runs `cargo audit` against the Rust dependency tree and the RustSec advisory
database on every push.

The repository copy of this policy is
[`SECURITY.md`](https://github.com/yhay81/pylopdf/blob/main/SECURITY.md).
