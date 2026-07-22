# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.1] - 2026-07-22

### Fixed
- Removed the invalid `Topic :: Text Processing :: Markup :: PDF` classifier that
  caused PyPI to reject the 0.4.0 upload; added `Typing :: Typed`
- Added a `validate-pyproject` pre-commit hook to catch invalid metadata earlier

## [0.4.0] - 2026-07-22

### Added
- `Document.select(page_numbers)` — keep/reorder pages (pymupdf-compatible)
- CI workflow: rustfmt / clippy / ruff / mypy / pytest on Linux, macOS, and Windows
- Release workflow: abi3 wheels for manylinux (x86_64, aarch64), macOS (arm64, x86_64),
  and Windows (x64), published to PyPI via Trusted Publishing
- English README (`README.md`); Japanese version moved to `README.ja.md`

## [0.3.0] - 2026-07-22

### Added
- Page rendering via [hayro](https://github.com/LaurenzV/hayro) 0.7:
  `Document.render_page(pno, scale)` (PNG) and `Document.render_page_svg(pno)` (SVG),
  with the standard-14 font set embedded (`embed-fonts`)

## [0.2.0] - 2026-07-22

### Added
- Editing core built on [lopdf](https://github.com/J-F-Liu/lopdf) 0.44:
  open/save (path & bytes), page count, metadata read/write (UTF-16BE aware),
  page deletion, text extraction, and document merging
- pymupdf-style Python API (`Document`, `open()`) with type stubs and `py.typed`
- Page-attribute inheritance (Resources, MediaBox, CropBox, Rotate) is resolved
  and baked into pages during merge and text extraction

### Changed
- Dependencies modernized: lopdf 0.33 → 0.44, PyO3 0.22 → 0.29 (abi3-py310),
  maturin 1.14, Rust edition 2024, requires-python >= 3.10
