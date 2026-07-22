# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.8.0] - 2026-07-23

### Added
- `Page.insert_image(rect, filename=/stream=, keep_proportion=, overlay=)`:
  draw a JPEG (embedded as-is, no recompression) or PNG (decoded, alpha kept as
  a soft mask) into a display-space rect — the same top-left coordinate system
  as `search_for` / `get_text`, so search hits can be stamped directly. Existing
  page content is never re-encoded: drawing only appends new content streams
  (the existing stream list is wrapped once in `q`/`Q` to isolate its graphics
  state). Rotated pages take display coordinates too
- `Page.show_pdf_page(rect, src, pno=0, keep_proportion=, overlay=)`: overlay a
  page from another document as a Form XObject — text and vectors stay intact
  (extractable afterwards), fonts stay embedded. Source rotation and CropBox are
  resolved so the page lands in the rect exactly as displayed. This is the
  universal adapter for the ecosystem recipes: a one-page stamp typeset with
  typst (e.g. a Japanese watermark using the pylopdf-fonts-cjk fonts via
  `font_paths`) burns onto every page as vectors, covered by an integration test
- `Page.replace_text(search, replacement, default_char=None)`: thin wrapper over
  lopdf's `replace_partial_text` returning the replacement count. Simple-encoded
  fonts only (no CID/CJK); page attributes are baked first so inherited
  Resources work
