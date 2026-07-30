# pylopdf

[![PyPI](https://img.shields.io/pypi/v/pylopdf)](https://pypi.org/project/pylopdf/)
[![CI](https://github.com/yhay81/pylopdf/actions/workflows/ci.yml/badge.svg)](https://github.com/yhay81/pylopdf/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/pylopdf)](https://pypi.org/project/pylopdf/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/yhay81/pylopdf/blob/main/LICENSE)

**PDF editing, rendering, extraction, and generation for Python — a
pymupdf-style API on a pure-Rust core, MIT licensed, with no mandatory Python
dependencies.**

[Documentation](https://pylopdf.haya.works/) ·
[Getting started](https://pylopdf.haya.works/getting-started/) ·
[API reference](https://pylopdf.haya.works/api/) ·
[pymupdf migration guide](https://pylopdf.haya.works/migration/) ·
[Benchmarks](https://pylopdf.haya.works/benchmarks/)

pylopdf combines the 2026 Rust PDF ecosystem behind one Python API:
[lopdf](https://github.com/J-F-Liu/lopdf) for editing,
[hayro](https://github.com/LaurenzV/hayro) (the pure-Rust PDF renderer adopted
by Typst) for rendering and positioned extraction, and
[krilla](https://github.com/LaurenzV/krilla) with
[HarfRust](https://github.com/harfbuzz/harfrust) for shaped, subset-embedded
text and form appearances.

## Why pylopdf?

| | pylopdf | pymupdf | pypdf | pypdfium2 | pdf_oxide | pikepdf |
|---|---|---|---|---|---|---|
| License | **MIT** | AGPL / commercial | BSD | Apache/BSD | MIT/Apache-2.0 | MPL-2.0 |
| Wheel size (MiB) | **~5.0–5.8** | ~17.5–24.7 | small (pure Python) | ~2.7–5.0 | ~9.7–10.9 | ~1.9–4.6 |
| Editing (merge / split / rotate / outlines) | ✅ | ✅ | ✅ | limited | ✅ | ✅ (structure-focused) |
| Rendering (PNG / SVG) | ✅ | ✅ | ❌ | ✅ (PNG) | ❌ | ❌ (docs point to other tools) |
| Text extraction | ✅ (positioned text, tables, Markdown) | ✅ (advanced) | ✅ | ✅ | ✅ (advanced, table detection / Markdown) | ❌ (docs point to other tools) |
| Encryption (AES-256) | ✅ read & write | ✅ | ✅ | read only | undocumented | ✅ (via qpdf) |
| Japanese font fallback / generation | ✅ ([cjk] extra) | ✅ | — | manual | — | — |
| Implementation | **pure Rust** | C/C++ | Python | C++ (PDFium) | Rust | C++ (qpdf) |

Wheel sizes are the ranges of published files for pylopdf 0.10.0, pymupdf
1.28.0, pypdfium2 5.12.1, pdf-oxide 0.3.75, and pikepdf 10.10.0 on 2026-07-25.

- **AGPL-free**: covers the common pymupdf use cases under the MIT license
- **Small and self-contained**: fits size-constrained environments such as AWS
  Lambda and Cloudflare Workers
- **One wheel per platform**: abi3 covers CPython 3.10–3.14, plus a native
  `cp314t` wheel for free-threaded Python
- **Familiar**: the API is modeled after
  [pymupdf](https://github.com/pymupdf/PyMuPDF), with a
  [migration guide](https://pylopdf.haya.works/migration/)

## Install

```bash
pip install pylopdf
```

Optional extras:

```bash
pip install "pylopdf[cjk]"   # Noto Sans/Serif JP: render Japanese PDFs without
                             # embedded fonts, auto-subset JP text generation
pip install "pylopdf[ocr]"   # offline PP-OCRv6 recognition, pure Rust,
                             # no system executables or network at runtime
```

Building from source requires a Rust toolchain: `uv sync`.

## Quickstart

```python
import pylopdf

doc = pylopdf.open("input.pdf")              # or pylopdf.open(stream=pdf_bytes)

# Extract: plain text, positioned words, search, tables, Markdown
text = doc.get_page_text(0)
words = doc[0].get_text("words")             # (x0, y0, x1, y1, word, block, line, word_no)
hits = doc[0].search_for("tax")              # case-insensitive, list[Rect]
tables = doc[0].find_tables()                # bordered grids incl. merged cells
markdown = doc.to_markdown()                 # RAG / LLM preprocessing

# Render: PNG / SVG / raw pixels
png: bytes = doc.render_page(0, dpi=300)
batch = doc.render_pages([0, 1, 2], scale=2, workers=4)
svg: str = doc.render_page_svg(0)
pix = doc[0].get_pixmap(dpi=144)             # immutable RGBA8 for NumPy / PIL

# Edit: reorganize pages, merge documents
doc.delete_pages([1, 2])
doc.select([2, 0])                           # keep / reorder / duplicate
merged = pylopdf.Document()
merged.insert_pdf(pylopdf.open("a.pdf"))
merged.insert_pdf(pylopdf.open("b.pdf"), from_page=0, to_page=2)

# Generate: images, overlays, shaped text (CJK auto-subsets with pylopdf[cjk])
page = doc[0]
page.insert_image((72, 72, 200, 200), filename="logo.png")
page.show_pdf_page(page.rect, letterhead)    # vector overlay from another PDF
page.insert_text((40, 80), "社外秘", fontsize=20, color=(0.8, 0, 0))
page.replace_text("DRAFT", "FINAL")
page.add_highlight_annot(page.search_for("important"))

# Headers / footers / page numbers (standard-14 fonts)
for i, p in enumerate(doc):
    p.insert_text((p.rect.width - 90, p.rect.height - 30), f"Page {i + 1}", fontsize=9)

# Forms (AcroForm)
doc.set_form_field("customer", "Taro Yamada")
doc.set_form_field("agree", True)

# OCR: add a searchable text layer offline (pip install "pylopdf[ocr]")
engine = pylopdf.OcrEngine(threads=4)
page.apply_ocr(engine=engine)                # skips pages with existing text

# Save: optimized, or AES-256 encrypted
doc.save("out.pdf", garbage=True, deflate=True, object_streams=True)
doc.save("locked.pdf", user_pw="secret", permissions=pylopdf.Permissions.PRINT)

# Encrypted input and fast probing
doc = pylopdf.open("locked.pdf", password="secret")
info = pylopdf.peek_metadata("input.pdf")    # page count / encryption, no full parse
```

The [getting started guide](https://pylopdf.haya.works/getting-started/)
walks through each area; the
[API reference](https://pylopdf.haya.works/api/) documents every method,
default, and resource boundary.

## Features

- **Editing** — merge, split, select/reorder, rotate, page boxes, blank and
  copied pages, table of contents, page labels, metadata, file attachments,
  AES-256 encrypted save, atomic file replacement
- **Extraction** — plain and positioned text (`words` / `blocks` / `dict`),
  case-insensitive search, bordered and opt-in borderless table detection with
  merged-cell reconstruction, image and vector-drawing extraction, Markdown
  conversion, vertical and rotated CJK reading order
- **Rendering** — PNG and SVG via hayro, page-region clips, DPI control,
  background fills, ordered parallel `render_pages`
- **Generation** — JPEG-passthrough/PNG image insertion, vector page overlays
  (`show_pdf_page`), HarfRust-shaped `insert_text` / `insert_textbox` with
  subset embedding and UAX #14 wrapping, highlight and link annotations,
  AcroForm filling with regenerated appearances, bounded simple-font text
  replacement
- **OCR** — offline PP-OCRv6 engine (`[ocr]` extra), positioned words,
  idempotent invisible searchable layers, clockwise rotation correction
- **Untrusted input policy** — `DocumentLimits.web()` bounds file size, pages,
  objects, decompression, glyph and output budgets in one opt-in profile;
  every documented cap raises a typed `LimitError` instead of degrading
  silently ([security model](https://pylopdf.haya.works/security/))
- **WebAssembly** — a static PyEmscripten wheel runs on Cloudflare Python
  Workers, verified end-to-end every release
  ([guide](https://pylopdf.haya.works/wasm/))
- **Concurrency** — heavy operations release the GIL, distinct `Document`
  objects work in parallel, and a native `cp314t` wheel supports
  free-threaded Python 3.14
  ([contract](https://pylopdf.haya.works/concurrency/))

## Limitations

pylopdf documents its boundaries instead of guessing. Multicolumn reading
order follows deterministic whitespace-gutter rules; borderless table
detection is opt-in because aligned prose is geometrically ambiguous. CJK text
generation selects one font per run (not per-glyph fallback). Pure RTL lines
without Latin or numeric runs are restored to logical Unicode order; mixed
bidirectional paragraph layout remains in producer visual order. Ruby, warichu,
and mixed-orientation Japanese typography are not interpreted semantically.
Typesetting, PDF/A output, and digital signatures are intentionally out of
scope — see the ecosystem recipes below. The
[API stability policy](https://pylopdf.haya.works/stability/) defines
what may change before and after v1.0.

## Ecosystem recipes

pylopdf stays a lightweight core; adjacent concerns pair with established
libraries. These recipes are covered by integration tests and detailed in the
[ecosystem guide](https://pylopdf.haya.works/ecosystem/).

**Typesetting and PDF/A output —
[typst](https://typst.app/)** (via
[typst-py](https://pypi.org/project/typst/)):

```python
import typst
import pylopdf

pdf_bytes = typst.compile("report.typ")         # typesetting: typst
doc = pylopdf.open(stream=pdf_bytes)            # editing / extraction: pylopdf
pdf_a: bytes = typst.compile("report.typ", pdf_standards="a-2b")
```

**Digital signatures (PAdES) —
[pyHanko](https://pypi.org/project/pyHanko/)** (MIT), which signs with an
incremental update so pylopdf's bytes remain untouched:

```python
import io
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import signers

signer = signers.SimpleSigner.load("key.pem", "cert.pem")
out = signers.sign_pdf(
    IncrementalPdfFileWriter(io.BytesIO(doc.tobytes())),
    signers.PdfSignatureMetadata(field_name="Signature1"),
    signer=signer,
)
```

Validating existing PDFs against PDF/A remains
[veraPDF](https://verapdf.org/)'s job.

## Benchmarks

A reproducible benchmark suite ships with the repository — same corpus, same
tasks, medians, and wins and losses published as-is:
[latest results](https://github.com/yhay81/pylopdf/blob/main/bench/results/latest.md),
[free-threaded extraction](https://github.com/yhay81/pylopdf/blob/main/bench/results/free-threaded-latest.md),
and the [native OCR report](https://github.com/yhay81/pylopdf/blob/main/bench/results/ocr-latest.md)
with strict and NFKC character error rates on licensed Japanese fixtures.

```bash
uv sync --all-extras --group bench && uv run python bench/run.py
```

## Architecture

```
pylopdf.Document (Python, pymupdf-style API)
   └─ _Document (PyO3)
        ├─ lopdf 0.44   … editing: open → modify → save
        ├─ hayro 0.7    … rendering and positioned extraction
        └─ krilla 0.8 + HarfRust 0.12
                         … shaped, subset-embedded text and form appearances
```

```
rust/          # PyO3 bindings
src/pylopdf/   # Python high-level API
tests/         # pytest (Rust behavior is verified through Python tests)
```

Every release installs each published wheel — five `abi3-py310`, five CPython
3.14t, the sdist, and the PyEmscripten wheel — on architecture-matched runners
and exercises creation, saving, extraction, and rendering before publishing
with build provenance.

## Contributing

Bug reports and focused contributions are welcome. See
[CONTRIBUTING.md](https://github.com/yhay81/pylopdf/blob/main/CONTRIBUTING.md)
for development commands, test expectations, and the rules for sharing PDF
regression files:

```bash
uv sync                    # build + install dependencies
uv run pytest              # tests
uv run ruff check .        # lint
uv run mypy src tests      # type check
```

Report security vulnerabilities privately through
[GitHub Security Advisories](https://github.com/yhay81/pylopdf/security/advisories/new).

## License

[MIT](https://github.com/yhay81/pylopdf/blob/main/LICENSE). Bundled Rust
dependencies keep their own permissive licenses (lopdf and HarfRust are MIT;
hayro and krilla are MIT/Apache-2.0) — see
[NOTICE.md](https://github.com/yhay81/pylopdf/blob/main/NOTICE.md).
