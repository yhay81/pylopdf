---
title: Getting started
description: Install pylopdf and learn its core editing, rendering, extraction and drawing workflows.
---

# Getting started

## Install { #installation }

```bash
pip install pylopdf
```

Install only the locale or script fonts your workload needs:

```bash
pip install "pylopdf[jp]"     # Japanese fallback and generation
pip install "pylopdf[ko]"     # Korean
pip install "pylopdf[zh-cn]"  # Simplified Chinese
pip install "pylopdf[zh-tw]"  # Traditional Chinese
pip install "pylopdf[ar]"     # Arabic generation (also: he, hi, th)
```

The four CJK locale extras support both rendering non-embedded Adobe CJK fonts
and subset-embedded generation. Arabic, Hebrew, Devanagari, and Thai packages
are generation providers. Choose `jp`, `ko`, `zh-cn`, or `zh-tw` explicitly
for the required regional glyph conventions; there is no generic CJK extra.

Prebuilt wheels cover CPython 3.11–3.15 on Linux (x86_64, ARM64), macOS
(Apple silicon, Intel), and Windows (x86_64), plus free-threaded 3.14t and
3.15t. See [Concurrency](concurrency.md) for free-threaded use and
[WebAssembly](wasm.md) for Pyodide and Cloudflare Python Workers.

## Open, inspect, save { #open-inspect-save }

```python
import pylopdf

with pylopdf.open("input.pdf") as doc:     # or pylopdf.open(stream=pdf_bytes)
    print(doc.page_count)                  # len(doc) works too
    print(doc.metadata["title"])
    doc.set_metadata({"title": "Report", "author": "Alice"})

    doc.save("out.pdf")
    data = doc.tobytes()                   # the same PDF as bytes
    doc.save("small.pdf", garbage=True, deflate=True, use_objstms=True)
    doc.save(
        "locked.pdf",
        encryption=pylopdf.PDF_ENCRYPT_AES_256,
        user_pw="secret",
        permissions=pylopdf.PDF_PERM_PRINT,
    )
```

- **Encrypted files** open with `password=`, or call `doc.authenticate()`
  afterwards.
- **Metadata only:** `pylopdf.peek_metadata(path)` reads metadata and the page
  count without parsing the whole document, which suits scans of large
  collections. `max_file_size=` rejects oversized input before parsing.
- **Damaged files:** when pylopdf recovers broken cross-reference data, it
  emits `PylopdfWarning` and sets `doc.is_repaired` (`repaired` in
  `peek_metadata`). Saving writes clean cross-reference data.

