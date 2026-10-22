# pylopdf

[![PyPI](https://img.shields.io/pypi/v/pylopdf)](https://pypi.org/project/pylopdf/)
[![CI](https://github.com/yhay81/pylopdf/actions/workflows/ci.yml/badge.svg)](https://github.com/yhay81/pylopdf/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/pylopdf)](https://pypi.org/project/pylopdf/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/yhay81/pylopdf/blob/main/LICENSE)

**Edit, render, extract, and create PDFs in Python.**

pylopdf aims to do everything that [pymupdf](https://pymupdf.readthedocs.io/)
does with PDF files through a smaller API of its own. It is built on the Rust
PDF engines [lopdf](https://github.com/J-F-Liu/lopdf),
[hayro](https://github.com/LaurenzV/hayro), and
[krilla](https://github.com/LaurenzV/krilla), released under the MIT license,
and installs as a single wheel with no required Python dependencies.

[Documentation](https://pylopdf.haya.works/) ·
[Getting started](https://pylopdf.haya.works/getting-started/) ·
[API reference](https://pylopdf.haya.works/api/) ·
[Migrating from pymupdf](https://pylopdf.haya.works/migration/)

> **Status:** pylopdf is pre-1.0, and the next release redesigns its API, as
> the [roadmap](https://github.com/yhay81/pylopdf/blob/main/ROADMAP.md)
> describes. The [stability policy](https://pylopdf.haya.works/stability/)
> explains what may change.

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
page = doc.pages[0]

# Extract
text = page.text()                     # plain text
words = page.words()                   # words with their positions
hits = page.search("invoice")          # rectangles of each match
tables = page.find_tables()
markdown = doc.markdown()              # whole document as Markdown

# Render
page.get_pixmap(dpi=150).save("page.png")
svg = page.get_svg_image()

# Add content
page.insert_text((72, 72), "Approved", fontsize=20, color=(0.8, 0, 0))
page.insert_image((72, 100, 200, 200), filename="logo.png")
page.add_highlight_annot(hits)

# Reorganize pages (fetch pages again with doc.pages[i] afterwards)
del doc.pages[1:3]
doc.pages.extend(pylopdf.open("appendix.pdf").pages)   # append another document

# Save, compressed and without unused objects
doc.save("output.pdf")
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

Redaction, editing or deleting annotations, creating form fields,
incremental saving, and validated PDF/A and PDF/UA output are planned; the
[roadmap](https://github.com/yhay81/pylopdf/blob/main/ROADMAP.md) lists them.

Typesetting with HTML and CSS and digital signatures stay with ecosystem
tools. The [ecosystem guide](https://pylopdf.haya.works/ecosystem/) shows how
to pair pylopdf with [typst](https://typst.app/) and
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
