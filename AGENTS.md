# AGENTS.md

This file is the canonical development context for coding agents.
`CLAUDE.md` only imports this file; update this file instead.

pylopdf is a published Python library for PDF editing and rendering, implemented
in Rust. Editing is powered by [lopdf](https://github.com/J-F-Liu/lopdf) and
rendering by [hayro](https://github.com/LaurenzV/hayro). Its API is inspired by
pymupdf. See [README.md](README.md) for the concept and API overview.

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
  row-major slots are `None`. Materialization is capped at 4096 slots and
  merged-span searches at 65,536 candidates. The opt-in borderless
  `strategy="text"` requires at least three consecutive physical rows with the
  same segment count, aligned left or right edges, compatible leading, and
  clear gaps. It intentionally does not run as the default because aligned
  multicolumn prose is geometrically ambiguous.
  Extraction coordinates use the same display space as rendering by passing
  `initial_transform(true)` to the context, resolving page rotation and CropBox
  offsets. Baseline direction is retained and exposed in line dicts. Rotated
  baselines assemble along their direction while remaining writing mode 0.
  Because hayro does not expose font WMode, mode-1 CJK lines are inferred only
  from conservative single-glyph vertical chains: top-to-bottom within a line,
  right-to-left across columns, with horizontal headings and footers preserved.
  Ruby, warichu, and mixed-orientation typography are not interpreted.
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
- Encode non-ASCII metadata strings as UTF-16BE with a BOM.
- Wheels use a single `abi3-py310` build for Python 3.10–3.14. Add size-increasing
  dependencies cautiously; the wheel is currently about 3.5 MB.
- Hayro warnings are collected by the interpreter settings sink in
  `pending_warnings`; Python's `_emit_warnings` drains them as
  `PylopdfWarning` after each operation.
- The buffer protocol is unavailable under `abi3-py310` because `Py_buffer`
  entered the stable ABI in Python 3.11. `Pixmap.samples` is a one-copy `bytes`
  value.

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
3. GitHub Actions (`release.yml`) builds wheels and the sdist for five platforms
   and publishes through PyPI Trusted Publishing.

The font wheel has a separate release process. Update the version in
`fonts/pylopdf-fonts-cjk/pyproject.toml`, then push a `fonts-vX.Y.Z` tag to run
`release-fonts.yml`. The first release requires registering the
`pylopdf-fonts-cjk` Trusted Publisher on PyPI with workflow
`release-fonts.yml` and environment `pypi`. Publish the font wheel before the
main package because the main `[cjk]` extra references it.

## Roadmap

[ROADMAP.md](ROADMAP.md) is the canonical medium-term plan, based on the
2026-07-22 market and upstream survey plus the 2026-07-23 deeper review of
out-of-scope areas. It includes strategy, the v0.6–v1.0 release plan, ecosystem
integrations, a watchlist, and explicit non-goals.

- Current phase: v0.9.0 was released on 2026-07-23 and verified end to end on
  PyPI. It includes an invisible OCR layer, `to_markdown`, AcroForm filling,
  attachments, page labels, and PDF/A claim reading. Incremental save was
  rejected after OSS analysis and moved to the watchlist. v0.10 is now the
  hardening and reusable-page-interpretation release; v0.11 deepens layout,
  arbitrary-font creation, concurrency, and the gated `[ocr]` track. v1.0 is
  targeted no earlier than 2026-08, after product-level refinement and field
  feedback rather than as a deadline-driven API freeze.
- lopdf#535 no longer affects pylopdf since the v0.7 hayro extraction engine.
  An upstream fix remains a parallel contribution candidate.
- See [CHANGELOG.md](CHANGELOG.md) for completed history.
