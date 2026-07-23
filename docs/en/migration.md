---
title: Migrating from pymupdf
description: Map pymupdf workflows to pylopdf and understand deliberate differences in types, behavior and scope.
---

# Migrating from pymupdf

pylopdf is pymupdf-*style*, not a drop-in replacement. The data shapes that
determine migration cost — `"words"` tuples, `"dict"` structure,
`search_for → list[Rect]`, 1-based TOC pages — match pymupdf, so most
extraction and page-management code ports with small edits. This page lists
what carries over, what changed, and what to use instead of the parts pylopdf
deliberately does not implement.

!!! note
    pylopdf handles **PDF files only**. pymupdf's ability to open XPS / EPUB /
    images does not carry over.

## Quick mapping { #mapping }

| pymupdf | pylopdf | Notes |
|---|---|---|
| `import fitz` / `import pymupdf` | `import pylopdf` | |
| `fitz.open(path)` / `open(stream=…)` | `pylopdf.open(path)` / `open(stream=…)` | same shape; `password=` too |
| `doc[i]`, `len(doc)`, iteration | same | 0-based, negative indices |
| `doc.metadata` / `set_metadata` | same | same key names |
| `page.get_text()` | same | options: `text` / `words` / `blocks` / `dict` |
| `page.search_for(t)` | same | returns `list[Rect]`; no `quads=` |
| `page.get_pixmap(matrix=fitz.Matrix(2, 2))` | `page.get_pixmap(scale=2)` | or `dpi=144`; no Matrix class |
| `pix.samples / width / height / stride` | same | always straight-alpha RGBA8; `tobytes()` → PNG |
| `page.get_images()` / extract | `page.get_images()` | returns drawn images with bbox; JPEG passthrough |
| `doc.select`, `delete_page(s)`, `copy_page`, `new_page` | same | `select` with repeats duplicates pages |
| `doc.insert_pdf(src, from_page=, to_page=, start_at=)` | same | |
| `doc.get_toc()` / `set_toc()` | same | pages 1-based (both) |
| `doc.save(garbage=4, deflate=True)` | `doc.save(garbage=True, deflate=True, object_streams=True)` | `garbage` is a bool |
| `doc.save(encryption=…, user_pw=…)` | `doc.save(user_pw=…, owner_pw=…, permissions=…)` | AES-256 only |
| `doc.needs_pass` / `authenticate()` | same | same return semantics (0/1/2/4/6) |
| `page.rect / rotation / set_rotation` | same | |
| `page.insert_image(rect, filename=)` | same | JPEG/PNG only; no `pixmap=` — convert via Pillow |
| `page.show_pdf_page(rect, src, pno)` | same | same-document overlay unsupported (copy first) |
| `page.insert_text(point, text, fontsize=, fontname=)` | same | standard-14 abbreviations (`helv` …); WinAnsi only |
| `page.add_highlight_annot(...)` | same | appearance stream always generated |
| `doc.embfile_add / names / get / del` | same | |
| `doc.get_page_labels / set_page_labels`, `page.get_label` | same | |
| `page.widgets()` / widget objects | `doc.get_form_fields()` / `doc.set_form_field(name, value)` | document-level; NeedAppearances |
| `pymupdf4llm.to_markdown(doc)` | `doc.to_markdown()` | built in, MIT |

## Behavioral differences { #behavioral-differences }

- **Coordinates** are top-left-origin display space in both libraries, and in
  pylopdf this includes rotated pages consistently across extraction, search,
  drawing and rendering.
- **Types**: `Rect` is an immutable `NamedTuple` (`x0, y0, x1, y1`, plus
  `width` / `height`). There are no `Point` / `Matrix` / `Quad` classes — APIs
  take plain tuples and `scale=` / `dpi=` keywords.
- **Stale pages**: after structural changes (delete / insert / reorder),
  previously fetched `Page` objects raise `StalePageError` instead of silently
  pointing at a different page. Re-fetch with `doc[i]`.
- **Exceptions**: `PdfError` (a `ValueError` subclass) is the base;
  `PasswordError`, `DocumentClosedError`, `EncryptedDocumentError`,
  `StalePageError` refine it. `except ValueError` keeps working.
- **`get_text` options** are limited to `text` / `words` / `blocks` / `dict`
  (no `html` / `rawdict` / `xml`). Span dicts carry `font` and pymupdf-style
  `flags` (bold/italic/serif/mono) for embedded fonts.
- **Form filling** sets values + `NeedAppearances`; viewers draw the values.
  pylopdf's own renderer does not regenerate widget appearances.
- **Vertical writing** reading order is not reconstructed yet.

## Deliberately not implemented — use the ecosystem { #deliberate-scope }

| pymupdf feature | pylopdf answer |
|---|---|
| Story API / `insert_htmlbox` (typesetting) | typst via typst-py — [recipe](ecosystem.md) |
| OCR (`get_textpage_ocr`, needs Tesseract installed) | any OCR engine + `insert_ocr_text_layer` |
| Digital signatures | pyHanko (MIT) — [recipe](ecosystem.md) |
| Incremental save | not planned (qpdf/pikepdf-style rewrite philosophy); pyHanko covers the signature use case |
| Opening XPS / EPUB / CBZ / images | out of scope — PDF only |

## Worked example { #worked-example }

```python
# pymupdf
import fitz
doc = fitz.open("in.pdf")
page = doc[0]
for rect in page.search_for("total"):
    page.add_highlight_annot(rect)
pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
pix.save("page.png")
doc.save("out.pdf", garbage=4, deflate=True)
```

```python
# pylopdf
import pylopdf
doc = pylopdf.open("in.pdf")
page = doc[0]
page.add_highlight_annot(page.search_for("total"))   # takes the whole list
with open("page.png", "wb") as f:
    f.write(page.get_pixmap(scale=2).tobytes())
doc.save("out.pdf", garbage=True, deflate=True)
```
