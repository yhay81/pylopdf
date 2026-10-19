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
| Complete PDF snapshot passed to rendering/extraction | 64 MiB |
| Cumulative positioned glyph records across interpreted pages | 65,536 |

Create `DocumentLimits(...)` directly when the workload needs different
budgets. Every non-`None` value must be a positive integer.
`max_decompressed_size=` remains a compatible shorthand for its one
per-stream budget and cannot be combined with `limits=`.

All public page indexing uses one iterative page-tree walker, including page
count and lookup, complexity, render snapshots, TOC and links, annotations,
Form imports, and page-structure edits. It rejects reused Page/Pages objects
and cycles, indirect `/Kids` chains over 32 references, internal trees over 256
levels, and edge counts above the indirect-object count. `max_pages` traversal
stops at the first over-limit page. Opening without a page policy remains
compatible and defers these page-tree checks until the first operation that
indexes pages. Page counts and lookups reuse one walk's page index, so
touching every page stays linear. Rotation, page-box, drawing, and annotation
edits keep the index because they cannot change the page tree: each adds
objects only at unused object numbers and keeps every entry the walk read, or
drops the index when it cannot show that. Every other edit drops it, and the
next access rebuilds it. A failed walk is never cached, so a malformed tree
raises on every access.

Page batches validate entries against one page-count snapshot. `pages=None`
and complete `insert_pdf()` ranges cover every page whatever the page count.
Explicit sequences may repeat pages, but materialization stops at the first
entry beyond the larger of the page count and 4,096 with `page_entry_count`, so
repetition never costs more than one complete document pass. Text extraction
and rendering replace call-scoped hayro caches every 4,096 entries, while
`max_size`, `max_text_size`, and the other output budgets bound the complete
call. Direct core calls repeat the entry check, and core page imports reject
repeated source pages before mutation.

`LimitError` is a `PdfError` subclass. Its stable `code` is one of
`file_size`, `page_count`, `object_count`, `object_depth`,
`decompressed_size`, `page_content_size`, `total_decompressed_size`,
`text_size`, `text_glyph_count`, `interpretation_size`, `embedded_file_size`,
`embedded_file_input_size`, `form_field_input_size`, `form_content_size`,
`annotation_input_size`, `metadata_input_size`, `toc_input_size`,
`toc_entry_count`, `toc_text_size`, `named_destination_count`,
`named_destination_text_size`, `page_entry_count`,
`page_label_input_size`, `xmp_metadata_size`, `render_output_size`,
`markdown_output_size`, `svg_output_size`, `replacement_input_size`,
`replacement_output_size`, `pdf_output_size`, `image_input_size`,
`image_pixel_count`, `font_input_size`, `text_input_size`, `text_line_count`,
`search_input_size`, `search_hit_count`, `password_input_size`, `pixmap_output_size`,
`ocr_model_size`, `ocr_dictionary_entries`, `stream_filter_count`,
`decompression_unverifiable`, `recovery_scan_size`, `content_nesting_depth`, or
`resource_nesting_depth`. The same code is also
`error.args[0]`. A filter chain that cannot be bounded safely is rejected
instead of being decoded optimistically. Pylopdf-owned bounded lopdf decode
paths also reject more than 16 sequential `/Filter` layers before materializing
the filter list.

`doc.complexity` reports page, object, and stream counts, encoded stream bytes,
and maximum direct object depth. It neither decodes streams nor invokes the
renderer, so it is suitable for routing work before extraction. Structural and
decompression budgets validate the opened source; reopen generated output with
the same policy when it must cross another trust boundary. Direct-depth
validation and complexity inspection grow their iterative traversal stack
fallibly, so allocation refusal raises `PdfError` without partial facts.

`max_interpretation_size` applies when hayro first consumes retained input and
whenever pylopdf must serialize current state after editing or decryption, or
to prepare a rendering copy, for example with synthesized text-markup
appearances. The bounded writer refuses the crossing write and
does not install a partial renderer/extractor cache. Its default is `None` for
compatibility; `DocumentLimits.web()` sets 64 MiB.

When `max_text_size` is configured, plain-text extraction preflights exact
assembled UTF-8 size and caps one extraction call at twice that glyph-payload
budget. Each non-empty glyph contributes at least one payload byte, while
inferred gaps plus line endings cannot outnumber glyphs. That output bound
covers the complete call rather than one internal 4,096-entry chunk, and
explicit sequences accept at most the larger of the page count and 4,096
entries, so repeated page numbers cannot bypass the policy. Refusals retain
`text_size`; the compatible default remains `None`.

`max_text_glyphs` bounds the records retained before line assembly and therefore
also bounds the number of blocks, lines, spans, words, and `rawdict` glyph
entries that structured text can materialize. Text and table interpretations
of one page share one cumulative admission, and a rejected page consumes no
budget. Its compatible default is `None`; `DocumentLimits.web()` sets 65,536.

