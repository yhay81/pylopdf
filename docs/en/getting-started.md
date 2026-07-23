---
title: Getting started
description: Install pylopdf and learn its core editing, rendering, extraction and drawing workflows.
---

# Getting started

## Install { #installation }

```bash
pip install pylopdf
```

For rendering Japanese PDFs without embedded fonts, install with the bundled
Noto CJK fonts (auto-detected at render time):

```bash
pip install pylopdf[cjk]
```

## Open, inspect, save { #open-inspect-save }

```python
import pylopdf

doc = pylopdf.open("input.pdf")           # or pylopdf.open(stream=pdf_bytes)
print(doc.page_count)                     # len(doc) works too
print(doc.metadata["title"])
doc.set_metadata({"title": "Report", "author": "Alice"})

doc.save("out.pdf")
data = doc.tobytes()
doc.save("small.pdf", garbage=True, deflate=True, object_streams=True)
doc.save("locked.pdf", user_pw="secret", permissions=pylopdf.Permissions.PRINT)
```

Encrypted PDFs open with `password=` (or `doc.authenticate()` afterwards).
`pylopdf.peek_metadata(path)` reads metadata and page count without parsing the
whole file — useful when scanning large collections. Pass
`max_decompressed_size=` when processing untrusted files (decompression-bomb
protection). The limit is checked per stream at open time, including page
content and decoded image size; streams whose filter chain cannot be bounded
safely are rejected while the limit is enabled.

## Pages, text, search { #pages-text-search }

```python
page = doc[0]                             # 0-based; negative indices from the end
for page in doc:
    print(page.number, page.rect)

text = page.get_text()                    # plain text
words = page.get_text("words")            # (x0, y0, x1, y1, word, block, line, word_no)
layout = page.get_text("dict")            # blocks → lines → spans (bbox, size, font, flags)
hits = page.search_for("total")           # case-insensitive, list[Rect]
```

All coordinates are top-left origin **display space** — search results, layout,
drawing and rendering all share the same coordinate system, including rotated
pages.

## Render { #rendering }

```python
png = doc.render_page(0, dpi=300)                    # bytes (PNG)
pix = page.get_pixmap(scale=2)                       # RGBA8 pixels for NumPy / PIL
svg = doc.render_page_svg(0)
```

## Edit { #editing }

```python
doc.delete_pages([1, 2])
doc.select([2, 0])                                   # keep/reorder (repeat = duplicate)
doc.new_page(); doc.copy_page(0, to=1)

merged = pylopdf.Document()
merged.insert_pdf(pylopdf.open("a.pdf"))
merged.insert_pdf(pylopdf.open("b.pdf"), from_page=0, to_page=2, start_at=0)

doc.set_toc([[1, "Chapter 1", 1], [2, "Section 1.1", 2]])
page.set_rotation(90)
```

## Draw & annotate { #drawing-annotations }

```python
page.insert_image((72, 72, 200, 200), filename="logo.png")   # JPEG passthrough / PNG alpha
page.insert_image(page.search_for("Approved")[0], stream=stamp_png)
page.show_pdf_page(page.rect, letterhead)                    # overlay another PDF as vectors
page.insert_text((40, 40), "CONFIDENTIAL", fontsize=18, color=(1, 0, 0))
page.add_highlight_annot(page.search_for("important"))       # search & mark
page.add_link_annot(page.search_for("Example")[0], "https://example.com/")
```

## Scanned PDFs, forms, Markdown { #scans-forms-markdown }

```python
page.insert_ocr_text_layer(ocr_words)     # searchable PDFs from any OCR output
doc.set_form_field("customer", "Alice")   # AcroForm fill (NeedAppearances)
md = doc.to_markdown()                    # RAG-ready Markdown
```

Continue with [Ecosystem recipes](ecosystem.md) for typesetting, PDF/A and
digital signatures, or the [migration guide](migration.md) if you come from
pymupdf.
