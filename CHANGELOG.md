# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Encrypted PDF reading: `password` argument on `Document`/`open`, `needs_pass` /
  `is_encrypted` properties, and pymupdf-compatible `authenticate(password)`
  (0=failed / 1=not needed / 2=user / 4=owner / 6=both). Supports RC4-40/128,
  AES-128, and AES-256 (R6); PDFs with an empty user password keep opening
  transparently. Operating on a still-encrypted document now raises a clear
  ValueError instead of silently appearing to have 0 pages
- CJK fallback fonts for rendering: `Document.set_fallback_font(font, kind, index)`
  supplies a TTF/OTF/TTC for non-embedded CID fonts (detected via CIDSystemInfo or
  BaseFont name; Mincho-like names pick the "serif" slot). The new optional extra
  `pylopdf[cjk]` installs `pylopdf-fonts-cjk` (Noto Sans/Serif JP, SIL OFL 1.1,
  built from `fonts/pylopdf-fonts-cjk/` in this repo) which is auto-detected at
  render time, so non-embedded Japanese PDFs render out of the box
- Real-world PDF regression test suite (`tests/test_real_world.py`) with a vendored
  redistributable corpus (`tests/assets/real_world/`, ~1.4 MB) covering PDF 1.5/1.7/2.0,
  AcroForm, CJK embedded CID fonts, and a 110-page document; each file's source and
  license are documented in the corpus README, and known lopdf limits are tracked via
  strict xfail
- Encrypted-PDF test fixtures (`tests/assets/encrypted/`, regenerable via `generate.py`)

### Fixed
- Corrected the recorded root cause of the empty text extraction on the PDF 2.0
  sample: lopdf fails on `%` comments inside content streams (fonts without
  /Encoding decode fine via the StandardEncoding fallback)

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