!!! tip "Untrusted input"

    Pass `limits=pylopdf.DocumentLimits.web()` when opening files you do not
    control. It bounds file size, structure, decompression, and interpreted
    text, and raises `LimitError` instead of exhausting memory. Check
    `doc.complexity` before heavy work. See
    [Security](security.md#untrusted-pdfs) for every budget.

## Pages, text, search { #pages-text-search }

```python
with pylopdf.open("input.pdf") as doc:
    for page in doc:                       # or doc[0], doc[-1]; 0-based
        print(page.number, page.rect)

    page = doc[0]
    text = page.get_text()                 # plain text
    words = page.get_text("words")         # (x0, y0, x1, y1, word, block, line, word_no)
    layout = page.get_text("dict")         # blocks → lines → spans with bbox, size, font
    hits = page.search_for("total")        # case-insensitive; list[Rect]
```

All coordinates use a top-left origin in **display space**. Search results,
layout, drawing, and rendering share one coordinate system, rotated pages
included.

## Render { #rendering }

```python
with pylopdf.open("input.pdf") as doc:
    doc[0].get_pixmap(dpi=144).save("page-1.png")
    png = doc[0].get_pixmap(dpi=300).tobytes()    # PNG bytes
    pix = doc[0].get_pixmap(dpi=144)               # RGB pixels for NumPy or Pillow
    pngs = doc.render_pages(dpi=144, workers=4)    # every page, in parallel
    svg = doc[0].get_svg_image()
```

## Edit { #editing }

```python
with pylopdf.open("input.pdf") as doc:
    doc.select([2, 0, 1])                  # keep and reorder; repeat an index to duplicate
    doc.delete_pages([1])                  # pages are renumbered after each edit
    doc.new_page()                         # blank A4 at the end
    doc.fullcopy_page(0, to=1)             # independent copy of page 0 at index 1
    doc[0].set_rotation(90)
    doc.set_outline([(1, "Chapter 1", 0), (2, "Section 1.1", 1)])
    doc.save("edited.pdf")

with pylopdf.Document() as merged, pylopdf.open("a.pdf") as a, pylopdf.open("b.pdf") as b:
    merged.insert_pdf(a)                   # all of a.pdf
    merged.insert_pdf(b, from_page=0, to_page=2)  # pages 1–3 of b.pdf
    merged.save("merged.pdf")
```

## Draw and annotate { #drawing-annotations }

Images, overlays, and annotations:

```python
with pylopdf.open("input.pdf") as doc, pylopdf.open("letterhead.pdf") as letterhead:
    page = doc[0]
    page.insert_image((72, 72, 200, 200), filename="logo.png")  # JPEG passes through as is
    page.show_pdf_page(page.rect, letterhead)                    # page 0 of another PDF as a vector overlay
    page.draw_line((36, 340), (400, 340), width=0.5, dashes="[3 2] 0")

    hits = page.search_for("important")
    if hits:
        page.add_highlight_annot(hits)
    for rect in page.search_for("Example")[:1]:
        page.insert_link({"kind": pylopdf.LINK_URI, "from": rect, "uri": "https://example.com/"})
    doc.save("annotated.pdf")
```

Text and fonts. Without a font argument, pylopdf uses Helvetica for Latin text
and the installed locale extra, such as `pylopdf[jp]`, for other scripts:

```python
with pylopdf.open("input.pdf") as doc:
    page = doc[0]
    page.insert_text((40, 40), "CONFIDENTIAL", fontsize=18, color=(1, 0, 0))
    page.insert_text((40, 70), "社外秘", fontsize=18)                        # needs pylopdf[jp]
    page.insert_text((40, 100), "简体中文", fontsize=18, font_language="zh-CN")  # needs pylopdf[zh-cn]

    bold = pylopdf.Font(fontfile="NotoSansJP-Bold.otf")    # any OpenType file
    width = bold.text_length("見出し", fontsize=16)          # the exact drawn width
    page.insert_text((page.rect.width - 40 - width, 140), "見出し", font=bold, fontsize=16)

    semibold = pylopdf.Font(fontfile="NotoSansCJKjp-VF.otf", weight=600)  # variable fonts take any weight on their axis
    page.insert_text((40, 170), "見出し", font=semibold, fontsize=16)
    doc.save("text.pdf")
```

Several shapes and text runs as one drawing, with each font embedded once:

```python
with pylopdf.open("input.pdf") as doc:
    with doc[0].new_shape() as shape:
        shape.draw_rect((36, 220, 400, 320), radius=0.1)
        shape.finish(color=(0.2, 0.4, 0.6), fill=(0.95, 0.97, 1), width=1)
        shape.insert_text((48, 250), "Report", fontsize=16)
        shape.insert_textbox((48, 260, 390, 310), "Body paragraph.", fontsize=11)
    doc.save("shapes.pdf")
```

## Scanned PDFs, forms, Markdown { #scans-forms-markdown }

```python
with pylopdf.open("scan.pdf") as doc:
    ocr_words = [(72, 90, 140, 104, "Invoice"), (146, 90, 190, 104, "#1024")]
    doc[0].insert_ocr_text_layer(ocr_words)   # any OCR output: (x0, y0, x1, y1, text, ...)

with pylopdf.open("form.pdf") as doc:
    doc.set_form_field("customer", "Alice")   # writes the value and its appearance
    doc.save("filled.pdf")

with pylopdf.open("input.pdf") as doc:
    md = doc.to_markdown()                    # headings, lists, and tables for RAG
```

See [Offline OCR](ocr.md) to recognize text inside pylopdf instead of bringing
your own OCR words.

Continue with [Ecosystem recipes](ecosystem.md) for typesetting, PDF/A and
digital signatures, or the [migration guide](migration.md) if you come from
pymupdf.
