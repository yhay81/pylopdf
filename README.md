# pylopdf

[![PyPI](https://img.shields.io/pypi/v/pylopdf)](https://pypi.org/project/pylopdf/)
[![CI](https://github.com/yhay81/pylopdf/actions/workflows/ci.yml/badge.svg)](https://github.com/yhay81/pylopdf/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/pylopdf)](https://pypi.org/project/pylopdf/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/yhay81/pylopdf/blob/main/LICENSE)

**PDF editing, rendering, extraction, and generation for Python — pymupdf's
API names and meaning on a pure-Rust core, MIT licensed, with no mandatory
Python dependencies.**

[Documentation](https://pylopdf.haya.works/) ·
[Getting started](https://pylopdf.haya.works/getting-started/) ·
[API reference](https://pylopdf.haya.works/api/) ·
[pymupdf migration guide](https://pylopdf.haya.works/migration/) ·
[Benchmarks](https://pylopdf.haya.works/benchmarks/) ·
[Roadmap](https://github.com/yhay81/pylopdf/blob/main/ROADMAP.md)

pylopdf combines the 2026 Rust PDF ecosystem behind one Python API:
[lopdf](https://github.com/J-F-Liu/lopdf) for editing,
[hayro](https://github.com/LaurenzV/hayro) (the pure-Rust PDF renderer adopted
by Typst) for rendering and positioned extraction, and
[krilla](https://github.com/LaurenzV/krilla) with
[HarfRust](https://github.com/harfbuzz/harfrust) for shaped, subset-embedded
text and form appearances.

> **Status: pre-1.0.** pylopdf is working toward
> [parity with pymupdf](https://github.com/yhay81/pylopdf/blob/main/ROADMAP.md)
> for PDF documents. Its rule is one API in which a pymupdf name keeps
> pymupdf's meaning and a redesign takes a new name, with no aliases. One
> breaking release before 1.0 brings the existing API in line: for example,
> `render_page` gave way to pymupdf's `get_page_pixmap`, `render_page_svg`
> and `Page.render_svg` to `Page.get_svg_image`, and `add_link_annot` to
> `insert_link`. The [stability policy](https://pylopdf.haya.works/stability/)
> says what may change.

## How pylopdf compares

### Packaging

| | pylopdf | pymupdf | pypdf | pypdfium2 | pikepdf | pdf_oxide |
|---|---|---|---|---|---|---|
| License | **MIT** | AGPL-3.0 or commercial | BSD-3-Clause | Apache-2.0 / BSD-3-Clause | MPL-2.0 | MIT or Apache-2.0 |
| Core | Rust (lopdf, hayro, krilla) | C (MuPDF) | pure Python | C++ (PDFium) | C++ (qpdf) | Rust |
| Wheel size (MiB) | 6.8–7.9 | 60.1–65.7 | 0.4 | 2.8–5.1 | 2.0–4.2 | 10.0–11.2 |
| Required dependencies | **none** | 8, including onnxruntime and numpy | none | none | Pillow, lxml, packaging | none |
| Free-threaded wheels | cp314t, cp315t | — | pure Python | pure Python (ctypes) | cp314t, cp315t | — |

Published files on PyPI on 2026-10-20: pylopdf 0.14.0, pymupdf 2.0, pypdf
6.21.1, pypdfium2 5.14.0, pikepdf 10.17.0, and pdf-oxide 0.3.78. Ranges span
each project's platform wheels. pylopdf's `cp315t` wheels and its hayro 0.8
upgrade, which grew the abi3 wheel by about 4%, are on `main` and not yet
released. Feature lists change quickly between releases, so this table states
packaging facts only; compare features in each project's documentation.

### Against pymupdf, measured

pymupdf covers every row below; the table shows where pylopdf stands. The
[parity manifest](https://github.com/yhay81/pylopdf/blob/main/api/pymupdf-parity.json)
classifies all 962 public names of pymupdf 2.0, and CI checks it against
pylopdf's public API: 201 of the 763 names in scope are available (26.3%), 562
are planned for a roadmap milestone, and 199 are non-goals with an ecosystem
answer. `python tools/check_parity.py` prints the report per pymupdf class.

| Area | pylopdf today | Not yet (milestone) |
|---|---|---|
| Pages and documents | ✅ merge, split, select, rotate, page boxes, labels, TOC, metadata, attachments, AES-256 | TOC item editing, viewer preferences, Art/Bleed/TrimBox (M5) |
| Text extraction | ✅ `text` / `words` / `blocks` / `dict` / `rawdict`, search, tables, Markdown | span colour and flags, `clip=`, html / xml / json output (M1) |
| Rendering | ✅ PNG, SVG, clips, ordered parallel batches | arbitrary matrix, grayscale, JPEG output, `annots=False` (M0, M1) |
| Drawing and text generation | ✅ `Shape` and `draw_*`, shaped and subset-embedded `insert_text` / `insert_textbox`, images, page overlays | rotated, translucent, and outlined text; images to PDF (M2); HTML text boxes (M8) |
| Annotations | ◐ create Highlight and URI Link | other types, editing, deletion, flattening (M3) |
| Forms | ◐ fill fields with regenerated appearances | field creation, deletion, properties, reset, flattening (M3) |
| Redaction | — | `apply_redactions`, `scrub` (M4) |
| Low-level objects | ✅ a typed object model in place of `xref_*` | XMP metadata, optional-content layers (M5) |
| Saving | ✅ `garbage` levels 0–4, object streams, encryption | incremental save, font subsetting, damaged xref-stream recovery (M7) |
| OCR and LLM output | ✅ native offline OCR, `to_markdown` | OCR text pages, page chunks, layout analysis (M6) |
| XPS, EPUB, HTML input | non-goal | typst or a dedicated converter |

### Beyond pymupdf

- **MIT for everything**, layout analysis included when it lands; pymupdf is
  AGPL or commercial
- **Small and self-contained**: one wheel without Python dependencies fits
  size-constrained environments such as AWS Lambda and Cloudflare Workers
- **Bounded on untrusted input**: every documented cap raises a typed
  `LimitError` instead of degrading silently, and `DocumentLimits.web()`
  enables the complete profile ([security model](https://pylopdf.haya.works/security/))
- **Concurrency contract**: heavy operations release the GIL, and native
  `cp314t` and `cp315t` wheels support free-threaded Python
  ([contract](https://pylopdf.haya.works/concurrency/))
- **WebAssembly**: a PyEmscripten wheel runs on Cloudflare Python Workers
  ([guide](https://pylopdf.haya.works/wasm/))
- **OCR without system tools**: PP-OCRv6 runs in pure Rust, with no Tesseract
  install and no network at runtime
- **One wheel per platform**: abi3 covers CPython 3.11–3.15

## Install

```bash
pip install pylopdf
```

Optional extras:

```bash
pip install "pylopdf[jp]"    # Japanese rendering fallback and text generation
pip install "pylopdf[ko]"    # Korean
pip install "pylopdf[zh-cn]" # Simplified Chinese
pip install "pylopdf[zh-tw]" # Traditional Chinese
pip install "pylopdf[ar]"    # Arabic generation (also: he, hi, th)
pip install "pylopdf[ocr]"   # offline PP-OCRv6 recognition, pure Rust,
                             # no system executables or network at runtime
```

The locale-specific CJK extras provide both non-embedded PDF fallback and
generation fonts. The `ar`, `he`, `hi`, and `th` extras provide generation
fonts for Arabic, Hebrew, Devanagari, and Thai. Choose `jp`, `ko`, `zh-cn`, or
`zh-tw` explicitly for the required regional glyph conventions; there is no
generic CJK font extra.

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
png: bytes = doc.get_page_pixmap(0, dpi=300).tobytes()
batch = doc.render_pages([0, 1, 2], dpi=144, workers=4)
svg: str = doc[0].get_svg_image()
pix = doc[0].get_pixmap(dpi=144)             # immutable RGB pixels for NumPy / PIL

# Edit: reorganize pages, merge documents
doc.delete_pages([1, 2])
doc.select([2, 0])                           # keep / reorder / duplicate
merged = pylopdf.Document()
merged.insert_pdf(pylopdf.open("a.pdf"))
merged.insert_pdf(pylopdf.open("b.pdf"), from_page=0, to_page=2)

# Generate: images, overlays, shaped subset-embedded multilingual text
page = doc[0]
page.insert_image((72, 72, 200, 200), filename="logo.png")
page.show_pdf_page(page.rect, letterhead)    # vector overlay from another PDF
page.insert_text((40, 80), "社外秘", fontsize=20, color=(0.8, 0, 0))
page.insert_text((40, 120), "简体中文", fontsize=20, font_language="zh-CN")
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
doc.save("out.pdf", garbage=True, deflate=True, use_objstms=True)
doc.save(
    "locked.pdf",
    encryption=pylopdf.PDF_ENCRYPT_AES_256,
    user_pw="secret",
    permissions=pylopdf.PDF_PERM_PRINT,
)

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
  AcroForm fields carried along by `insert_pdf` and pruned with deleted pages,
  atomic file replacement
- **Extraction** — plain and positioned text (`words` / `blocks` / `dict` /
  `rawdict`), case-insensitive search, bordered and opt-in borderless table
  detection with merged-cell reconstruction, image and vector-drawing
  extraction, Markdown conversion, vertical and rotated CJK reading order
- **Rendering** — PNG and SVG via hayro, page-region clips, DPI control,
  background fills, ordered parallel `render_pages`
- **Generation** — JPEG-passthrough/PNG image insertion, vector page overlays
  (`show_pdf_page`), HarfRust-shaped `insert_text` / `insert_textbox` with
  subset embedding and UAX #14 wrapping, reusable `Font` faces whose
  `text_length` is the drawn width, pymupdf's `Shape` and `draw_*` vector
  drawing that embeds each font once per commit, highlight and link annotations,
  AcroForm filling with regenerated appearances, bounded simple-font text
  replacement; repeated images and page overlays reuse one embedded copy
- **Saving** — pymupdf's `garbage` levels 0–4, where 3 and 4 merge identical
  objects and streams, object and cross-reference streams, AES-256 encryption
- **Low-level access** — `doc.objects`, `doc.trailer`, `doc.catalog`, and
  `page.obj` expose the object graph as typed Python values in place of
  pymupdf's string-based `xref_*` methods
- **Geometry** — immutable `Point` and `Matrix` values with pymupdf's
  arithmetic; `Rect` algebra, `IRect`, and `Quad` follow
- **OCR** — offline PP-OCRv6 engine (`[ocr]` extra), positioned words,
  idempotent invisible searchable layers, clockwise rotation correction

## Limitations

pylopdf documents its boundaries instead of guessing. Redaction, annotation
editing, form-field creation, and incremental save are not available yet; the
[comparison above](#against-pymupdf-measured) lists the milestone for each.
Multicolumn reading
order follows deterministic whitespace-gutter rules; borderless table
detection is opt-in because aligned prose is geometrically ambiguous. CJK text
generation selects one font per run (not per-glyph fallback). Pure RTL lines
and lines with one Latin or numeric token bracketed by RTL text are restored to
logical Unicode order. Edge tokens, multiple tokens, and general mixed
bidirectional paragraph layout remain in producer visual order. Ruby, warichu,
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
tasks, medians, and wins and losses published as-is. Rendering is reported end
to end and split into cold and warm rasterization plus PNG encoding time and
size, so a fast PNG encoder cannot hide slower rasterization:
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
        ├─ lopdf 0.45   … editing: open → modify → save
        ├─ hayro 0.8    … rendering and positioned extraction
        └─ krilla 0.8 + HarfRust 0.14
                         … shaped, subset-embedded text and form appearances
```

```
rust/          # PyO3 bindings
src/pylopdf/   # Python high-level API
tests/         # pytest (Rust behavior is verified through Python tests)
```

Every release installs each published wheel — five `abi3-py311` on CPython
3.14 and 3.15, five CPython 3.14t, five CPython 3.15t, the sdist, and the
PyEmscripten wheel — on architecture-matched runners and exercises creation,
saving, extraction, and rendering before publishing with build provenance.

## Contributing

Bug reports and focused contributions are welcome. See
[CONTRIBUTING.md](https://github.com/yhay81/pylopdf/blob/main/CONTRIBUTING.md)
for development commands, test expectations, and the rules for sharing PDF
regression files:

```bash
uv sync                    # build + install dependencies
uv run pytest              # tests
uv run ruff check .        # lint
uv run ty check            # type check
```

Report security vulnerabilities privately through
[GitHub Security Advisories](https://github.com/yhay81/pylopdf/security/advisories/new).

## License

[MIT](https://github.com/yhay81/pylopdf/blob/main/LICENSE). Bundled Rust
dependencies keep their own permissive licenses (lopdf and HarfRust are MIT;
hayro and krilla are MIT/Apache-2.0) — see
[NOTICE.md](https://github.com/yhay81/pylopdf/blob/main/NOTICE.md).