- Annotations: `Page.annots()` reads `{"type", "rect", "contents", "uri"}` dicts
  (rect in display coordinates, rotation-aware); `Page.add_highlight_annot(rects,
  color=, opacity=, content=)` highlights one or more rects — `search_for`
  results feed in directly ("search & mark"). QuadPoints use the Acrobat zigzag
  convention AND an appearance stream (Form XObject with Multiply blend) is
  always generated, because hayro (and thus pylopdf's own rendering) only draws
  annotations that carry an /AP — pixel-verified in tests, including rotated
  pages; `Page.add_link_annot(rect, uri)` adds a borderless URI link
- `Page.insert_text(point, text, fontsize=, fontname=, color=)`: print text with
  a PDF standard-14 font (pymupdf-style abbreviations "helv" / "tiro" / "cour" /
  bold-italic variants / "symb" / "zadb"; nothing is embedded). WinAnsi range
  only — CJK input raises with a pointer to the typst + `show_pdf_page` recipe.
  `\n` makes multiple lines (1.2 × fontsize leading); text stays upright on
  rotated pages via the display-space text matrix. Headers / footers / page
  numbers / Bates stamps are a documented loop over pages
- Ecosystem interop recipes, documented in both READMEs and guarded by
  integration tests (`tests/test_interop.py`, optional `interop` dependency
  group installed in CI): typesetting and PDF/A output for new documents via
  typst (`typst.compile()` bytes feed straight into `pylopdf.open(stream=)`),
  and PAdES signatures via pyHanko (incremental signing keeps pylopdf's output
  bytes untouched as a prefix — asserted byte-for-byte). veraPDF is documented
  as the external answer for PDF/A validation

## [0.7.0] - 2026-07-23

### Added
- Positioned text extraction: `Page.get_text(option)` / `Document.get_page_text(pno,
  option)` accept pymupdf-style `"words"` (8-tuples with bbox + block/line/word
  numbers), `"blocks"`, and `"dict"` (blocks → lines → spans with bboxes, sizes,
  origins) in addition to the default `"text"`. Coordinates are top-left origin;
  vertical extents are approximated from the font size (not real font metrics)
- `Page.search_for(needle)`: case-insensitive text search returning `list[Rect]`,
  including matches across word gaps and CJK text (works even for non-embedded
  CJK fonts, since Unicode comes from the CMap machinery). Line-spanning matches
  are not detected
- `Page.get_pixmap(scale, dpi=, background=)`: renders to a `Pixmap` object with
  straight-alpha RGBA8 pixels (`samples` bytes plus `width` / `height` / `stride`
  / `n` and `tobytes()` for PNG), ready for
  `np.frombuffer(pix.samples, np.uint8).reshape(h, w, 4)`. The buffer protocol
  (zero-copy) is not implemented because `Py_buffer` only joined the stable ABI
  in Python 3.11 while our wheels are abi3-py310; `samples` costs one copy
- Interpreter warnings surface as Python warnings: font-resolution and
  image-decode failures reported by hayro during rendering or extraction are
  emitted as `PylopdfWarning` (deduplicated per operation, cleared between
  operations)
- `Page.get_images()`: extracts images drawn on the page as
  `{"width", "height", "bbox", "ext", "image"}` dicts. Images whose filter chain
  ends in DCTDecode (including `[FlateDecode, DCTDecode]`) return the original
  JPEG bytes unmodified (verified against the JPEG magic, no recompression);
  everything else (CCITT / JBIG2 / Flate / stencils) is decoded and re-encoded
  as PNG. `bbox` is the drawn position on the page (top-left origin)

### Changed
- Text extraction now runs on a hayro-based engine (`rust/src/extract.rs`): the
  interpreter collects per-glyph Unicode + positions and assembles them into
  reading order (top-to-bottom, left-to-right with word-gap detection). This
  fixes two known limits at once — content streams with `%` comments
  ([lopdf#535](https://github.com/J-F-Liu/lopdf/issues/535)) and non-embedded
  CJK fonts via predefined CMaps (90ms-RKSJ-H etc.) both extract correctly now —
  and covers invisible text (OCR layers) explicitly. Extraction no longer
  mutates the document (the inherited-attribute baking step became unnecessary).
  Vertical writing order is not reconstructed yet

## [0.6.0] - 2026-07-23

### Added
- Page views: `doc[i]` (negative indices too), iteration, and `load_page` return
  a `Page` with `number` / `parent`, `rotation` / `set_rotation`, `mediabox` /
  `cropbox` / `rect` (inheritance-resolved; `rect` is rotation-aware),
  `set_mediabox` / `set_cropbox`, `get_text`, `render`, and `render_svg`.
  Structural changes invalidate previously obtained pages (`StalePageError`),
  matching pymupdf's re-fetch semantics
- Page operations: `insert_pdf(other, from_page=, to_page=, start_at=)` merges
  ranges (negative / reversed) at an insertion position, `new_page(pno, width,
  height)` inserts a blank page, `copy_page(pno, to=)` duplicates a page, and
  repeating a page number in `select` now duplicates it instead of raising
- Table of contents: `get_toc()` / `set_toc()` with pymupdf-compatible
  `[[level, title, 1-based page], ...]` lists; non-ASCII titles are written as
  UTF-16BE; an empty list removes the outline
- Encrypted saving: `save` / `tobytes` accept `user_pw` / `owner_pw` /
  `permissions` and write AES-256 (PDF 2.0, V5/R6) output while the in-memory
  document stays unencrypted; `Permissions` IntFlag exported. The 256-bit file
  key comes from `os.urandom`
- Typed exceptions: `PdfError` (ValueError-compatible base), `PasswordError`
  (wrong/missing password), `DocumentClosedError`, `EncryptedDocumentError`,
  and `StalePageError`; existing `except ValueError` code keeps working
- `peek_metadata()`: metadata / page-count / encryption probe that does not
  parse the whole document (for scanning large collections), and
  `max_decompressed_size=` on `Document` / `open` bounding per-stream
  decompression (bomb protection)
- `render_page(pno, scale=1.0, *, dpi=None, background=None)`: resolution-based
  sizing via `dpi` (alternative to `scale`; combining both raises) and an RGB(A)
  `background` fill color (rendering stays transparent by default)
- Save options on `save` / `tobytes`: `garbage=` (prune unreferenced objects),
  `deflate=` (compress streams), and `object_streams=` (write object streams +
  cross-reference streams in PDF 1.5+ form; 13% smaller on the already-compressed
  110-page corpus document, more on object-heavy files)
- Scanned-PDF coverage in the real-world corpus: `patent-us223898.pdf`
  (Edison's electric-lamp patent, 1880, public domain) exercising CCITTFaxDecode
  images and an OCR text layer, and `wdl6812-manuscript.pdf` (World Digital
  Library illuminated manuscript, public domain) exercising DCTDecode + JBIG2Decode
  color scans with no text layer
- `ROADMAP.md`: mid-term plan (strategy, v0.6–v1.0 themes, explicit non-goals)
  based on the 2026-07 survey of lopdf, hayro, and the Python PDF ecosystem

### Changed
- Rendering now caches the parsed hayro document and invalidates it on edits,
  instead of re-serializing and re-parsing the whole document on every page
  render (hayro parses lazily, so the win is small for typical files and grows
  with document size; the cached view is also the groundwork for the planned
  hayro-based text extraction)
- Heavy operations (load, save, render, text extraction, merge, compression)
  release the GIL; concurrent rendering on two threads now scales near-linearly
  (measured 1.9x) where it previously serialized
- The content-stream comment bug behind the pdf20 empty-extraction xfail is now
  reported upstream as [lopdf#535](https://github.com/J-F-Liu/lopdf/issues/535)

### Fixed
- Built a valid Catalog and empty page tree for newly created documents, so
  saving a zero-page document no longer emits a PDF without a trailer `/Root`
- Recomputed page-tree `/Count` from reachable pages when appending, repairing
  stale counts in input PDFs instead of propagating them to merged output
- Validated complete metadata updates before applying them, preventing partial
  changes when a later key or value is invalid
- Made `validate-pyproject` UTF-8-safe on Windows, enabled complete dependency
  validation, and added the metadata check to CI
- Prevented object-ID collisions when inserting real-world PDFs into an empty
  document, including the empty-source edge case
- Rejected cyclic inherited page parents instead of hanging indefinitely
- Bounded PNG rendering to finite positive scales, 65,535 pixels per side, and
  64 million total pixels to avoid unbounded allocations
- Decoded metadata with the PDF-standard PDFDocEncoding mapping
- Kept `needs_pass` false for PDFs whose empty user password requires no
  authentication, regardless of the supplied `password` argument
- Enforced closed/encrypted document checks for empty `delete_pages([])` and
  `select([])` calls

## [0.5.0] - 2026-07-22

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
