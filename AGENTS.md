# AGENTS.md

This file is the canonical development context for coding agents.
`CLAUDE.md` only imports this file; update this file instead.

pylopdf is a published Python library for PDF editing, rendering, extraction,
and generation, implemented in Rust. Editing is powered by
[lopdf](https://github.com/J-F-Liu/lopdf), rendering and extraction by
[hayro](https://github.com/LaurenzV/hayro), and generated text and form
appearances by [krilla](https://github.com/LaurenzV/krilla) plus HarfRust. Its
API is inspired by pymupdf. See [README.md](README.md) for the concept and API
overview.

## Working conventions

- Commit directly to `main` and push after each coherent unit of work. Do not use
  feature branches.
- Write commit messages, code comments, docstrings, repository documentation,
  configuration comments, and user-facing messages in English.
- Non-English text is allowed only in localized documentation and data required
  to test Unicode or CJK behavior.
- Do not place experiments unrelated to PDF processing in this repository.

## Development commands

- `uv sync` — build the extension and install dependencies. Rust changes are
  included in uv's rebuild cache keys.
- `uv run pytest` / `uv run ruff check .` / `uv run mypy src tests`
- `uv sync --group bench && uv run python bench/run.py` — run reproducible
  benchmarks. Results are written to `bench/results/latest.md`; publish wins and
  losses together.
- `uv sync --all-extras && uv run python bench/ocr.py` — reproduce native OCR
  strict/NFKC CER and elapsed time on the licensed MHLW fixture. Results are
  written to `bench/results/ocr-latest.md`.
- `uv sync --group docs && uv run zensical serve -f mkdocs.yml` — preview the
  English documentation with Zensical. Locale configurations are
  `mkdocs.ja.yml`, `mkdocs.zh-cn.yml`, and `mkdocs.ko.yml`. To reproduce the
  production validation, build all four configurations in EN → JA → zh-CN → KO
  order with `uv run --no-sync zensical build -f <config> -c -s`. A push to
  `main` deploys the site through `docs.yml`.
- `cargo clippy --manifest-path rust/Cargo.toml --all-targets` /
  `cargo fmt --manifest-path rust/Cargo.toml`
- Do not add Rust unit tests. Verify all behavior through Python tests in
  `tests/`.
- Real-world PDF regressions belong in `tests/test_real_world.py`. Record corpus
  sources, licenses, and known limitations in
  `tests/assets/real_world/README.md`, and bundle only redistributable files.

## Architecture and invariants

- `_Document` (`rust/src/document.rs`) is a thin conversion and error-mapping
  layer. The Python `Document` (`src/pylopdf/__init__.py`) owns validation,
  zero-/one-based conversion, and closed-state handling.
- Python API page numbers are zero-based; Rust/lopdf page numbers are one-based.
  Keep the conversion centralized in `_lopdf_page_number`.
- `merge` and `select` must materialize inherited page attributes (`Resources`,
  `MediaBox`, `CropBox`, `Rotate`) into page dictionaries because lopdf does not
  resolve page attribute inheritance.
- Text extraction is implemented as a hayro `Device` in `rust/src/extract.rs`.
  It collects glyph Unicode and positions, then assembles lines
  (`LINE_TOLERANCE`), words (`WORD_GAP`), and blocks (`BLOCK_GAP`).
  `get_text("words"/"blocks"/"dict")` and `search_for` share the same glyph
  collection through a bounded, generation-invalidated `TextPage` cache. CJK
  fallback configuration also applies to extraction, including invisible OCR
  text. Hayro normalizes glyph space to 1000 upem, so font size is the transform
  factor × 1000. Vertical bboxes approximate baseline ± a size ratio.
  Sustained whitespace gutters split same-baseline segments into recursive
  left-to-right columns; full-width headings and footers remain outside the
  column regions, and isolated wide gaps stay on one line. `find_tables` uses a
  separate bounded, generation-invalidated `TablePage` cache so normal text
  extraction does not collect or analyze vector rules. It collects at most
  4096 axis-aligned candidates from strokes or thin filled polygons. A table
  requires a connected outer grid with at least two rows and columns.
  Rectangular merged cells are tiled from missing internal dividers; covered
  row-major slots are `None`, with an internal anchor map retained for exact
  Markdown span expansion. Coarse grid spans gain synthetic dividers only when
  at least three evenly led physical lines occupy half the cross-axis slots and
  adjacent slot signatures overlap by at least 0.8. This inference is symmetric
  across right-angle rotations; hybrid grids score 0.95 while complete vector
  grids score 1.0. Materialization is capped at 4096 slots and merged-span
  searches at 65,536 candidates. The opt-in borderless
  `strategy="text"` requires at least three consecutive physical rows with the
  same segment count, aligned left or right edges, compatible leading, and
  clear gaps. It intentionally does not run as the default because aligned
  multicolumn prose is geometrically ambiguous. `find_tables(clip=)` returns
  only complete candidate bboxes inside the display-coordinate region; it does
  not synthesize partial tables or reduce the cached full-page interpretation
  cost. `TableDiagnostics.confidence` is a deterministic ranking heuristic,
  not a probability. Text diagnostics retain em-normalized alignment error,
  minimum gutter, and row-gap variation.
  `Document.to_markdown()` inserts complete bordered tables by default and
  accepts `table_strategy="text"` for conservative non-overlapping borderless
  candidates or `None` to disable table conversion. It removes contained text
  from prose and heading inference while retaining words outside a table on the
  same physical line, and normalizes physical table matrices to the dominant
  logical text direction on right-angle rotations. Merged spans expand from the
  internal anchor map rather than guessing from adjacent empty slots.
  Extraction coordinates use the same display space as rendering by passing
  `initial_transform(true)` to the context, resolving page rotation and CropBox
  offsets. Baseline direction is retained and exposed in line dicts. Rotated
  baselines assemble along their direction while remaining writing mode 0.
  Uniform axis-aligned pages derive logical inline and block axes from that
  direction, so 90/180/270-degree lines and sustained columns preserve reading
  order. Explicitly rotated glyph bboxes follow the baseline and cross-axis;
  inferred mode-1 CJK keeps its conservative upright-glyph approximation.
  Because hayro does not expose font WMode, mode-1 CJK lines are inferred only
  from conservative single-glyph vertical chains: top-to-bottom within a line,
  right-to-left across columns, with horizontal headings and footers preserved.
  Ruby, warichu, and mixed-orientation typography are not interpreted.
- Native OCR uses RTen 0.24 with only its `rten_format` feature. The core wheel
  contains the pure-Rust inference engine; PP-OCRv6 small detector,
  recognizer, and dictionary data come from the independently versioned
  `pylopdf-ocr-models` package through the `[ocr]` extra. `OcrEngine` loads one
  immutable model set and owns a dedicated 1–16 thread pool. Loading and
  inference release the GIL. OCR clones the Pixmap's `Arc<[u8]>`, composites
  RGBA onto white, and uses overlapping detector tiles bounded to 256–2048
  pixels, a 4096-candidate cap, and deterministic edge deduplication. The
  default 1408-pixel tile and 192-pixel overlap measured about 419 MiB peak on
  a 300-dpi A4 page. Recognizer class count must match dictionary length + 2.
  Results use rotation-resolved display coordinates and recursive sustained-
  gutter column order. One immutable engine can serve calls on distinct
  Documents, including CPython 3.14t. A Python admission semaphore covers the
  complete render-and-recognize operation; `max_concurrent=1` is the
  memory-safe default and values through 16 require workload measurement.
  Every admitted call owns raster and inference buffers. Same-Document
  restrictions still apply. `Page.apply_ocr` skips pages with extractable text
  by default so repeated runs are idempotent. With `clip=`, only intersecting
  text triggers the skip and result boxes remain in full-page display
  coordinates. Clipping reduces OCR detector input but not hayro's current
  full-page rendering cost. The first engine returns axis-aligned boxes only;
  `rotation=90 / 180 / 270` turns the rendered OCR input clockwise, maps boxes
  back to the unmodified display space, and makes `apply_ocr` orient its
  invisible baseline accordingly. Nonzero rotation temporarily adds one RGBA
  raster copy inside the complete-call admission limit. Arbitrary skew,
  automatic page orientation, ruby, warichu, and mixed-orientation typography
  are not interpreted.
- Rendering caches a hayro snapshot in `_Document.hayro_pdf`. An unedited,
  unencrypted load first consumes its original input bytes and falls back to a
  lopdf serialization only when hayro rejects them or reports a different page
  count. Editing methods must call `invalidate_hayro_pdf`, which also discards
  the original-byte fast path; edited state must always be reflected in
  rendering.
- `Document.render_pages` is the supported same-document concurrency boundary:
  it renders an immutable hayro snapshot on a dedicated rayon pool, preserves
  input order, releases the GIL, accepts 1–64 requested workers, and caps actual
  concurrency to roughly 512 MB of estimated raster and conversion buffers.
  Other simultaneous calls or edits on the same `Document` are outside the
  contract.
- `Page.get_pixmap(clip=)` accepts rotation-resolved display coordinates,
  intersects them with the page, and rounds outward to pixel boundaries.
  hayro 0.7 lacks an offset viewport, so clipping crops a full-page raster and
  does not relax the full-page render-size limits.
- Release the GIL with `Python::detach` for heavy operations: load, save, render,
  extraction, merge, and compression.
- `Page` is a lightweight view of a `Document` plus a generation number.
  Python methods that change page structure must call `_bump_generation()`.
  Otherwise an old `Page` could silently refer to a different page. Old pages
  must raise `StalePageError` after structural changes.
- Rust defines `PdfError` (a `ValueError`-compatible base) and `PasswordError`;
  Python defines `DocumentClosedError`, `EncryptedDocumentError`, and
  `StalePageError`. Add new errors under the `PdfError` hierarchy instead of
  introducing plain `ValueError` exceptions.
- Encryption during `save` operates on a clone, so the in-memory document always
  remains plaintext. Python generates the key with `os.urandom(32)`.
- TOC page numbers in `get_toc` and `set_toc` are one-based for pymupdf
  compatibility. All other page APIs are zero-based.
- lopdf automatically decrypts PDFs with an empty user password. Other encrypted
  PDFs require the `password` argument or `authenticate()`, which reopens the
  document with a password. `_ensure_open` must check `is_encrypted` because an
  undecrypted document otherwise appears to have zero pages.
- CJK fallback replaces hayro's `font_resolver`
  (`pick_cjk_fallback` in `rust/src/document.rs`). Detect CJK through
  `CIDSystemInfo` or the `BaseFont` name. Serif-like names use the serif slot;
  other names use sans. Font files come from
  `fonts/pylopdf-fonts-cjk/`, an uv workspace member exposed through the `[cjk]`
  extra and auto-detected during rendering.
- Drawing (`rust/src/draw.rs`) appends streams to `/Contents` without
  re-encoding existing content. Existing arrays are wrapped in `q/Q` only once.
  Inputs use display coordinates with a top-left origin and page rotation
  resolved, then convert to `cm`/`Tm`. Annotations must always include an
  appearance stream at `AP /N`, because hayro does not render annotations
  without one. `render_annotations` defaults to true.
- Embedded-font text generation lives in `rust/src/generate.rs`. krilla is
  pinned to 0.8.2 with all default features disabled; HarfRust 0.12 supplies
  shaping without krilla's unmaintained rustybuzz/ttf-parser path. Raster and
  PDF-import features remain disabled. Generation creates a transparent page
  in target display coordinates, subset-embeds the selected OpenType face, and
  returns bytes that the existing lopdf Form-XObject path imports. It releases
  the GIL, rejects missing glyphs, and does not provide fallback or paragraph
  layout. RTL shapes render, but extraction currently follows visual order.
  Keep third-party acknowledgements in `NOTICE.md` and include both license
  files through PEP 639. The Windows abi3 wheel measured 5.42 MB after
  integration, up from 4.44 MB.
- `Page.insert_textbox` completes layout before mutating the PDF and returns
  negative spare height without drawing on overflow. Standard 14 measurement
  uses canonical Adobe AFM widths; embedded OpenType measurement uses HarfRust
  advances and font vertical metrics. Both share greedy UAX #14 wrapping in
  `rust/src/layout.rs`, with grapheme-safe emergency breaks and soft-line-only
  justification. Keep page rotation resolved through display coordinates and
  preserve the no-mutation overflow boundary. The resulting Windows abi3 wheel
  is 5.58 MB, up 0.16 MB from the arbitrary-font baseline.
- AcroForm filling writes `/V`, synchronizes button `/AS`, and regenerates
  widget `/AP /N`. Text and choice appearances auto-fit in widget-local
  coordinates, respect inherited `/Q` and multiline `/Ff`, and reuse the Core
  14 or krilla generation paths. Comb text fields resolve inherited `/MaxLen`,
  count Unicode graphemes, position them individually, and reject overlength or
  incompatible flag combinations. Preserve non-empty authored button states
  and synthesize only missing/empty Off/on states. Widget `/MK /R`, `/BG`,
  `/BC`, `/BS /W`, and legacy `/Border` feed the appearance. Keep updates
  atomic by restoring the document clone on error. hayro 0.7 cannot select an
  `/AP /N` state dictionary, so the rendering snapshot substitutes any
  resolvable widget `/AS` stream; the editable/saved PDF remains canonical.
  Retain the original-byte fast path when no selected state stream can be
  resolved.
- Encode non-ASCII metadata strings as UTF-16BE with a BOM.
- GIL-enabled CPython 3.10–3.14 uses one `abi3-py310` wheel per platform.
  Free-threaded CPython 3.14 uses a version-specific `cp314-cp314t` wheel.
  Add `abi3t-py315` only when 3.15t builds can be tested: enabling it alongside
  3.14t breaks maturin's cross-compilation config by raising the implied
  minimum interpreter version. Add size-increasing dependencies cautiously;
  published v0.10.0 wheels are about 5.0–5.8 MiB depending on platform and ABI.
- Hayro warnings are collected by the interpreter settings sink in
  `pending_warnings`; Python's `_emit_warnings` drains them as
  `PylopdfWarning` after each operation.
- `Pixmap` is immutable. Version-specific builds expose its RGBA8 storage
  through a read-only zero-copy buffer. The buffer protocol remains unavailable
  under `abi3-py310` because `Py_buffer` entered the stable ABI in Python 3.11;
  `Pixmap.samples` is the one-copy portable fallback.
- Concurrent operations on distinct `Document` objects are supported.
  Concurrent external calls or edits on the same `Document` are not; `Page`
  shares its parent's restriction. `Document.render_pages` is the supported
  bounded same-document parallel read boundary.
## Known pitfalls

- lopdf's `time` feature contains an uncompilable `From<time::Time>`
  implementation introduced in 0.43.0. Upstream #527 is fixed but unreleased,
  so this project uses `chrono`.
- lopdf's content parser drops all operations after a comment line followed by
  an indented line, reported as lopdf#535. pylopdf is unaffected since v0.7
  because extraction moved to hayro (`rust/src/extract.rs`).
- The pre-commit `validate-pyproject` hook with `trove-classifiers` validates
  classifier existence. v0.4.0 was rejected by PyPI because of the invalid
  classifier `Topic :: Text Processing :: Markup :: PDF`.
  Do not add `validate-pyproject-schema-store`; it raises `UnboundLocalError`.
- Synchronize the version manually in three places: `pyproject.toml`,
  `rust/Cargo.toml`, and `src/pylopdf/__init__.py`.
- Release CI cross-compiles macOS x86_64 on an arm64 runner because Intel runner
  queues are slow.

## Release procedure

1. Update the version in all three locations, add the changelog entry, commit,
   and push.
2. Run `git tag -a vX.Y.Z -m "..." && git push origin vX.Y.Z`.
3. GitHub Actions (`release.yml`) builds abi3 and cp314t wheels plus the sdist
   for five platforms and publishes through PyPI Trusted Publishing.

The font wheel has a separate release process. Update the version in
`fonts/pylopdf-fonts-cjk/pyproject.toml`, then push a `fonts-vX.Y.Z` tag to run
`release-fonts.yml`. The first release requires registering the
`pylopdf-fonts-cjk` Trusted Publisher on PyPI with workflow
`release-fonts.yml` and environment `pypi`. Publish the font wheel before the
main package because the main `[cjk]` extra references it.

The OCR model wheel also releases separately. Update the version in
`models/pylopdf-ocr-models/pyproject.toml` and
`models/pylopdf-ocr-models/src/pylopdf_ocr_models/__init__.py`, keep the
artifact hashes synchronized in `SHA256SUMS` and its README, then push an
`ocr-models-vX.Y.Z` tag. Tests and `release-ocr-models.yml` consume that
manifest. The release workflow runs `tools/smoke_ocr_models_artifact.py`
against isolated wheel and sdist installations before attestation. The first
release requires registering the `pylopdf-ocr-models` Trusted Publisher on
PyPI with workflow `release-ocr-models.yml` and environment `pypi`. Publish the
model wheel before the main package because the main `[ocr]` extra references
it.

## Roadmap

[ROADMAP.md](ROADMAP.md) is the canonical medium-term plan, based on the
2026-07-22 market and upstream survey plus the 2026-07-23 deeper review of
out-of-scope areas. It includes strategy, the v0.6–v1.0 release plan, ecosystem
integrations, a watchlist, and explicit non-goals.

- Current phase: v0.10.0 was released on 2026-07-25. It hardens malformed-input
  handling and adds reusable TextPage/TablePage interpretation, parallel batch
  rendering, clipped pixmaps, vertical CJK and table extraction depth,
  arbitrary-font text insertion, and native CPython 3.14t wheels. The current
  v0.11 work also completes `insert_textbox`, AcroForm appearances, and typed
  public mapping contracts. Incremental save was rejected after OSS analysis
  and remains on the watchlist; the gated `[ocr]` track and product refinement
  remain. v1.0 is targeted no earlier than 2026-08, after field feedback rather
  than as a deadline-driven API freeze.
- lopdf#535 no longer affects pylopdf since the v0.7 hayro extraction engine.
  An upstream fix remains a parallel contribution candidate.
- See [CHANGELOG.md](CHANGELOG.md) for completed history.
