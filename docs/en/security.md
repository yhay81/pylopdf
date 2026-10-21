---
title: Security
description: Supported versions, private vulnerability reporting, and how to process untrusted PDFs safely with pylopdf.
---

# Security

## Supported versions { #supported-versions }

Only the latest release on PyPI receives security fixes.

## Report a vulnerability { #report-a-vulnerability }

Report vulnerabilities privately through
[GitHub Security Advisories](https://github.com/yhay81/pylopdf/security/advisories/new).
Do not open a public issue. You should receive an initial response within one
week.

## Handle untrusted PDFs { #untrusted-pdfs }

pylopdf is written in Rust and has no required Python dependencies, but a PDF
from an unknown source can still be built to use excessive memory or time.
When you process files you do not control, such as user uploads, open them
with a resource policy:

```python
import pylopdf

try:
    with pylopdf.open("upload.pdf", limits=pylopdf.DocumentLimits.web()) as doc:
        if doc.complexity["page_count"] > 50:
            reject_upload("too many pages")
        preview = doc[0].get_pixmap(dpi=144)
except pylopdf.LimitError as error:
    reject_upload(error.code)
```

!!! warning "Enforce time limits and isolate the process"

    Limits bound memory and output size, but they do not stop a call that is
    taking too long. Enforce a CPU or wall-clock deadline in the worker,
    process, or container, and process untrusted files in a sandbox or
    container when you can.

`DocumentLimits.web()` is a conservative starting point for web servers and
queue workers with limited memory. It sets these limits:

| Resource | Limit |
|---|---:|
| File size | 10 MiB |
| Pages | 200 |
| Objects | 100,000 |
| Nesting of arrays and dictionaries | 64 levels |
| Any single decoded stream, including decoded images | 64 MiB |
| One page's content stream | 10 MiB |
| All decoded streams together | 128 MiB |
| Extracted text, in total | 1 MiB |
| Extracted characters (glyphs), in total | 65,536 |
| The PDF data handed to the renderer | 64 MiB |

To change a limit, build your own policy. Every argument is optional, and
`None` means no limit:

```python
limits = pylopdf.DocumentLimits(
    max_file_size=50 * 1024 * 1024,
    max_pages=1_000,
    max_decompressed_size=128 * 1024 * 1024,
)
```

| Argument | Limits |
|---|---|
| `max_file_size` | Size of the input file |
| `max_pages` | Number of pages |
| `max_objects` | Number of objects |
| `max_object_depth` | Nesting of arrays and dictionaries |
| `max_decompressed_size` | Size of any single decoded stream |
| `max_page_content_size` | Size of one page's decoded content |
| `max_total_decompressed_size` | Size of all decoded streams together |
| `max_text_size` | Total UTF-8 size of extracted text |
| `max_text_glyphs` | Total number of extracted characters |
| `max_interpretation_size` | Size of the PDF data that rendering and extraction read |

`doc.complexity` reports page, object, and stream counts, the encoded stream
size, and the deepest nesting, without decoding anything. Use it to route or
reject a document before doing heavy work.

Documents opened without `limits` have no open-time limits, but the per-call
limits below still apply.

### Limits on individual calls { #default-limits }

Methods that read or produce large amounts of data have their own limit, with
a default that is safe for most uses. Raise it with the keyword shown, or pass
`None` to remove it for input you trust.

| Method | Default limit | Keyword |
|---|---|---|
| `Document.tobytes()` | 512 MiB of output | `max_size` |
| `Document.render_pages()` | 512 MiB of PNG output in total | `max_size` |
| `to_markdown()`, `Table.to_markdown()` | 64 MiB of output | `max_size` |
| `Pixmap.tobytes()`, `Page.get_svg_image()` | 64 MiB of output | `max_size` |
| `Page.search_for()` | 4,096 results | `max_hits` |
| `Page.insert_text()`, `insert_textbox()` | 1 MiB of text and 4,096 lines | `max_text_size` |
| Font input to drawing, forms, and `Font` | 64 MiB per font | `max_font_size` |
| `Page.insert_image()` | 64 MiB of input and 64 million pixels | `max_size`, `max_pixels` |
| `Page.replace_text()` | 64 MiB of page content | `max_size` |
| Attachments: `add()`, `read()`, `write()` | 64 MiB per file | `max_size` |
| `Stream.read()` | 64 MiB | `max_size` |
| `Document.get_pdfa_claim()` | 1 MiB of XMP metadata | `max_size` |
| `OcrEngine()` | 64 MiB of model files | `max_model_size` |

A few limits are fixed. Rendering is capped at 64 million pixels per page.
Passwords stop at 127 UTF-8 bytes, the PDF 2.0 maximum. Lists of pages passed to
`select()`, `delete_pages()`, `get_text()`, `to_markdown()`, and
`render_pages()` may hold up to the page count or 4,096 entries, whichever is
larger. Outlines, page labels, form fields, annotations, and attachment lists
have generous fixed limits sized for large real-world documents.

A refused call raises before it changes the document and never returns
partial results.

### Error codes { #error-codes }

`LimitError` is a subclass of `PdfError`. Its `code` attribute, also
`error.args[0]`, names the limit that was reached and is stable across
releases, so programs can act on it. The message is for people and may
change.

| Codes | Reached when |
|---|---|
| `file_size`, `page_count`, `object_count`, `object_depth` | The document exceeds a structural limit of its `DocumentLimits` |
| `decompressed_size`, `page_content_size`, `total_decompressed_size`, `decompression_unverifiable`, `stream_filter_count` | A stream decodes to more than allowed, or uses filters whose output cannot be bounded (including more than 16 chained filters) |
| `text_size`, `text_glyph_count`, `interpretation_size` | Extraction or rendering would read more than the policy allows |
| `recovery_scan_size` | Repairing a damaged file would take an unreasonable amount of work |
| `content_nesting_depth`, `resource_nesting_depth` | A page is built so deeply nested that interpreting it is unsafe; see [below](#built-in-protections) |
| `pdf_output_size`, `render_output_size`, `markdown_output_size`, `svg_output_size`, `pixmap_output_size`, `replacement_output_size` | Output would exceed `max_size` |
| `search_input_size`, `search_hit_count` | The search text is over 4,096 bytes, or there are more results than `max_hits` |
| `text_input_size`, `text_line_count`, `font_input_size`, `path_point_count` | Text, fonts, or shape paths passed for drawing are too large |
| `image_input_size`, `image_pixel_count`, `image_inventory_count` | An image is too large, or a page uses more than 4,096 images |
| `embedded_file_size`, `embedded_file_input_size` | An attachment, or its name or description, is too large |
| `stream_data_size`, `object_value_depth` | A low-level stream is too large to read, or a value written is nested too deeply |
| `password_input_size`, `metadata_input_size`, `annotation_input_size`, `form_field_input_size`, `toc_input_size`, `page_label_input_size`, `replacement_input_size` | Text passed to the method is too large |
| `page_entry_count` | A list of pages is longer than allowed |
| `toc_entry_count`, `toc_text_size`, `named_destination_count`, `named_destination_text_size`, `structure_tree_count`, `form_content_size` | An outline, link destination table, tagged-PDF structure, or imported page is too large |
| `xmp_metadata_size` | The XMP metadata is larger than `max_size` |
| `ocr_model_size`, `ocr_dictionary_entries` | OCR model files are too large |

## Built-in protections { #built-in-protections }

These apply to every document, with or without `DocumentLimits`:

- **Deep nesting.** The rendering engine, hayro, reads nested structures
  recursively. Before rendering or extracting a page, pylopdf checks how deeply
  its content, color spaces, and functions nest, and refuses a page that would
  overflow the stack or be silently cut short (`content_nesting_depth`,
  `resource_nesting_depth`, `object_depth`). Other pages of the document stay
  usable.
- **Renderer crashes.** If the renderer panics on malformed data, pylopdf
  raises `PdfError` instead of an exception that `except Exception` would not
  catch, and the document stays usable.
- **Damaged files.** pylopdf repairs broken cross-reference data when it
  can, emits a `PylopdfWarning`, and sets `doc.is_repaired`. Reject such
  documents if your application must not accept repaired input. Some damaged
  files cannot be opened and raise `FileDataError`.
- **Safe saving.** `save()` and `Pixmap.save()` write to a temporary file
  next to the destination and replace it only after a complete write, so a
  failure never leaves a half-written file. Existing file permissions are kept,
  including Windows access control lists.
- **No scripts.** Embedded JavaScript is never executed.

## Recommendations { #recommendations }

- **Enforce time limits in the host.** Limits bound memory and output size, but
  some valid-looking documents take a long time to render. Run untrusted work
  with a CPU or wall-clock deadline in the worker, process, or container.
- **Isolate batch processing.** Process untrusted files in a sandbox or
  container when you can.
- **Do not rely on page deletion for redaction.** Deleting pages removes
  their links, form fields, and structure entries, but copies of their content
  can remain elsewhere in the file, such as in attachments or metadata.
- **Use `render_pages()` for parallel rendering.** It already limits its own
  memory use, so do not wrap it in additional unbounded parallelism.

pylopdf's continuous integration runs the same hostile-input tests on native
Python and WebAssembly, and scheduled fuzzing feeds it malformed
cross-reference tables, reference cycles, deeply nested objects, broken
streams, and compression bombs.

## Dependency auditing { #dependency-auditing }

CI runs `cargo audit` against the Rust dependencies and the RustSec advisory
database on every push.

The repository copy of this policy is
[`SECURITY.md`](https://github.com/yhay81/pylopdf/blob/main/SECURITY.md).
