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
- `Page.get_images()` rejects partial results above 4,096 placements,
  64,000,000 cumulative source pixels, or 64 MiB of returned payloads per page;
  Flate-wrapped JPEG passthrough is decompressed only to the remaining budget.
- `Document.embfile_get()` bounds every attachment decoding layer to 64 MiB by
  default and raises `LimitError` with code `embedded_file_size`. Raise
  `max_size=` for a known large attachment; `None` is an explicit unbounded
  opt-out. Attachment name trees are also rejected above 4,096 entries/nodes,
  32 levels, or 1 MiB of encoded or decoded names.
- Prefer running batch processing of untrusted documents in a sandboxed or
  containerized environment, and enforce CPU deadlines in the host. pylopdf
  resource budgets do not provide in-process time cancellation.

## Dependency auditing

CI runs `cargo audit` against the Rust dependency tree (RustSec advisory
database) on every push.
