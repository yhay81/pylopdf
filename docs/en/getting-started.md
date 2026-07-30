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
are generation providers. The former Japanese-only `pylopdf[cjk]` name remains
as a compatibility alias for `pylopdf[jp]`.

Release CI installs every native platform wheel on an architecture-matched
runner and exercises PDF creation, saving, reopening, extraction, rendering,
and immutable Pixmap storage. This covers all five `abi3-py310` and all five
CPython 3.14t artifacts; the sdist and PyEmscripten wheel have separate
environment-specific gates.

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
`pylopdf.peek_metadata(path)` reads metadata and page count without fully
parsing the document — useful when scanning large collections. Its
optional `max_file_size=` rejects oversized path or byte input before parsing;
the backward-compatible default is unbounded. Its
`repaired` result and `doc.is_repaired` expose the bounded recovery of an
incorrect final classic `startxref`; pylopdf also emits `PylopdfWarning`, and
saving rewrites normalized cross-reference data. Pass
`limits=pylopdf.DocumentLimits.web()` when processing untrusted files. It
bounds file, structure, decompression, the rendering/extraction PDF snapshot,
and interpreted text; inspect
`doc.complexity` before heavy work and catch `LimitError` for controlled
rejection. See [Security](security.md#untrusted-pdfs) for every budget.

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
batch = doc.render_pages([0, 1, 2], scale=2, workers=4)
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
page.insert_image((300, 72, 500, 200), pixmap=thumbnail, rotate=90)  # direct RGBA, clockwise rotation
page.show_pdf_page(page.rect, letterhead)                    # vector overlay; same-document src also works
page.insert_text((40, 40), "CONFIDENTIAL", fontsize=18, color=(1, 0, 0))
page.insert_text((40, 70), "社外秘", fontsize=18)             # auto-subset with pylopdf[jp]
page.insert_text((40, 100), "简体中文", fontsize=18, font_language="zh-CN")
page.add_highlight_annot(page.search_for("important"))       # search & mark
page.add_link_annot(page.search_for("Example")[0], "https://example.com/")
```

## Scanned PDFs, forms, Markdown { #scans-forms-markdown }

```python
page.insert_ocr_text_layer(ocr_words)        # searchable PDFs from any OCR output
doc.set_form_field("customer", "Alice")      # native AcroForm appearance
doc.set_form_field("customer_ja", "山田 太郎")  # auto-subset with pylopdf[jp]
md = doc.to_markdown()                       # RAG-ready Markdown
```

Continue with [Ecosystem recipes](ecosystem.md) for typesetting, PDF/A and
digital signatures, or the [migration guide](migration.md) if you come from
pymupdf.
