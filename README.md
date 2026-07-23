# pylopdf

[![PyPI](https://img.shields.io/pypi/v/pylopdf)](https://pypi.org/project/pylopdf/)
[![CI](https://github.com/yhay81/pylopdf/actions/workflows/ci.yml/badge.svg)](https://github.com/yhay81/pylopdf/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/pylopdf)](https://pypi.org/project/pylopdf/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

[日本語版 README はこちら](README.ja.md)

PDF editing and rendering for Python, powered by Rust — [lopdf](https://github.com/J-F-Liu/lopdf) for editing and [hayro](https://github.com/LaurenzV/hayro) (the pure-Rust PDF renderer adopted by typst) for rendering.

**MIT licensed, zero runtime dependencies, lightweight wheels.** Covers the common pymupdf use cases without the AGPL.

## Why pylopdf?

| | pylopdf | pymupdf | pypdf | pypdfium2 | pdf_oxide | pikepdf |
|---|---|---|---|---|---|---|
| License | **MIT** | AGPL / commercial | BSD | Apache/BSD | MIT/Apache-2.0 | MPL-2.0 |
| Wheel size | **~3.5 MB** | ~40 MB+ | small (pure Python) | ~8 MB | ~10–11 MB | ~2–5 MB |
| Editing (merge / split / rotate / outlines) | ✅ | ✅ | ✅ | limited | ✅ | ✅ (structure-focused) |
| Rendering (PNG / SVG) | ✅ | ✅ | ❌ | ✅ (PNG) | ❌ | ❌ (docs point to other tools) |
| Text extraction | ✅ (basic) | ✅ (advanced) | ✅ | ✅ | ✅ (advanced, table detection / Markdown) | ❌ (docs point to other tools) |
| Encryption (AES-256) | ✅ read & write | ✅ | ✅ | ❌ | undocumented | ✅ (via qpdf) |
| CJK font fallback | ✅ ([cjk] extra) | ✅ | — | manual | — | — |
| Implementation | **pure Rust** | C | Python | C++ (PDFium) | Rust | C++ (qpdf) |

- Fits size-constrained environments such as AWS Lambda
- Safe for commercial projects that need to avoid the AGPL
- abi3: one wheel covers Python 3.10–3.14
- API modeled after [pymupdf](https://github.com/pymupdf/PyMuPDF)

**Limitations**: no precise layout analysis, and no appearance-stream regeneration for forms and
annotations (form filling uses NeedAppearances — viewers draw the values). Use pymupdf if you need those.
Typesetting, PDF/A output, and digital signatures are covered by the ecosystem recipes below.

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

# Positioned text and search (pymupdf-style, top-left origin)
words = doc[0].get_text("words")     # (x0, y0, x1, y1, word, block, line, word_no)
layout = doc[0].get_text("dict")     # blocks -> lines -> spans with bboxes
rects = doc[0].search_for("tax")     # case-insensitive, list[Rect]
images = doc[0].get_images()         # [{"width", "height", "bbox", "ext", "image"}]
pix = doc[0].get_pixmap(dpi=144)     # RGBA8 pixels for NumPy / PIL (pix.samples)

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

# Drawing (coordinates are the same top-left display space as search_for / get_text)
page.insert_image((72, 72, 200, 200), filename="logo.png")   # JPEG passthrough, PNG with alpha
page.insert_image(page.search_for("Approved")[0], stream=stamp_png)  # stamp at a search hit
page.show_pdf_page(page.rect, letterhead)  # overlay another PDF page as vectors (watermark / letterhead)
page.replace_text("DRAFT", "FINAL")        # text replacement (simple-encoded fonts only)

# Headers / footers / page numbers (standard-14 fonts, WinAnsi range; CJK via the typst recipe)
for i, p in enumerate(doc):
    p.insert_text((p.rect.width - 90, p.rect.height - 30), f"Page {i + 1}", fontsize=9)

# Annotations: search & highlight / link
page.add_highlight_annot(page.search_for("important"))  # appearance stream included (visible everywhere)
page.add_link_annot(page.search_for("Example")[0], "https://example.com/")
print(page.annots())  # [{"type", "rect", "contents", "uri"}]

# Make scanned PDFs searchable (write external OCR results as an invisible text layer)
page.insert_ocr_text_layer(ocr_words)  # sequence of (x0, y0, x1, y1, text, ...); near-zero size cost, CJK included

# Markdown conversion (RAG / LLM preprocessing; size-based headings, CJK-aware line joining)
md = doc.to_markdown()
md_p1 = doc[0].to_markdown()

# Read the PDF/A self-declaration (validation belongs to veraPDF)
print(doc.get_pdfa_claim())  # e.g. (2, "B") for PDF/A-2b; None if absent

# Forms (AcroForm): read and fill
print(doc.get_form_fields())        # [{"name", "type", "value"}]
doc.set_form_field("customer", "Taro Yamada")
doc.set_form_field("agree", True)   # checkboxes take bool or a state name

# Page labels (display numbers: roman front matter + decimal body, etc.)
doc.set_page_labels([{"startpage": 0, "style": "r"}, {"startpage": 3, "style": "D"}])
print(doc[4].get_label())  # "2"

# File attachments (e.g. attach the XML data to an invoice PDF)
doc.embfile_add("invoice.xml", xml_bytes, filename="invoice-data.xml")
print(doc.embfile_names())  # ["invoice.xml"]
xml = doc.embfile_get("invoice.xml")

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

## Ecosystem recipes (typesetting, PDF/A, signatures)

pylopdf stays a lightweight core for editing, extraction, and rendering; adjacent
concerns are solved by pairing it with established libraries. The recipes below
are covered by integration tests (tests/test_interop.py).

**Typesetting / creating new documents = [typst](https://typst.app/)**
(via [typst-py](https://pypi.org/project/typst/)). Typeset reports with typst and
feed the bytes straight into pylopdf:

```python
import typst
import pylopdf

pdf_bytes = typst.compile("report.typ")   # typesetting: typst
doc = pylopdf.open(stream=pdf_bytes)      # editing / extraction / merging: pylopdf
```

**PDF/A for new documents** is also typst's job (validated export via krilla;
PDF/A-1b through 4 and PDF/UA-1):

```python
pdf_a: bytes = typst.compile("report.typ", pdf_standards="a-2b")
```

**CJK watermarks / headers / footers** combine typst with pylopdf: typeset a
one-page stamp with typst (fonts get subset-embedded), then burn it onto every
page as vectors with `show_pdf_page`:

```python
from pylopdf_fonts_cjk import sans_path  # pip install pylopdf[cjk] (reuses the Noto fonts)

stamp_typ = """
#set page(width: 595pt, height: 842pt, fill: none)
#set text(font: "Noto Sans JP", size: 48pt, fill: rgb(255, 0, 0, 40%))
#align(center + horizon)[社外秘]
"""
stamp = pylopdf.open(stream=typst.compile(stamp_typ.encode(), font_paths=[str(sans_path().parent)]))
for page in doc:
    page.show_pdf_page((0, 0, page.rect.width, page.rect.height), stamp)
```

Converting or validating *existing* PDFs against PDF/A is a different problem;
[veraPDF](https://verapdf.org/) (Java) is the de-facto validator.

**Digital signatures (PAdES) = [pyHanko](https://pypi.org/project/pyHanko/)** (MIT).
pyHanko signs with an incremental update, so the bytes produced by pylopdf remain
untouched as a prefix of the signed file:

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
signed_pdf: bytes = out.getvalue()
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
| `get_page_text(pno, option="text")` | Extract text (or positioned layout: `"words"` / `"blocks"` / `"dict"`) |
| `render_page(pno, scale=1.0, dpi=None, background=None)` | Render a page to PNG bytes; `dpi` replaces `scale`, `background` is an RGB(A) fill (max 65,535 px per side / 64 MP total) |
| `render_page_svg(pno)` | Render a page to an SVG string |
| `set_fallback_font(font, kind="sans", index=0)` | Set a fallback font (path/bytes) for non-embedded CJK fonts; `None` disables auto-detection |
| `select(page_numbers)` | Keep only the given pages, in the given order (repeats duplicate the page) |
| `delete_page(pno)` / `delete_pages(iterable)` | Delete pages |
| `insert_pdf(other, from_page=0, to_page=-1, start_at=-1)` | Merge a page range (negative / reversed ranges; `start_at` sets the insertion position) |
| `new_page(pno=-1, width=595, height=842)` / `copy_page(pno, to=-1)` | Insert a blank page / duplicate a page |
| `get_toc()` / `set_toc(toc)` | Read/write outlines as `[[level, title, page], ...]` (page numbers are 1-based here) |
| `to_markdown(pages=None)` | Markdown conversion (size-inferred headings, CJK-aware joining, bullet normalization; no bold/tables/multi-column) |
| `get_form_fields()` / `set_form_field(name, value)` | List and fill AcroForm fields (NeedAppearances approach; checkboxes take bool) |
| `get_pdfa_claim()` | Read the XMP PDF/A declaration `(part, conformance)` (a self-claim read, not validation) |
| `embfile_add(name, data, filename=, desc=)` / `embfile_names()` / `embfile_get(name)` / `embfile_del(name)` | Add / list / read / delete file attachments (EmbeddedFiles) |
| `get_page_labels()` / `set_page_labels(labels)` | Read/write page label ranges (`{"startpage", "style", "prefix", "firstpagenum"}`) |
| `save(filename, garbage=, deflate=, object_streams=, user_pw=, owner_pw=, permissions=)` / `tobytes(same)` | Save; prune / compress / object streams, or AES-256 encryption via `user_pw` / `owner_pw` (the in-memory document stays plain) |
| `close()` | Close (supports `with`) |

`pylopdf.Page` (obtained via `doc[i]`):

| Method / property | Description |
|---|---|
| `number` / `parent` | 0-based page number and owning Document |
| `get_label()` | Display label of the page ("iv", "A-2", …; empty string if undefined) |
| `get_text(option="text")` | Text extraction; `"words"` / `"blocks"` / `"dict"` return positioned layout |
| `to_markdown()` | Markdown conversion of this page |
| `search_for(needle)` | Case-insensitive text search returning `list[Rect]` |
| `get_images()` | Extract page images (original JPEG bytes passed through; others as PNG) |
| `get_pixmap(scale, dpi=, background=)` | Render to a `Pixmap` (straight RGBA8: `samples` / `width` / `height` / `stride` / `tobytes()`) |
| `insert_image(rect, filename=/stream=, keep_proportion=True, overlay=True)` | Draw an image (JPEG without recompression, PNG with alpha; rect in display coordinates) |
| `show_pdf_page(rect, src, pno=0, keep_proportion=True, overlay=True)` | Overlay a page from another document as vectors (watermarks / stamps / letterheads) |
| `insert_text(point, text, fontsize=11, fontname="helv", color=(0,0,0))` | Print text with a standard-14 font (WinAnsi range; `\n` for multiple lines; upright on rotated pages) |
| `insert_ocr_text_layer(words)` | Write OCR results as an invisible text layer (searchable PDFs; no font embedding, near-zero size) |
| `annots()` | Read annotations (`{"type", "rect", "contents", "uri"}` dicts; rect in display coordinates) |
| `add_highlight_annot(rects, color=(1,1,0), opacity=0.4, content=None)` | Highlight annotation; feed `search_for` results directly; appearance stream included |
| `add_link_annot(rect, uri)` | URI link annotation (no border) |
| `replace_text(search, replacement, default_char=None)` | Replace text (simple-encoded fonts only; returns the count; no CJK) |
| `render(scale, dpi=, background=)` / `render_svg()` | Rendering |
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

## Benchmarks

A reproducible benchmark ships with the repo (same corpus, same tasks, medians —
wins and losses are published as-is). See
[bench/results/latest.md](bench/results/latest.md) for the latest numbers with
environment details:

```bash
uv sync --all-extras --group bench && uv run python bench/run.py
```

## License

MIT (lopdf is MIT; hayro is MIT/Apache-2.0)
