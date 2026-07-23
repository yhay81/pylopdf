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

pylopdf is written in Rust (lopdf + hayro) and ships no runtime dependencies,
but parsing hostile PDF input is inherently risky. When processing untrusted
files:

- Pass `max_decompressed_size=` to `pylopdf.open()` to validate every readable
  stream before returning the document, including page content that the renderer
  would otherwise decompress lazily. Image streams are bounded by their decoded
  RGBA size; filter chains whose output cannot be bounded safely are rejected
  while the limit is enabled.
- Rendering is bounded to 64 megapixels per page. Embedded JavaScript is never
  executed (unsupported by design).
- Prefer running batch processing of untrusted documents in a sandboxed or
  containerized environment.

## Dependency auditing

CI runs `cargo audit` against the Rust dependency tree (RustSec advisory
database) on every push.
