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

pylopdf is written in Rust and ships no runtime dependencies, but hostile PDF
input remains inherently risky.

!!! warning "Set an explicit decompression budget"
    Pass `max_decompressed_size=` to `pylopdf.open()`. pylopdf validates every
    readable stream before returning the document, including page content that
    would otherwise be decompressed lazily by the renderer.

```python
import pylopdf

with pylopdf.open("upload.pdf", max_decompressed_size=128 * 1024 * 1024) as doc:
    preview = doc[0].get_pixmap(dpi=144)
```

- Decoded image streams are bounded by their RGBA size.
- Filter chains whose output cannot be bounded safely are rejected while a
  limit is enabled.
- Rendering is capped at 64 megapixels per page.
- Embedded JavaScript is never executed; it is unsupported by design.
- Run batch processing of untrusted files in a sandbox or container when
  possible.

## Dependency auditing { #dependency-auditing }

CI runs `cargo audit` against the Rust dependency tree and the RustSec advisory
database on every push.

The repository copy of this policy is
[`SECURITY.md`](https://github.com/yhay81/pylopdf/blob/main/SECURITY.md).