The `get_text("rawdict")` option (since 0.14.0) uses the same interpreted glyph
cache and cumulative `max_text_size`/`max_text_glyphs` admissions as other
extraction modes; requesting several representations does not charge the
same cached page again. One entry may contain a multi-character Unicode
mapping, so the glyph count and UTF-8 payload remain separate budgets.
Rust raw-layout buffers and glyph-text copies grow fallibly; native allocation
refusal raises `PdfError` without returning a partial layout. The policy bounds
retained glyph data, not the exact byte size of Python dictionaries or a
caller's later JSON/HTML serialization. Bound that output separately when
exporting raw layout from a worker.

Lenient opening uses lopdf's cross-reference recovery. A `startxref`,
`/Prev`, or `/XRefStm` offset that misses by at most 64 bytes moves to the
nearby `xref` keyword. When standard resolution still fails, lopdf rebuilds the
table from line-start object headers and the newest classic `trailer` whose
`/Root` exists; objects from later revisions win, but objects from damaged
regions may be missing. Damaged xref-stream files without a classic trailer
are rejected. Either recovery emits `PylopdfWarning`, sets `doc.is_repaired`
(and the metadata probe's `repaired` key), and rendering and extraction use the
recovered object graph that saving writes with normalized xref data. Callers
that must not accept recovered input can reject `is_repaired` documents.
Lenient tolerances that keep the declared structure, such as 19-byte xref
entries or a stream `/Length` corrected at an unambiguous object boundary, are
not reported. `DocumentLimits` apply to recovered documents unchanged.

lopdf's reconstruction rescans the rest of the input after every `stream`
keyword without a later `endstream`. Before each lenient open or metadata
probe, pylopdf estimates that work in linear time and refuses input above 64
bytes per input byte plus 64 MiB with `recovery_scan_size`, so hostile input
cannot make recovery quadratic. Real damaged files carry at most one truncated
stream in that position.

hayro 0.7 read arrays and dictionaries in content streams by unbounded
recursion, so one stream that opens hundreds of thousands of `[` used to
overflow the stack and end the Python process instead of raising. hayro 0.8
stops at 64 levels and silently leaves out the deeper operands. Before text
extraction, layout, tables, search, drawings, image reads, rendering (PNG,
pixmap, SVG), Markdown conversion, or image compression interprets a page,
pylopdf decodes the content streams that page can reach: its `/Contents`, the
Form XObjects in its resources, annotation and widget appearances (every state
of a check box or radio button, of which hayro draws the one that `/AS`
selects), soft-mask groups, tiling patterns, and Type 3 glyph procedures. It
refuses the page with `content_nesting_depth` when they nest arrays or
dictionaries deeper than 64 levels, embed a stream object, or hide an inline
image boundary it cannot check. The bound is fixed, applies with or without
`DocumentLimits`, and leaves other pages usable. Each page is checked once per
document snapshot, and an edit clears the result. Entries that a page's
resource dictionaries list count even when no operator draws them, because the
check does not work out which names the content uses.

hayro builds colour spaces, shadings, and functions by the same kind of
unbounded recursion (hayro 0.7 and 0.8 alike): an Indexed or Pattern space from
its base, a Separation or DeviceN space from its alternate space and tint
transform, an ICCBased space from the `/Alternate` of a profile it cannot parse,
a stitching function from every entry of `/Functions`, and a Type 4 function by
parsing its `{ }` procedures recursively. About 20,000 indirect objects that
name each other, or 300,000 nested braces, used to overflow the stack and end
the process. Before interpreting a page, pylopdf measures what its resource
dictionaries' `/ColorSpace` and `/Shading` entries, shading patterns, image
XObjects, soft-mask groups, and the `/TR` and `/TR2` transfer functions of its
graphics states and soft masks reach, and refuses the page with
`resource_nesting_depth` when a chain of colour spaces and functions is deeper
than 16 levels or a Type 4 function nests procedures deeper than 64. A chain
counts its colour space arrays, functions, and profile streams, hayro's own cut
lets a self-reference or a short ring still render, a group of more than 16
objects that all reach one another is refused, and shared sub-graphs are
measured once, so the check reads each object once however many entries name it.
The bounds are fixed, apply with or without `DocumentLimits`, and leave other
pages usable. They limit stack use, not time: hayro can need exponential CPU for
graphs that stay under them, such as a function named twice per level or nested
Separation spaces, so keep enforcing CPU deadlines in the host.

hayro 0.8 reads no array or dictionary nested more than 64 levels deep,
counting the object that holds it, and treats the whole object as absent;
hayro 0.7 read such objects by recursion, and lopdf reads them up to 100 levels
deep. A page whose dictionary, with the attributes it inherits from the page
tree, nests deeper is refused with `object_depth` whenever an operation
interprets it, and the other pages stay usable. A trailer, catalog, or root
page-tree node nested that deeply no longer keeps the document from rendering:
rendering and extraction read a copy without the entries that nest too deeply,
while the editable and saved document keep them. Any other object nested that
deeply reads as absent, as it does in hayro. `DocumentLimits.web()` already
refuses such documents with `object_depth` when they open.

hayro 0.8 panics on some malformed colour data, such as a Separation or DeviceN
tint transform that returns the wrong number of components or a shading in a
Pattern colour space
([LaurenzV/hayro#1546](https://github.com/LaurenzV/hayro/issues/1546)); hayro
0.7 drew the same pages. Every operation that lets hayro interpret a page
catches such a panic and raises `PdfError` instead of PyO3's `PanicException`,
which derives from `BaseException` and would escape `except Exception`. The
renderer state is dropped as an edit drops it, so later calls start over and
the document stays usable. Rust still prints the panic message to standard
error.

- Rendering is capped at 64 megapixels per page.
- Page deletion and selection clean navigation to excluded pages before
  pruning. AcroForm widget `/P` references can still retain excluded page
  objects. These operations are not secure redaction: form references,
  arbitrary custom references, attachments, metadata, and other copies of the
  content require their own inspection and removal.
- `Document.render_page()`, `Page.render()`, and `Pixmap.tobytes()` default to
  a 64 MiB encoded-PNG output boundary. Their writers refuse the crossing
  write before returning Python `bytes`; `max_size=None` explicitly opts out.
  Render refusals use `render_output_size`, while direct Pixmap encoding uses
  `pixmap_output_size`.
- `Document.tobytes()` defaults to a 512 MiB serialized-output limit across
  normal, object/xref-stream, and encrypted output. Its Rust writer refuses
  the write crossing the boundary before Python `bytes` conversion;
  `max_size=None` explicitly opts out. `save()` streams to a securely created
  sibling in the target directory and atomically replaces the requested path
  only after a complete write, so serialization or replacement failures
  preserve an existing file. It is not governed by the in-memory output
  budget. Save options such as `garbage`, `deflate`, and `object_streams`
  retain their documented mutation semantics even when later I/O fails.
- `Pixmap.save()` streams PNG encoding directly to an unpredictable,
  exclusively created sibling in the target directory and atomically replaces
  the requested path only after the complete write succeeds. It does not
  retain a second completed PNG in memory. Replacement failures preserve
  existing output and remove the temporary file.
- `Page.insert_image()` defaults encoded JPEG/PNG input to 64 MiB and decoded
  PNG input to 64,000,000 pixels. Filename input is read under the released GIL
  through the same bounded Rust boundary used by direct core calls; PNG
  dimensions are checked before decoded storage is allocated. `max_size=None`
  and `max_pixels=None` explicitly opt trusted workloads out.
- Explicit and automatically selected OpenType input for `insert_text`,
  `insert_textbox`, `set_form_field`, and `set_fallback_font` defaults to
  64 MiB. Buffer input is rejected before its PyO3 copy and filename input is
  read through the bounded, GIL-released Rust path. `max_font_size=None`
  explicitly opts trusted workloads out.
- `insert_text()` and `insert_textbox()` default generated text input to
  1 MiB of UTF-8 and 4,096 physical or final wrapped lines. Python checks
  physical input before PyO3 copying, the Rust boundary repeats it and stops
  wrapped layout before mutation, and textbox tab expansion is preflighted
  before allocating the expanded string. `max_text_size=None` explicitly opts
  trusted insertion input out; refusals use `text_input_size` or
  `text_line_count`. AcroForm text and choice appearances retain the fixed
  4,096-line layout cap. UAX #14 and grapheme indexes, line collections, and
  retained line text grow fallibly, so allocation refusal exposes neither a
  partial layout nor a document edit. Pylopdf-owned normalization, grapheme
  references, shaped-line/glyph collections, WinAnsi output, and
  input-derived text-operator growth are fallible; Standard 14 width checks do
  not allocate an encoded text copy. Allocation refusal returns `PdfError`;
  insertion remains nonmutating and form filling rolls back atomically.
- `search_for()` accepts at most 4,096 UTF-8 bytes of search text and defaults
  returned geometry to 4,096 hits. Python rejects oversized terms before PyO3
  copying and the Rust boundary repeats both limits. `max_hits=None` explicitly
  opts trusted result sets out; refusals use `search_input_size` or
  `search_hit_count` and never return a partial list.
- Passwords used by open, authenticate, fast metadata probes, and AES-256
  output stop at 127 UTF-8 bytes before PyO3 copying or password-KDF work.
  Direct Rust calls repeat the boundary. Refusals use `password_input_size`;
  save rejection occurs before document mutation or output creation.
- `render_page_svg()` and `Page.render_svg()` default to a 64 MiB UTF-8 output
  limit and reject over-limit output before PyO3 creates the Python string;
  `max_size=None` explicitly opts out. hayro-svg 0.8 materializes one internal
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
  content and no-match/error calls preserve the document and caches. Caller
  text is counted incrementally before PyO3 copying rather than encoded in
  full for preflight.
  `max_size=None` explicitly opts out for trusted input.
- `delete_pages()` and `select()` accept at most the larger of the page count
  and 4,096 entries per call in both Python and Rust, so repeated pages cannot
  grow a selection beyond the document. Iterable reads stop at the first entry
  beyond that bound before graph mutation, raising `page_entry_count`.
  `insert_pdf()` imports any complete source range. Empty deletion preserves
  caches, generation, and existing `Page` views.
- `Page.get_images()` rejects partial results above 4,096 placements,
  64,000,000 cumulative source pixels, or 64 MiB of returned payloads per page.
  Flate-wrapped JPEG passthrough stops decompression at the remaining budget.
- `Document.embfile_add()` rejects input above 64 MiB before its PyO3 copy, and
  `embfile_get()` applies the same default to every decoded filter layer. Raise
  `max_size=` for a known large attachment; `max_size=None` explicitly accepts
  unbounded input or materialization. Attachment name trees are rejected above
  4,096 entries/nodes, 32 levels, or 1 MiB of encoded or decoded names. Caller
  lookup/deletion names and aggregate add-time key/filename/description text
  stop at 1 MiB before traversal or data copying with
  `embedded_file_input_size`. Edits validate inline FileSpecs before cloning
  above 4,096 direct objects,
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
  are visited once; writes enforce the same entry/text boundary before PyO3
  copying with `page_label_input_size`.
- AcroForm field-tree reads reject partial output above 4,096 entries/nodes,
  8,192 edges, 64 levels, 1 MiB of encoded, decoded, or returned names/values,
  or 4,096 choice-value items. Reference cycles are visited once, inherited
  values are charged per returned leaf, and fills enforce the same tree plus
  1 MiB caller name/value input atomically. Caller input stops before font
  discovery, button lookup, or file reads with `form_field_input_size`.
- Before it changes the receiving document, `insert_pdf()` compares top-level
  field names within the 1 MiB name budget and, for a partial range, walks the
  source field tree under the same node, edge, widget, and level limits. A
  complete import copies the field tree without walking it. Comparing two
  default resources for sharing reads at most 4,096 objects and 1 MiB; a
  larger pair is renamed instead.
- AcroForm button fields reject more than 4,096 widgets, 8,192 normal-appearance
  state entries, 4,096 unique returned state names, or 1 MiB of encoded/returned
  state-name text. Fills budget missing `Off` and on-state keys before mutation.
- Annotation and link reads reject partial output above 4,096 `/Annots` entries
  or 1 MiB of aggregate encoded/returned metadata text per call. Adds enforce
  the same page count, 1 MiB generated subtype plus Contents/URI input, and
  4,096 highlight rectangles before creating dependent objects or invalidating
  caches. Caller text is rejected before PyO3 copying or rectangle iteration
  with `annotation_input_size`; highlight iteration stops at item 4,097.
- Named-destination lookup visits reference cycles once and rejects traversal
  above 65,536 entries/nodes or 131,072 edges with `named_destination_count`,
  above 16 MiB of key bytes with `named_destination_text_size`, or above 32
  levels, instead of silently reporting a truncated tree as unresolved.
  `get_toc()`, `Page.get_links()`, link import in `insert_pdf()`, and the
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
  enforce the same entry, depth, and title-text limits before PyO3 copying and
  mutation with `toc_entry_count` and `toc_input_size`. Page deletion and
  selection share the outline limits, so every written outline stays editable.
- `Document.metadata` decodes only the eight standard Info fields and rejects
  aggregate source or returned text above 1 MiB; custom entries do not become
  Python output. `peek_metadata(max_file_size=)` can reject path or byte input
  before parsing and also caps returned standard text; `None` preserves the
  unbounded input default. Writes preflight 1 MiB aggregate source/encoded text
  before PyO3 copying with `metadata_input_size` and apply atomically.
- Embedded JavaScript is never executed; it is unsupported by design.
- `render_pages()` renders every page by default, accepts at most the larger
  of the page count and 4,096 explicit entries, and defaults to a 512 MiB
  cumulative encoded-PNG limit. Completed parallel results share one atomic
  budget and failures return no partial list; `max_size=None` explicitly opts
  out. Worker admission separately caps estimated live raster/conversion
  buffers, and each worker replaces its hayro cache after 4,096 pages, so do
  not add unbounded application-level parallelism around it.
- `Document.to_markdown()` converts every page by default, accepts at most the
  larger of the page count and 4,096 explicit entries, and defaults to a
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
