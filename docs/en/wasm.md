# WebAssembly compatibility

pylopdf builds a static PyEmscripten wheel for the Python 3.13 ABI used by
Pyodide 0.28.3 and Cloudflare Python Workers. The binary contains the same Rust
PDF engines as the native wheel; it does not use a JavaScript PDF
implementation or a wasm-bindgen shim.

!!! note "Release status"

    The WebAssembly build is available on `main` and will first be published
    with the package release after v0.10.0. v0.10.0 itself contains only native
    wheels.

## Distribution status

| Environment | Status | Detail |
|---|---|---|
| Cloudflare Python Workers | Release-gated | CI resolves the PEP 783 wheel with `workers-py` 1.15.0 and dry-runs a bundle with Wrangler 4.114.0. A tagged release repeats this check from PyPI before creating the GitHub release. |
| Pyodide 0.28.3 in Node.js | Compatibility-gated | CI installs the locally built legacy-tag wheel into the exact runtime and executes the full compatibility suite. |
| Direct browser installation from PyPI | Not yet a supported path | Pyodide 0.28.3's `micropip` predates the PEP 783 `pyemscripten_*` tag required by PyPI. The binary is compatible, but a stable public installation flow still needs newer frontend tooling. |
| Other Pyodide or Python/Wasm versions | Not tested | Platform and ABI compatibility must be established before widening the wheel tag or support claim. |

The published artifact uses
`cp310-abi3-pyemscripten_2025_0_wasm32`. The builder first runs the same binary
under the runtime-native `pyodide_2025_0_wasm32` tag, then deterministically
retags it for PEP 783 publication. Only the PyEmscripten-tagged artifact enters
PyPI, provenance attestation, and the release SBOM.

## Tested API surface

The shared native/Wasm suite currently covers:

- `bytes` input without a host filesystem, page counts, PDF 2.0 parsing, and
  encrypted AES-256 input;
- plain text, words, dictionaries, search, document Markdown, embedded Japanese
  text, inferred vertical CJK, sustained multicolumn order, image-only pages,
  and rotated pages;
- bordered and conservative borderless tables, including Markdown integration,
  plus vector drawing extraction;
- empty-document creation, Standard 14 and subset-embedded OpenType text,
  textbox layout, rendering, `Pixmap`, serialization, virtual-filesystem save,
  merge, reorder, duplicate, and select;
- `PdfError`, `PasswordError`, `EncryptedDocumentError`,
  `DocumentClosedError`, and `StalePageError`, followed by reuse of the runtime
  after malformed input; and
- `render_pages(workers=4)` input ordering and byte equality with
  `workers=1`.

The fixtures include a PDF 2.0 sample, an embedded-CJK Japanese government
document, IRS Form 1040, a rotated US Senate table, an image-only Japanese scan,
and a generated vertical-writing document. Every committed PDF is under 1 MB
and has a redistributable license recorded in the corpus README.

The suite runs once with the native wheel and once inside Pyodide. Logical
results must match exactly. It checks explicit structure and expected text in
addition to full extraction and Markdown hashes, so a native/Wasm divergence
cannot hide behind a permissive smoke test.

## Runtime differences and limits

- Emscripten has no rayon worker pool in this build.
  `render_pages(workers=...)` accepts the normal public arguments but executes
  serially. Native builds keep bounded rayon parallelism.
- Paths refer to the runtime's virtual filesystem, not the user's browser or
  Worker host. Prefer `Document(stream=data)` and `tobytes()` at application
  boundaries.
- Rendering limits are unchanged. `clip=` reduces returned pixels but hayro
  still rasterizes the complete page internally.
- Native OCR and the separately distributed OCR model package are not in the
  current WebAssembly compatibility contract.
- Automatic external CJK fallback-font discovery is not covered. Embedded CJK
  PDFs are tested, and applications may supply font bytes explicitly.
- The current gate proves Cloudflare bundle construction, not a live
  authenticated production deployment.

Resource-limit and adversarial-input coverage is tracked separately from this
functional matrix. Do not infer a larger memory budget merely because a PDF
passes on native Python.
