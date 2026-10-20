# pylopdf

[![PyPI](https://img.shields.io/pypi/v/pylopdf)](https://pypi.org/project/pylopdf/)
[![CI](https://github.com/yhay81/pylopdf/actions/workflows/ci.yml/badge.svg)](https://github.com/yhay81/pylopdf/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/pylopdf)](https://pypi.org/project/pylopdf/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/yhay81/pylopdf/blob/main/LICENSE)

**Edit, render, extract, and create PDFs in Python.**

pylopdf follows the API of [pymupdf](https://pymupdf.readthedocs.io/), so
code and knowledge carry over, but it is built on a Rust core and released
under the MIT license. It installs as a single wheel with no required Python
dependencies.

[Documentation](https://pylopdf.haya.works/) ·
[Getting started](https://pylopdf.haya.works/getting-started/) ·
[API reference](https://pylopdf.haya.works/api/) ·
[Migrating from pymupdf](https://pylopdf.haya.works/migration/)

> **Status:** pylopdf is pre-1.0 and covers part of pymupdf's API so far.
> Breaking changes can still happen; the
> [stability policy](https://pylopdf.haya.works/stability/) explains what may
> change.

## Install

```bash
pip install pylopdf
```

Python 3.11 or newer. Wheels are available for Linux, macOS, and Windows,
including free-threaded Python 3.14t and 3.15t.

Optional extras add fonts and OCR models:

| Extra | Adds |
|---|---|
| `jp`, `ko`, `zh-cn`, `zh-tw` | Japanese, Korean, and Chinese fonts for displaying and writing text |
| `ar`, `he`, `hi`, `th` | Arabic, Hebrew, Hindi, and Thai fonts for writing text |
| `ocr` | An offline OCR engine for scanned pages |

```bash
pip install "pylopdf[jp,ocr]"
```

## Quickstart

```python
import pylopdf

doc = pylopdf.open("input.pdf")
page = doc[0]

# Extract
text = page.get_text()                 # plain text
words = page.get_text("words")         # words with their positions
hits = page.search_for("invoice")      # rectangles of each match
tables = page.find_tables()
markdown = doc.to_markdown()           # whole document as Markdown

# Render
page.get_pixmap(dpi=150).save("page.png")
svg = page.get_svg_image()

# Add content
page.insert_text((72, 72), "Approved", fontsize=20, color=(0.8, 0, 0))
page.insert_image((72, 100, 200, 200), filename="logo.png")
page.add_highlight_annot(hits)

# Reorganize pages (fetch pages again with doc[i] afterwards)
doc.delete_pages([1, 2])
doc.insert_pdf(pylopdf.open("appendix.pdf"))   # append another document

# Save
doc.save("output.pdf", garbage=True, deflate=True)
```

The [getting started guide](https://pylopdf.haya.works/getting-started/)
covers each area in more detail.

## Features

- **Pages and documents**: merge, split, reorder, rotate, and delete pages;
  table of contents, page labels, metadata, and file attachments
- **Text extraction**: plain or positioned text, search, tables, Markdown
  output, and vertical Japanese/Chinese text
- **Rendering**: pages to PNG or SVG, with parallel rendering of many pages
- **Adding content**: text in any font with proper shaping, images, vector
  drawings, pages from other PDFs, highlights, and links
- **Forms**: fill in form fields
- **OCR**: make scanned pages searchable without installing Tesseract
- **Saving**: compression, cleanup of unused objects, and AES-256 encryption

## Why pylopdf

- **MIT licensed.** pymupdf is AGPL or commercial.
- **Small and self-contained.** About 7 MB per wheel, with nothing else to
  install, so it fits AWS Lambda and similar environments. It also runs in
  WebAssembly, including [Cloudflare Python Workers](https://pylopdf.haya.works/wasm/).
- **Safe with untrusted files.** Size and complexity limits raise a clear
  `LimitError` instead of exhausting memory; `DocumentLimits.web()` enables
  all of them. See the [security model](https://pylopdf.haya.works/security/).
- **Built for concurrency.** Heavy operations release the GIL, and
  free-threaded Python is supported. See the
  [concurrency guide](https://pylopdf.haya.works/concurrency/).

## Not supported yet

Redaction, editing or deleting annotations, creating form fields, and
incremental saving are planned; the [roadmap](https://github.com/yhay81/pylopdf/blob/main/ROADMAP.md) lists when.

Typesetting, PDF/A output, and digital signatures are out of scope. The
[ecosystem guide](https://pylopdf.haya.works/ecosystem/) shows how to pair
pylopdf with [typst](https://typst.app/) and
[pyHanko](https://pypi.org/project/pyHanko/) for those.

## Benchmarks

Benchmarks against pymupdf are reproducible from this repository, and the
results include the cases where pylopdf is slower. See the
[benchmark page](https://pylopdf.haya.works/benchmarks/).

## Contributing

Bug reports and pull requests are welcome. Building from source needs a Rust
toolchain and [uv](https://docs.astral.sh/uv/):

```bash
uv sync
uv run pytest
```

[CONTRIBUTING.md](https://github.com/yhay81/pylopdf/blob/main/CONTRIBUTING.md) has the full development guide. Please
report security issues privately through
[GitHub Security Advisories](https://github.com/yhay81/pylopdf/security/advisories/new).

## License

[MIT](https://github.com/yhay81/pylopdf/blob/main/LICENSE). pylopdf is built on [lopdf](https://github.com/J-F-Liu/lopdf),
[hayro](https://github.com/LaurenzV/hayro),
[krilla](https://github.com/LaurenzV/krilla), and
[HarfRust](https://github.com/harfbuzz/harfrust); see [NOTICE.md](https://github.com/yhay81/pylopdf/blob/main/NOTICE.md)
for third-party licenses.
