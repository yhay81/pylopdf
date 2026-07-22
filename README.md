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
| Editing (merge / split / metadata) | ✅ | ✅ | ✅ | limited |
| Rendering (PNG / SVG) | ✅ | ✅ | ❌ | ✅ (PNG) |
| Text extraction | ✅ (basic) | ✅ (advanced) | ✅ | ✅ |
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
svg: str = doc.render_page_svg(0)

# Delete pages (split)
doc.delete_page(0)
doc.delete_pages([1, 2])

# Keep/reorder pages
doc.select([2, 0])

# Merge
merged = pylopdf.Document()
merged.insert_pdf(pylopdf.open("a.pdf"))
merged.insert_pdf(pylopdf.open("b.pdf"))

# Save
merged.save("merged.pdf")
data: bytes = merged.tobytes()

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
| `Document(filename=None, stream=None, password=None)` | Open from a path or bytes; empty document if both are None |
| `needs_pass` / `is_encrypted` | Encryption status (pymupdf-compatible semantics) |
| `authenticate(password)` | Decrypt with a password (returns 0/1/2/4/6, pymupdf-compatible) |
| `page_count` / `len(doc)` | Number of pages |
| `metadata` | Metadata dict (title, author, subject, keywords, creator, producer, creationDate, modDate, format) |
| `set_metadata(dict)` | Set metadata (empty string deletes the entry) |
| `get_page_text(pno)` | Extract text from a page |
| `render_page(pno, scale=1.0)` | Render a page to PNG bytes (max 65,535 px per side / 64 MP total) |
| `render_page_svg(pno)` | Render a page to an SVG string |
| `set_fallback_font(font, kind="sans", index=0)` | Set a fallback font (path/bytes) for non-embedded CJK fonts; `None` disables auto-detection |
| `select(page_numbers)` | Keep only the given pages, in the given order |
| `delete_page(pno)` / `delete_pages(iterable)` | Delete pages |
| `insert_pdf(other)` | Append all pages of another document |
| `save(filename)` / `tobytes()` | Save |
| `close()` | Close (supports `with`) |

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
