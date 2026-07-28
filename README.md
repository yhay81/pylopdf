# pylopdf

[![PyPI](https://img.shields.io/pypi/v/pylopdf)](https://pypi.org/project/pylopdf/)
[![CI](https://github.com/yhay81/pylopdf/actions/workflows/ci.yml/badge.svg)](https://github.com/yhay81/pylopdf/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/pylopdf)](https://pypi.org/project/pylopdf/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

[Japanese README](README.ja.md) /
**Documentation: <https://yhay81.github.io/pylopdf/>** (with a
[pymupdf migration guide](https://yhay81.github.io/pylopdf/migration/) and
[API stability policy](https://yhay81.github.io/pylopdf/stability/))

PDF editing, rendering, extraction, and generation for Python, powered by Rust —
[lopdf](https://github.com/J-F-Liu/lopdf) for editing,
[hayro](https://github.com/LaurenzV/hayro) (the pure-Rust PDF renderer adopted
by Typst) for rendering and extraction, and
[krilla](https://github.com/LaurenzV/krilla) with
[HarfRust](https://github.com/harfbuzz/harfrust) for generated text and form
appearances.

**MIT licensed, no mandatory Python dependencies, lightweight wheels.** Covers
the common pymupdf use cases without the AGPL.

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
1.28.0, pypdfium2 5.12.1, pdf-oxide 0.3.75, and pikepdf 10.10.0 on 2026-07-25;
the exact artifact depends on platform and Python ABI.

- Fits size-constrained environments such as AWS Lambda
- Safe for commercial projects that need to avoid the AGPL
- abi3: one wheel covers Python 3.10–3.14
- v0.10 includes native `cp314t` wheels for free-threaded Python 3.14
- API modeled after [pymupdf](https://github.com/pymupdf/PyMuPDF)

**Limitations**: multicolumn text follows deterministic whitespace gutters.
Sub-em gutters require a dense run of repeated long text on both sides, so
aligned labels, dot leaders, and dense multi-separator rows keep row-major
order. Once established, a column boundary follows small scan/OCR drift.
`find_tables()` reconstructs bordered grids from strokes or thin filled rules,
`find_tables()` reconstructs bordered grids from strokes or thin filled rules,
including rectangular merged cells. It conservatively separates repeated text
records when a generator omits internal rules inside an otherwise connected
grid. The opt-in
`find_tables(strategy="text")` handles high-confidence borderless layouts, but
can interpret aligned multicolumn prose as a table. `Document.to_markdown()`
inserts complete bordered tables by default; pass `table_strategy="text"` to
opt into borderless candidates or `None` to preserve plain layout text.
Vertical CJK columns are
reconstructed conservatively and ordered right-to-left; ruby, warichu, and
mixed-orientation Japanese typography are not interpreted semantically. There
is no general-purpose regeneration of arbitrary existing annotation
appearances; the rendering snapshot does conservatively supply missing
appearances for bounded RGB Highlight, Underline, StrikeOut, and Squiggly
annotations with valid `QuadPoints`. AcroForm filling generates appearances for
text, choice, checkbox, radio, and comb text fields, but rich text, pushbuttons,
and signature fields remain out of scope.
Typesetting, PDF/A output, and digital signatures are covered by the ecosystem
recipes below. Native OCR returns axis-aligned word boxes; automatic page
orientation, arbitrary deskew, ruby, warichu, and mixed-orientation typography
remain explicit limits.

## Install

```bash
pip install pylopdf
```

To render Japanese PDFs without embedded fonts, or auto-subset a JP font for
Japanese/Han `insert_text` and `insert_textbox`, install the optional font
package (Noto Sans/Serif JP):

```bash
pip install pylopdf[cjk]
```

For local PP-OCRv6 recognition without system executables, shared libraries,
network requests, or an ONNX parser at runtime, install the optional model
wheel:

```bash
pip install "pylopdf[ocr]"
```

See the [offline OCR guide](https://yhay81.github.io/pylopdf/ocr/) for memory
controls, searchable-layer behavior, and current layout boundaries.

### WebAssembly and Cloudflare Workers

pylopdf 0.11 adds a static PyEmscripten wheel for the pinned Python 3.13 /
Pyodide 0.28.3 ABI. Cloudflare Python Workers are the supported public
installation path: every release resolves the wheel from PyPI, bundles the
repository's
[bounded PDF extraction Worker](examples/cloudflare-worker/README.md), starts
local `workerd`, and verifies that a module-scope `import pylopdf` can serve
`/health`.

Pyodide 0.28.3 itself is runtime-tested, but its older `micropip` cannot install
PyPI's PEP 783 wheel tag directly. Native OCR inference is intentionally absent
from Wasm; external OCR results can still be inserted with
`Page.insert_ocr_text_layer()`. See the
[WebAssembly guide](https://yhay81.github.io/pylopdf/wasm/) for the exact
version matrix, local Pyodide workflow, resource policy, and release gates.

Every release installs each of the five `abi3-py310` and five CPython 3.14t
wheels on an architecture-matched Linux, macOS, or Windows runner and exercises
PDF creation, saving, reopening, extraction, rendering, and immutable Pixmap
storage. The sdist and PyEmscripten wheel have equivalent environment-specific
gates, and every artifact carries build provenance.

Building from source (requires a Rust toolchain):

```bash
uv sync
```

### Concurrency and free-threaded Python

Starting with v0.10, pylopdf supports concurrent work on distinct `Document`
objects. Heavy native operations release the GIL, and the `cp314-cp314t` wheel
keeps the GIL disabled on free-threaded Python 3.14. Calls or edits on the same
`Document` must be serialized; use `Document.render_pages(workers=...)` for
supported parallel rendering within one document.

`Pixmap` is immutable. The cp314t wheel supports read-only, zero-copy
`memoryview(pixmap)`; the Python 3.10-compatible abi3 wheel uses the one-copy
`pixmap.samples` fallback. See the
[full concurrency contract](https://yhay81.github.io/pylopdf/concurrency/).

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
tables = doc[0].find_tables(clip=(30, 30, 500, 700))  # complete bordered grids in a region
text_tables = doc[0].find_tables(strategy="text")  # opt-in borderless tables
confidence = text_tables[0].confidence if text_tables else None  # ranking heuristic, not probability
images = doc[0].get_images()         # [{"width", "height", "bbox", "ext", "image"}]
pix = doc[0].get_pixmap(dpi=144, clip=(0, 0, 300, 200))  # cropped RGBA8 pixels for NumPy / PIL

# Rendering
png: bytes = doc.render_page(0)             # 72 dpi; 64 MiB encoded-output cap
png2x: bytes = doc.render_page(0, scale=2)  # 144 dpi
png300 = doc.render_page(0, dpi=300)        # by resolution
png_bg = doc.render_page(0, background=(255, 255, 255))  # white background (default: transparent)
batch = doc.render_pages([0, 1, 2], scale=2, workers=4)  # ordered parallel PNGs
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
page.insert_image((300, 72, 500, 200), pixmap=thumbnail, rotate=90)  # direct RGBA, clockwise rotation
page.show_pdf_page(page.rect, letterhead)  # vector overlay; same-document sources also work
page.replace_text("DRAFT", "FINAL")        # bounded, atomic simple-font replacement

# Headers / footers / page numbers (standard-14 fonts, WinAnsi range)
for i, p in enumerate(doc):
    p.insert_text((p.rect.width - 90, p.rect.height - 30), f"Page {i + 1}", fontsize=9)

# Japanese/Han text auto-subsets the JP font with pip install "pylopdf[cjk]"
page.insert_text((40, 80), "社外秘", fontsize=20, color=(0.8, 0, 0))

# Wrap a paragraph into a rectangle; negative means nothing was drawn
spare = page.insert_textbox(
    (40, 100, 300, 220),
    "日本語も空白なしで自然に折り返します。",
    fontsize=12,
    align=pylopdf.TEXT_ALIGN_JUSTIFY,
)

# Annotations: search & highlight / link
page.add_highlight_annot(page.search_for("important"))  # appearance stream included (visible everywhere)
page.add_link_annot(page.search_for("Example")[0], "https://example.com/")
print(page.annots())  # [{"type", "rect", "contents", "uri"}]

# Native offline OCR: model inputs share a 64 MiB cap; add a searchable layer
engine = pylopdf.OcrEngine(threads=4, max_concurrent=1)  # pip install "pylopdf[ocr]"
words = page.get_text_ocr(engine=engine)
page.apply_ocr(engine=engine)  # skips existing searchable text by default
# Correct a sideways scan clockwise for OCR without changing page rotation
page.apply_ocr(engine=engine, rotation=270)

# Or write external OCR results as an invisible text layer
page.insert_ocr_text_layer(ocr_words)  # (x0, y0, x1, y1, text, ...); max 4,096 words / 1 MiB UTF-8 text

# Markdown conversion (RAG / LLM preprocessing; bordered tables are automatic)
md = doc.to_markdown()
md_with_borderless_tables = doc.to_markdown(table_strategy="text")
md_p1 = doc[0].to_markdown()

# Read the PDF/A self-declaration (1 MiB XMP cap; validation belongs to veraPDF)
print(doc.get_pdfa_claim())  # e.g. (2, "B") for PDF/A-2b; None if absent

# Forms (AcroForm): read and fill
print(doc.get_form_fields())        # [{"name", "type", "value"}]
doc.set_form_field("customer", "Taro Yamada")
doc.set_form_field("customer_ja", "山田 太郎")  # auto-subset with pylopdf[cjk]
doc.set_form_field("agree", True)   # checkboxes take bool or a state name

# Page labels (display numbers: roman front matter + decimal body, etc.)
doc.set_page_labels([{"startpage": 0, "style": "r"}, {"startpage": 3, "style": "D"}])
print(doc[4].get_label())  # "2"

# File attachments (e.g. attach the XML data to an invoice PDF)
doc.embfile_add("invoice.xml", xml_bytes, filename="invoice-data.xml")  # 64 MiB input cap
print(doc.embfile_names())  # ["invoice.xml"]
xml = doc.embfile_get("invoice.xml")  # decoded output is capped at 64 MiB by default
# known_large = doc.embfile_get("archive.bin", max_size=256 * 1024 * 1024)

# Table of contents (page numbers are 1-based here, pymupdf-compatible)
doc.set_toc([[1, "Chapter 1", 1], [2, "Section 1.1", 2]])
print(doc.get_toc())

# Merge (with ranges, reversed order, and an insertion position)
merged = pylopdf.Document()
merged.insert_pdf(pylopdf.open("a.pdf"))
merged.insert_pdf(pylopdf.open("b.pdf"), from_page=0, to_page=2, start_at=0)

# Save
merged.save("merged.pdf")
data: bytes = merged.tobytes()  # 512 MiB output cap; max_size=None opts out

# Optimized save (prune unreferenced objects + compress + object streams)
merged.save("small.pdf", garbage=True, deflate=True, object_streams=True)

# Encrypted save (AES-256; owner_pw alone = open freely, restricted permissions)
merged.save("locked.pdf", user_pw="secret", permissions=pylopdf.Permissions.PRINT)

# Fast metadata probe without parsing the whole document
info = pylopdf.peek_metadata("input.pdf", max_file_size=32 * 1024 * 1024)
print(info["title"], info["page_count"], info["encrypted"], info["repaired"])

# Context manager
with pylopdf.open("input.pdf") as doc:
    print(doc.metadata)

# Encrypted PDFs (RC4-40/128, AES-128, AES-256; empty user passwords open transparently)
doc = pylopdf.open("locked.pdf", password="secret")
doc = pylopdf.open("locked.pdf")
if doc.needs_pass:
    doc.authenticate("secret")  # 0=failed, 2=user, 4=owner, 6=both

# A bounded repair of an incorrect final classic startxref is always visible.
if doc.is_repaired:
    doc.save("normalized.pdf")

# CJK fallback font for PDFs without embedded fonts
# (automatic with pylopdf[cjk]; or bring your own font)
doc.set_fallback_font("NotoSansJP-Regular.otf")
doc.set_fallback_font(font_bytes, kind="serif")
```

Embedded-font `insert_text` shapes each line with HarfRust and asks krilla to
subset and embed the resulting glyphs. With `pylopdf[cjk]`, Japanese and Han
text automatically selects its JP-subset sans font; a Times `fontname` selects
serif. This is one whole-run font selection, not per-glyph fallback. Pass
`fontfile=` / `fontbuffer=` for Hangul, locale-specific Chinese glyph forms,
other scripts, or another typeface. RTL glyph shaping works, but extraction
currently follows visual rather than logical order. Use typst below when full
typesetting is required.

## Ecosystem recipes (typesetting, PDF/A, signatures)

pylopdf stays a lightweight core for editing, extraction, rendering, and bounded
text/form generation; adjacent concerns are solved by pairing it with
established libraries. The recipes below are covered by integration tests
(tests/test_interop.py).

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

**Richly typeset CJK watermarks / headers / footers** can combine typst with
pylopdf. For simple text, use `insert_text(fontfile=...)` directly; for a
full-page composition, typeset one stamp page with typst (fonts get
subset-embedded), then burn it onto every page as vectors with `show_pdf_page`:

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
| `Document(filename=None, stream=None, password=None, max_decompressed_size=None, *, limits=None)` | Open from a path or bytes; empty document if both are None. Passwords stop at 127 UTF-8 bytes. Use `limits=DocumentLimits.web()` for a complete untrusted-upload policy; `max_decompressed_size` remains the compatible per-stream shorthand |
| `doc[i]` / `load_page(pno)` / `for page in doc` | Get a Page view (negative indices count from the end; re-fetch after structural changes) |
| `needs_pass` / `is_encrypted` | Encryption status (pymupdf-compatible semantics) |
| `is_repaired` | Whether opening repaired an incorrect final classic `startxref`; a `PylopdfWarning` is also emitted and saving normalizes the xref data |
| `authenticate(password)` | Decrypt with a password of at most 127 UTF-8 bytes (returns 0/1/2/4/6, pymupdf-compatible) |
| `page_count` / `len(doc)` | Number of pages |
| `limits` / `complexity` | Immutable open-time resource policy, including optional rendering/extraction snapshot, positioned-text glyph, and plain-text assembly caps / cheap page, object, stream, encoded-byte, and direct-depth facts without decoding |
| `metadata` | Bounded standard metadata dict (title, author, subject, keywords, creator, producer, creationDate, modDate, format); 1 MiB aggregate Info text |
| `set_metadata(dict)` | Atomically set standard metadata under a 1 MiB pre-PyO3 UTF-8/encoded boundary (empty string deletes the entry) |
| `get_page_text(pno, option="text")` | Extract text (or positioned layout: `"words"` / `"blocks"` / `"dict"`) |
| `get_text(pages=None)` | Extract plain text from up to 4,096 pages in one batch sharing one interpreter font cache (`None` means every page) |
| `render_page(pno, scale=1.0, dpi=None, background=None, max_size=64 MiB)` | Render bounded PNG bytes; `dpi` replaces `scale`, `background` is an RGB(A) fill (max 65,535 px per side / 64 MP total); `None` opts out |
| `render_pages(pages=None, scale=1.0, workers=None, max_size=512 MiB, ...)` | Render up to 4,096 ordered PNGs from one immutable snapshot; up to 4 workers by default, ~512 MB estimated live-work concurrency, and a cumulative encoded-output cap (`None` opts out) |
| `render_page_svg(pno, max_size=64 MiB)` | Render bounded UTF-8 SVG; over-limit output is rejected before Python string conversion, `None` opts out |
| `compress_images(dpi=150, quality=75)` | Lossily downsample and JPEG-recompress safe unmasked DeviceGray/DeviceRGB DCT or Flate XObjects; preserves the largest reuse, skips non-smaller output, bounds interpretation at 65,536 indirect placements, and returns typed byte/count statistics |
| `set_fallback_font(font, kind="sans", index=0, max_font_size=64 MiB)` | Set a bounded fallback font (path/bytes) for non-embedded CJK fonts; `font=None` disables auto-detection and `max_font_size=None` opts trusted font input out |
| `select(page_numbers)` | Keep up to 4,096 page entries in the given order (repeats duplicate the page) |
| `delete_page(pno)` / `delete_pages(iterable)` | Delete up to 4,096 page entries per call; an empty iterable is a true no-op |
| `insert_pdf(other, from_page=0, to_page=-1, start_at=-1)` | Merge up to 4,096 pages per call (negative / reversed ranges; `start_at` sets the insertion position) |
| `new_page(pno=-1, width=595, height=842)` / `copy_page(pno, to=-1)` | Insert a blank page / duplicate a page |
| `get_toc()` / `set_toc(toc)` | Read/write cycle-aware bounded outlines as `[[level, title, page], ...]` (page numbers are 1-based here; caps: 4,096 entries/nodes, 8,192 edges, 64 levels, 1 MiB text preflighted before PyO3 on writes) |
| `to_markdown(pages=None, table_strategy="lines", max_size=64 MiB)` | Page-at-a-time two-pass Markdown conversion with a bounded linear entry builder, capped at 4,096 pages and cumulative UTF-8 output (`None` opts out); headings, emphasis, CJK joining, lists, columns, vertical order, and bordered/opt-in borderless tables |
| `get_form_fields()` / `set_form_field(name, value, fontfile=, fontbuffer=, fontindex=, max_font_size=64 MiB)` | List and fill AcroForm fields with native text/choice/button appearances; bounded field-tree, 1 MiB caller name/value, button-state, and font interpretation; checkboxes take bool |
| `get_pdfa_claim(max_size=1 MiB)` | Bounded-decode the XMP PDF/A declaration `(part, conformance)` (a self-claim read, not validation); `max_size=None` explicitly opts out |
| `embfile_add(name, data, filename=, desc=, max_size=64 MiB)` / `embfile_names()` / `embfile_get(name, max_size=64 MiB)` / `embfile_del(name)` | Add / list / retrieve / delete attachments under symmetric data/decode defaults; `max_size=None` explicitly opts out, caller text stops at 1 MiB, name trees are capped at 4,096 entries/nodes, and inline FileSpec clone shapes are bounded |
| `get_page_labels()` / `set_page_labels(labels)` | Read/write page label ranges (`{"startpage", "style", "prefix", "firstpagenum"}`); fixed caps: 4,096 entries/nodes, 32 levels, 1 MiB label text preflighted before PyO3 on writes |
| `save(filename, garbage=, deflate=, object_streams=, user_pw=, owner_pw=, permissions=)` / `tobytes(same, max_size=512 MiB)` | Atomically replace a file after a complete same-directory streamed write, or return bounded PDF bytes; prune / compress / object streams, or AES-256 encryption via 127-byte-bounded `user_pw` / `owner_pw`; `max_size=None` opts out of the byte-return limit |
| `close()` | Close (supports `with`) |

`pylopdf.Page` (obtained via `doc[i]`):

| Method / property | Description |
|---|---|
| `number` / `parent` | 0-based page number and owning Document |
| `get_label()` | Display label of the page ("iv", "A-2", …; empty string if undefined) |
| `get_text(option="text")` | Text extraction; `"words"` / `"blocks"` / `"dict"` return positioned layout |
| `get_text_ocr(dpi=300, engine=None, tile_size=1408, overlap=192, min_confidence=0.5, rotation=0, clip=None)` | Recognize positioned words locally through `pylopdf[ocr]` without modifying the page; `rotation` corrects rendered input clockwise and `clip` uses display coordinates |
| `apply_ocr(..., rotation=0, clip=None, skip_existing=True)` | Recognize and insert an orientation-aware invisible searchable layer; existing searchable text in the selected region is skipped by default |
| `to_markdown(table_strategy="lines", max_size=64 MiB)` | Single-page Markdown with the same table and UTF-8 output controls as the document method |
| `search_for(needle, max_hits=4096)` | Case-insensitive bounded search returning `list[Rect]`; terms stop at 4,096 UTF-8 bytes and `max_hits=None` opts trusted result sets out |
| `find_tables(strategy="lines", clip=None)` | Detect complete or conservatively refined bordered grids and rectangular merged cells; use `strategy="text"` for opt-in borderless detection; `clip` filters in display coordinates and results expose confidence diagnostics |
| `get_images()` | Extract page images (original JPEG bytes passed through; others as PNG); rejects partial output above 4,096 placements, 64,000,000 cumulative pixels, or 64 MiB of payloads per page |
| `get_drawings()` | Extract interpreted vector fill/stroke paths as typed pymupdf-style dictionaries with display-space line/cubic geometry, RGB/opacity, fill rule, width, cap, join, and dashes; rejects partial output above 8,192 paths, 131,072 commands, or 131,072 aggregate dash values |
| `get_pixmap(scale, dpi=, background=, clip=None)` | Render to an immutable `Pixmap`; `clip` is a display-coordinate rectangle (straight RGBA8: `samples` / `width` / `height` / `stride` / `tobytes(max_size=64 MiB)` / streaming, failure-atomic PNG-only `save(path)`; cp314t also supports read-only zero-copy `memoryview()`) |
| `insert_image(rect, filename=/stream=/pixmap=, rotate=0, keep_proportion=True, overlay=True, max_size=64 MiB, max_pixels=64,000,000)` | Draw JPEG without recompression, bounded PNG with alpha, or a rendered RGBA `Pixmap` without a PNG round trip; `None` opts trusted encoded input or PNG pixels out of its boundary; optional clockwise right-angle rotation and rect use display coordinates |
| `show_pdf_page(rect, src, pno=0, keep_proportion=True, overlay=True)` | Overlay a page as vectors from another or the same document; same-document placement uses a stable pre-edit snapshot |
| `insert_text(point, text, fontsize=11, fontname="helv", fontfile=, fontbuffer=, fontindex=, color=, overlay=True, max_font_size=64 MiB, max_text_size=1 MiB)` | Print bounded multiline text with a standard-14 or shaped subset font; generated input stops at 4,096 lines; `pylopdf[cjk]` auto-selects its JP font for Japanese/Han; `None` opts the corresponding trusted font or text input out; upright on rotated pages |
| `insert_textbox(rect, text, fontsize=11, fontname="helv", fontfile=, fontbuffer=, fontindex=, color=, align=0, lineheight=None, expandtabs=8, overlay=True, max_font_size=64 MiB, max_text_size=1 MiB)` | Wrap bounded UTF-8 text with UAX #14 and Core 14, explicit OpenType, or auto-selected JP font metrics; physical and wrapped layout stop at 4,096 lines, tab expansion is preflighted, and overflow draws nothing |
| `insert_ocr_text_layer(words, rotation=0)` | Write up to 4,096 words / 1 MiB incrementally counted UTF-8 text as an orientation-aware invisible OCR layer (searchable PDFs; no font embedding) |
| `annots()` / `get_links()` | Bounded annotation/link reads, including one cycle-aware named-destination index per call (4,096 annotations and 1 MiB aggregate metadata text; display coordinates) |
| `add_highlight_annot(rects, color=(1,1,0), opacity=0.4, content=None)` | Highlight annotation; bounded iteration of up to 4,096 `search_for` results; appearance stream included; 1 MiB pre-copy subtype/content budget |
| `add_link_annot(rect, uri)` | URI link annotation (no border; 1 MiB pre-copy subtype/URI budget) |
| `replace_text(search, replacement, default_char=None, max_size=64 MiB)` | Atomic copy-on-write text replacement (simple-encoded fonts only; 4,096-byte caller text is counted without a complete encoded copy; bounded output; returns the count; no CJK) |
| `render(scale, dpi=, background=)` / `render_svg(max_size=64 MiB)` | PNG / bounded UTF-8 SVG rendering |
| `rotation` / `set_rotation(deg)` | Display rotation (multiples of 90, inheritance-resolved) |
| `mediabox` / `cropbox` / `rect` | Page boxes (`Rect`); `rect` is the rotation-aware visible rectangle |
| `set_mediabox(rect)` / `set_cropbox(rect)` | Set page boxes |

Drawing insertions preflight page `/Contents` before decoding inputs or creating
dependent objects. The resulting array is capped at 4,096 stream references;
the one-time `q`/`Q` isolation pair is included in that total, and failures do
not mutate the document.

Module level:

| Name | Description |
|---|---|
| `peek_metadata(filename/stream, password=None, *, max_file_size=None)` | Fast metadata / page-count / encryption probe; optional input-size rejection and 127-byte password boundary; `repaired` reports bounded classic-`startxref` recovery |
| `Permissions` | Encryption permission flags (IntFlag) |
| `Rect` | Rectangle NamedTuple with `width` / `height` |
| `ImageCompressionResult` | Typed counts and rewritten source/result byte totals from `compress_images()` |
| `DrawingInfo` / `DrawingItem` | Typed vector-path dictionary and its line/cubic command union |
| `TEXT_ALIGN_LEFT` / `CENTER` / `RIGHT` / `JUSTIFY` | `insert_textbox` alignment constants (0–3, pymupdf-compatible) |
| `OcrEngine` / `OcrWord` / `OcrRotation` | Reusable pure-Rust PP-OCR engine, its typed positioned-word result, and the `0 / 90 / 180 / 270` clockwise-correction contract |
| `TableFinder` / `Table` / `TableDiagnostics` | Owned table geometry, cell text, strategy, and confidence evidence; `Table.to_markdown(max_size=64 MiB)` preflights escaped UTF-8 output |
| `PylopdfWarning` | Recoverable interpretation warning, including bounded xref repair, font resolution, and image decoding |
| Exceptions | `PdfError` (ValueError-compatible base), `PasswordError`, `OcrError`, `DocumentClosedError`, `EncryptedDocumentError`, `StalePageError` |

For low-level access, use `pylopdf.pylopdf_core._Document` (a thin lopdf wrapper) directly.

## Architecture

Follows the division of labor in the 2026 Rust PDF ecosystem:

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

```bash
uv sync                    # build + install dependencies
uv run pytest              # tests
uv run ruff check .        # lint
uv run mypy src tests      # type check
uv build --wheel           # build a wheel
```

`uv sync` detects Rust source changes and rebuilds automatically (via `tool.uv.cache-keys`).

## Contributing

Bug reports and focused contributions are welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md) for development commands, test expectations,
and the rules for sharing PDF regression files. Report security vulnerabilities
privately through [GitHub Security
Advisories](https://github.com/yhay81/pylopdf/security/advisories/new).

## Benchmarks

A reproducible benchmark ships with the repo (same corpus, same tasks, medians —
wins and losses are published as-is). See
[bench/results/latest.md](bench/results/latest.md) for the latest numbers with
environment details. Free-threaded extraction is generated independently at
[bench/results/free-threaded-latest.md](bench/results/free-threaded-latest.md)
so a normal benchmark run cannot overwrite its CPython 3.14t evidence:

```bash
uv sync --all-extras --group bench && uv run python bench/run.py
# Windows:
py -3.14t bench/free_threaded.py
# POSIX:
python3.14t bench/free_threaded.py
```

The separate [native OCR report](bench/results/ocr-latest.md) publishes strict
and NFKC-normalized CER plus elapsed time on two licensed Japanese fixtures,
including an image-only archival scan. It also records a bounded shared-engine
concurrency check:

```bash
uv sync --all-extras && uv run python bench/ocr.py
```

## License

MIT (lopdf and HarfRust are MIT; hayro and krilla are MIT/Apache-2.0)
