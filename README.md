# pylopdf

[![PyPI](https://img.shields.io/pypi/v/pylopdf)](https://pypi.org/project/pylopdf/)
[![CI](https://github.com/yhay81/pylopdf/actions/workflows/ci.yml/badge.svg)](https://github.com/yhay81/pylopdf/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/pylopdf)](https://pypi.org/project/pylopdf/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

[日本語版 README はこちら](README.ja.md)

PDF editing and rendering for Python, powered by Rust — [lopdf](https://github.com/J-F-Liu/lopdf) for editing and [hayro](https://github.com/LaurenzV/hayro) (the pure-Rust PDF renderer adopted by typst) for rendering.

**MIT licensed, zero runtime dependencies, lightweight wheels.** Covers the common pymupdf use cases without the AGPL.

## Why pylopdf?

| | pylopdf | pymupdf | pypdf | pypdfium2 |
|---|---|---|---|---|
| License | **MIT** | AGPL / commercial | BSD | Apache/BSD |
| Wheel size | **~3.5 MB** | ~40 MB+ | small (pure Python) | ~8 MB |
| Editing (merge / split / rotate / outlines) | ✅ | ✅ | ✅ | limited |
| Rendering (PNG / SVG) | ✅ | ✅ | ❌ | ✅ (PNG) |
| Text extraction | ✅ (basic) | ✅ (advanced) | ✅ | ✅ |
| Encryption (AES-256) | ✅ read & write | ✅ | ✅ | ❌ |
| CJK font fallback | ✅ ([cjk] extra) | ✅ | — | manual |
| Implementation | **pure Rust** | C | Python | C++ (PDFium) |

- Fits size-constrained environments such as AWS Lambda
- Safe for commercial projects that need to avoid the AGPL
- abi3: one wheel covers Python 3.10–3.14
- API modeled after [pymupdf](https://github.com/pymupdf/PyMuPDF)

**Limitations**: no precise layout analysis, no annotation/form editing. Use pymupdf if you need those.

## Install

```bash
pip install pylopdf
```

To render Japanese PDFs without embedded fonts, install the optional CJK fonts
(Noto Sans/Serif JP, auto-detected at render time):

```bash
pip install pylopdf[cjk]
```

Building from source (requires a Rust toolchain):

```bash
uv sync
```

## Usage

```python
import pylopdf

# Open from a path or bytes
doc = pylopdf.open("input.pdf")
doc = pylopdf.open(stream=pdf_bytes)

# Page count
print(doc.page_count)  # same as len(doc)

# Metadata
print(doc.metadata["title"])
doc.set_metadata({"title": "Monthly Report", "author": "Alice"})

# Text extraction (0-based page numbers)
text = doc.get_page_text(0)

# Rendering
png: bytes = doc.render_page(0)             # 72 dpi
png2x: bytes = doc.render_page(0, scale=2)  # 144 dpi
png300 = doc.render_page(0, dpi=300)        # by resolution
png_bg = doc.render_page(0, background=(255, 255, 255))  # white background (default: transparent)
svg: str = doc.render_page_svg(0)

# Delete pages (split)
doc.delete_page(0)
doc.delete_pages([1, 2])

# Keep/reorder pages (repeating a page duplicates it)
doc.select([2, 0])

# Page objects (0-based; negative counts from the end)
page = doc[0]
for page in doc:
    print(page.number, page.rect)
page.set_rotation(90)                # display rotation (multiples of 90)
page.set_mediabox((0, 0, 300, 400))  # page boxes

# Insert / copy pages
doc.new_page()          # blank A4 appended
doc.copy_page(0, to=1)  # duplicate page 0 in front of page 1

# Table of contents (page numbers are 1-based here, pymupdf-compatible)
doc.set_toc([[1, "Chapter 1", 1], [2, "Section 1.1", 2]])
print(doc.get_toc())

# Merge (with ranges, reversed order, and an insertion position)
merged = pylopdf.Document()
merged.insert_pdf(pylopdf.open("a.pdf"))
merged.insert_pdf(pylopdf.open("b.pdf"), from_page=0, to_page=2, start_at=0)

# Save
merged.save("merged.pdf")
data: bytes = merged.tobytes()

# Optimized save (prune unreferenced objects + compress + object streams)
merged.save("small.pdf", garbage=True, deflate=True, object_streams=True)

# Encrypted save (AES-256; owner_pw alone = open freely, restricted permissions)
merged.save("locked.pdf", user_pw="secret", permissions=pylopdf.Permissions.PRINT)

# Fast metadata probe without parsing the whole file
info = pylopdf.peek_metadata("input.pdf")
print(info["title"], info["page_count"], info["encrypted"])

# Context manager
with pylopdf.open("input.pdf") as doc:
    print(doc.metadata)

# Encrypted PDFs (RC4-40/128, AES-128, AES-256; empty user passwords open transparently)
doc = pylopdf.open("locked.pdf", password="secret")
doc = pylopdf.open("locked.pdf")
if doc.needs_pass:
    doc.authenticate("secret")  # 0=failed, 2=user, 4=owner, 6=both

# CJK fallback font for PDFs without embedded fonts
# (automatic with pylopdf[cjk]; or bring your own font)
doc.set_fallback_font("NotoSansJP-Regular.otf")
doc.set_fallback_font(font_bytes, kind="serif")
```

## API

`pylopdf.Document` (`pylopdf.open()` is an alias constructor):

| Method / property | Description |
|---|---|
| `Document(filename=None, stream=None, password=None, max_decompressed_size=None)` | Open from a path or bytes; empty document if both are None. `max_decompressed_size` guards against decompression bombs |
| `doc[i]` / `load_page(pno)` / `for page in doc` | Get a Page view (negative indices count from the end; re-fetch after structural changes) |
| `needs_pass` / `is_encrypted` | Encryption status (pymupdf-compatible semantics) |
| `authenticate(password)` | Decrypt with a password (returns 0/1/2/4/6, pymupdf-compatible) |
| `page_count` / `len(doc)` | Number of pages |
| `metadata` | Metadata dict (title, author, subject, keywords, creator, producer, creationDate, modDate, format) |
| `set_metadata(dict)` | Set metadata (empty string deletes the entry) |
| `get_page_text(pno)` | Extract text from a page |
| `render_page(pno, scale=1.0, dpi=None, background=None)` | Render a page to PNG bytes; `dpi` replaces `scale`, `background` is an RGB(A) fill (max 65,535 px per side / 64 MP total) |
| `render_page_svg(pno)` | Render a page to an SVG string |
| `set_fallback_font(font, kind="sans", index=0)` | Set a fallback font (path/bytes) for non-embedded CJK fonts; `None` disables auto-detection |
| `select(page_numbers)` | Keep only the given pages, in the given order (repeats duplicate the page) |
| `delete_page(pno)` / `delete_pages(iterable)` | Delete pages |
| `insert_pdf(other, from_page=0, to_page=-1, start_at=-1)` | Merge a page range (negative / reversed ranges; `start_at` sets the insertion position) |
| `new_page(pno=-1, width=595, height=842)` / `copy_page(pno, to=-1)` | Insert a blank page / duplicate a page |
| `get_toc()` / `set_toc(toc)` | Read/write outlines as `[[level, title, page], ...]` (page numbers are 1-based here) |
| `save(filename, garbage=, deflate=, object_streams=, user_pw=, owner_pw=, permissions=)` / `tobytes(same)` | Save; prune / compress / object streams, or AES-256 encryption via `user_pw` / `owner_pw` (the in-memory document stays plain) |
| `close()` | Close (supports `with`) |

`pylopdf.Page` (obtained via `doc[i]`):

| Method / property | Description |
|---|---|
| `number` / `parent` | 0-based page number and owning Document |
| `get_text()` / `render(scale, dpi=, background=)` / `render_svg()` | Extraction and rendering |
| `rotation` / `set_rotation(deg)` | Display rotation (multiples of 90, inheritance-resolved) |
| `mediabox` / `cropbox` / `rect` | Page boxes (`Rect`); `rect` is the rotation-aware visible rectangle |
| `set_mediabox(rect)` / `set_cropbox(rect)` | Set page boxes |

Module level:

| Name | Description |
|---|---|
| `peek_metadata(filename/stream, password=None)` | Fast metadata / page-count / encryption probe without parsing the whole file |
| `Permissions` | Encryption permission flags (IntFlag) |
| `Rect` | Rectangle NamedTuple with `width` / `height` |
| Exceptions | `PdfError` (ValueError-compatible base), `PasswordError`, `DocumentClosedError`, `EncryptedDocumentError`, `StalePageError` |

For low-level access, use `pylopdf.pylopdf_core._Document` (a thin lopdf wrapper) directly.

## Architecture

Follows the division of labor in the 2026 Rust PDF ecosystem:

```
pylopdf.Document (Python, pymupdf-style API)
   └─ _Document (PyO3)
        ├─ lopdf 0.44   … editing: open → modify → save
        └─ hayro 0.7    … rendering: PNG / SVG (standard fonts embedded)
```

```
rust/          # PyO3 bindings
src/pylopdf/   # Python high-level API
tests/         # pytest (Rust behavior is verified through Python tests)
```

```bash
uv sync                    # build + install dependencies
uv run pytest              # tests
uv run ruff check .        # lint
uv run mypy src tests      # type check
uv build --wheel           # build a wheel
```

`uv sync` detects Rust source changes and rebuilds automatically (via `tool.uv.cache-keys`).

## License

MIT (lopdf is MIT; hayro is MIT/Apache-2.0)
