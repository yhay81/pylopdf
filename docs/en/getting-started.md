---
title: Getting started
description: Install pylopdf and learn its core editing, rendering, extraction and drawing workflows.
---

# Getting started

!!! note "Project status"

    pylopdf is pre-1.0, and the next release redesigns its API, as the
    [roadmap](https://github.com/yhay81/pylopdf/blob/main/ROADMAP.md)
    describes. The [stability policy](stability.md) explains what may
    change. This site follows the `main` branch, so it can describe
    changes that are not on PyPI yet. The
    [changelog](https://github.com/yhay81/pylopdf/blob/main/CHANGELOG.md)
    lists what each release contains.

## Install { #installation }

```bash
pip install pylopdf
```

pylopdf needs Python 3.11 or newer and has no required Python dependencies.
Prebuilt wheels cover Linux (x86_64, ARM64), macOS (Apple silicon, Intel), and
Windows (x86_64), including free-threaded Python 3.14t and 3.15t. A WebAssembly
build runs in Pyodide and Cloudflare Python Workers; see
[WebAssembly](wasm.md).

Optional extras add fonts and OCR models. Install only the ones you need:

| Extra | Adds |
|---|---|
| `jp`, `ko`, `zh-cn`, `zh-tw` | Japanese, Korean, Simplified Chinese, and Traditional Chinese fonts, for displaying PDFs that do not embed their fonts and for writing text |
| `ar`, `he`, `hi`, `th` | Arabic, Hebrew, Hindi (Devanagari), and Thai fonts for writing text |
| `ocr` | Models for the built-in [offline OCR](ocr.md) engine |
| `migrate` | The [pymupdf migration tool](migration.md#codemod) |

```bash
pip install "pylopdf[jp,ocr]"
```

The Chinese, Japanese, and Korean extras are separate because each region
draws some characters differently; pick the one that matches your documents.

## Open, inspect, save { #open-inspect-save }

```python
import pylopdf

with pylopdf.open("input.pdf") as doc:     # or pylopdf.open(pdf_bytes)
    print(len(doc.pages))
    print(doc.metadata.get("title"))
    doc.metadata.update(title="Report", author="Alice")

    doc.save("out.pdf")                    # compressed, without unused objects
    data = doc.to_bytes()                  # the same PDF as bytes
    doc.save("small.pdf", dedupe=True, object_streams=True)
    doc.save(
        "locked.pdf",
        encryption=pylopdf.Encryption(
            user_password="secret",
            owner_password="owner-secret",
            permissions=pylopdf.Permissions.PRINT,
        ),
    )
```

- **Encrypted files:** pass `password=` when opening, or call
  `doc.unlock(password)` afterwards; `doc.needs_password` tells whether you
  have to.
- **Metadata only:** `pylopdf.probe(path)` reads the page count, version, and
  metadata without loading the whole document, which is useful when scanning
  large collections.
- **Damaged files:** when pylopdf has to repair a broken file to open it, it
  emits a `PylopdfWarning` and sets `doc.is_repaired`. Saving writes a clean
  file.

!!! tip "Files from untrusted sources"

    Open files you do not control with
    `limits=pylopdf.DocumentLimits.web()`. pylopdf then raises `LimitError`
    instead of exhausting memory on oversized or malicious input. See
    [Security](security.md#untrusted-pdfs).

## Pages, text, search { #pages-text-search }

```python
with pylopdf.open("input.pdf") as doc:
    for page in doc.pages:                 # or doc.pages[0], doc.pages[-1]; indexes start at 0
        print(page.index, page.rect)

    page = doc.pages[0]
    text = page.text()                     # plain text
    words = page.words()                   # Word records with text, bbox, block, line, index
    layout = page.layout()                 # blocks → lines → spans with bbox, size, font
    hits = page.search("total")            # case-insensitive; a list of Rect
    tables = page.tables()                 # Table records with bbox, rows, and cells
```

Coordinates are in points (1/72 inch) with the origin at the top left of the
page as it is displayed. Search results, extracted text, drawing, and rendering
all use the same coordinates, also on rotated pages.

## Render { #rendering }

```python
with pylopdf.open("input.pdf") as doc:
    doc.pages[0].pixmap(dpi=144).save("page-1.png")
    png = doc.pages[0].pixmap(dpi=300).to_bytes()  # PNG bytes
    pix = doc.pages[0].pixmap(dpi=144)            # RGB pixels for NumPy or Pillow
    pngs = doc.render(dpi=144, workers=4)       # every page, in parallel
    svg = doc.pages[0].svg()
```

## Edit pages { #editing }

```python
with pylopdf.open("input.pdf") as doc:
    doc.pages.select([2, 0, 1])            # keep and reorder; repeat an index to duplicate
    del doc.pages[1]
    doc.pages.new()                        # blank A4 page at the end
    doc.pages.insert(1, doc.pages[0])      # a copy of page 0 at index 1
    doc.pages.move(0, -1)                  # move the first page to the end
    doc.pages[0].rotation = 90
    doc.outline = [
        pylopdf.OutlineEntry(1, "Chapter 1", pylopdf.Destination(0)),
        pylopdf.OutlineEntry(2, "Section 1.1", pylopdf.Destination(1)),
    ]
    doc.save("edited.pdf")

with pylopdf.Document() as merged, pylopdf.open("a.pdf") as a, pylopdf.open("b.pdf") as b:
    merged.pages.extend(a.pages)           # all of a.pdf
    merged.pages.extend(b.pages[:3])       # the first three pages of b.pdf
    merged.save("merged.pdf")
```

Page indexes shift after pages are added, removed, or reordered. A `Page`
object fetched before such a change raises `StalePageError`; fetch it again
with `doc.pages[i]`.

## Draw and annotate { #drawing-annotations }

Images, overlays, lines, and annotations:

```python
with pylopdf.open("input.pdf") as doc, pylopdf.open("letterhead.pdf") as letterhead:
    page = doc.pages[0]
    page.insert_image((72, 72, 200, 200), filename="logo.png")
    page.show_pdf_page(page.rect, letterhead)       # page 0 of another PDF, kept as vectors
    page.draw_line((36, 340), (400, 340), width=0.5, dashes="[3 2] 0")

    hits = page.search("important")
    if hits:
        page.add_highlight_annot(hits)
    for rect in page.search("Example")[:1]:
        page.links.add(rect, "https://example.com/")
    doc.save("annotated.pdf")
```

Text and fonts. Without a font argument, pylopdf writes Latin text in
Helvetica and other scripts with the font of an installed extra, such as
`pylopdf[jp]`:

```python
with pylopdf.open("input.pdf") as doc:
    page = doc.pages[0]
    page.insert_text((40, 40), "CONFIDENTIAL", fontsize=18, color=(1, 0, 0))
    page.insert_text((40, 70), "社外秘", fontsize=18)                          # needs pylopdf[jp]
    page.insert_text((40, 100), "简体中文", fontsize=18, font_language="zh-CN")  # needs pylopdf[zh-cn]

    bold = pylopdf.Font(fontfile="NotoSansJP-Bold.otf")       # any OpenType file
    width = bold.text_length("見出し", fontsize=16)             # the width as drawn
    page.insert_text((page.rect.width - 40 - width, 140), "見出し", font=bold, fontsize=16)
    doc.save("text.pdf")
```

To draw shapes, text, and images together, in order, use a canvas. Each font
and image is then stored once:

```python
from pylopdf import Color

with pylopdf.open("input.pdf") as doc:
    with doc.pages[0].draw() as canvas:
        canvas.rect((36, 220, 400, 320), radius=6, fill=Color(0.95, 0.97, 1), stroke=Color(0.2, 0.4, 0.6))
        canvas.text("Report", (48, 250), size=16)
        canvas.textbox((48, 260, 390, 310), "Body paragraph.", size=11)
        with canvas.opacity(0.3):
            canvas.image((300, 230, 390, 300), "logo.png")
    doc.save("canvas.pdf")
```

To write a new document, such as a tagged PDF/UA file, use
[`pylopdf.create()`](api.md#create): its pages draw with the same canvas.

## Scans, forms, Markdown { #scans-forms-markdown }

```python
with pylopdf.open("scan.pdf") as doc:
    doc.pages[0].apply_ocr()                        # needs pylopdf[ocr]; adds invisible, searchable text
    doc.save("searchable.pdf")

with pylopdf.open("form.pdf") as doc:
    print(doc.get_form_fields())
    doc.set_form_field("customer", "Alice")   # writes the value and how it looks
    doc.save("filled.pdf")

with pylopdf.open("input.pdf") as doc:
    md = doc.markdown()                       # headings, lists, and tables
```

[Offline OCR](ocr.md) covers OCR in detail, including how to add text
recognized by another OCR engine.

## Not supported yet { #not-supported }

Redaction, editing or deleting annotations, creating form fields,
incremental saving, and validated PDF/A and PDF/UA output are planned; the
[roadmap](https://github.com/yhay81/pylopdf/blob/main/ROADMAP.md) lists them.
pylopdf opens PDF files only.

Typesetting with HTML and CSS and digital signatures stay with ecosystem
tools. The [ecosystem recipes](ecosystem.md) show how to pair pylopdf with
typst and pyHanko for those.

## Next steps { #next-steps }

- [API reference](api.md) for every class and method
- [Migrating from pymupdf](migration.md) if you are porting existing code
- [Contributing and support](contributing.md) to report a bug or ask for a
  feature
