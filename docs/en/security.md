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
`text_size`, or `decompression_unverifiable`. The same code is also
`error.args[0]`. A filter chain that cannot be bounded safely is rejected
instead of being decoded optimistically.

`doc.complexity` reports page, object, and stream counts, encoded stream bytes,
and maximum direct object depth. It neither decodes streams nor invokes the
renderer, so it is suitable for routing work before extraction. Structural and
decompression budgets validate the opened source; reopen generated output with
the same policy when it must cross another trust boundary.

- Rendering is capped at 64 megapixels per page.
- Embedded JavaScript is never executed; it is unsupported by design.
- `render_pages()` keeps its normal bounded-memory worker admission; do not add
  unbounded application-level parallelism around it.
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
