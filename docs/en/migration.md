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
| `import pymupdf` (`fitz` is the legacy name) | `import pylopdf` | |
| `pymupdf.open(path)` / `open(stream=…)` | `pylopdf.open(path)` / `open(stream=…)` | same shape; `password=` too, capped at 127 UTF-8 bytes |
| `doc[i]`, `len(doc)`, iteration | same | 0-based, negative indices |
| `doc.metadata` / `set_metadata` | same | same key names |
| `page.get_text()` | same | options: `text` / `words` / `blocks` / `dict` |
| `page.search_for(t)` | same, plus `max_hits=4096` | returns a bounded `list[Rect]`; 4,096-byte term limit, `max_hits=None` opt-out, no `quads=` |
| `page.get_pixmap(matrix=pymupdf.Matrix(2, 2))` | `page.get_pixmap(scale=2)` | or `dpi=144`; no Matrix class |
| `pix.samples / width / height / stride / save()` | same | always straight-alpha RGBA8; pylopdf's bounded `tobytes(max_size=64 MiB)` and streaming `save(path)` produce PNG, and `save` requires `.png` |
| `page.get_images()` / extract | `page.get_images()` | returns drawn images with bbox; JPEG passthrough |
| `page.get_drawings()` | same | typed path dictionaries; lines/cubics and common paint/stroke properties; no `extended=` clip/group hierarchy |
| `doc.rewrite_images(dpi_target=, quality=)` | `doc.compress_images(dpi=, quality=)` | pylopdf rewrites safe unmasked DeviceGray/DeviceRGB DCT or Flate rasters to JPEG; `dpi` directly caps the largest placement and there is no lossless conversion |
| `doc.select`, `delete_page(s)`, `copy_page`, `new_page` | same | `select` with repeats duplicates pages |
| `doc.insert_pdf(src, from_page=, to_page=, start_at=)` | same | |
| `doc.get_toc()` / `set_toc()` | same | pages 1-based (both) |
| `doc.save(garbage=4, deflate=True)` | `doc.save(garbage=True, deflate=True, object_streams=True)` | `garbage` is a bool |
| `doc.save(encryption=…, user_pw=…)` | `doc.save(user_pw=…, owner_pw=…, permissions=…)` | AES-256 only; each password stops at 127 UTF-8 bytes |
| `doc.needs_pass` / `authenticate()` | same | same return semantics (0/1/2/4/6) |
| `page.rect / rotation / set_rotation` | same | |
| `page.insert_image(rect, filename= / stream= / pixmap=, rotate=)` | same | JPEG passthrough, PNG alpha, direct rendered-RGBA `Pixmap` reuse, and clockwise right-angle rotation; convert other encoded formats with Pillow |
| `page.show_pdf_page(rect, src, pno)` | same | same-document sources use a native pre-edit snapshot; no serialize/open workaround |
| `page.insert_text(point, text, fontsize=, fontname=, fontfile=)` | same, plus `fontbuffer=` / `fontindex=` / `font_language=` | no source: standard-14 / WinAnsi or an installed script/locale font provider; other sources are shaped and subset-embedded |
| `page.insert_textbox(rect, text, align=, lineheight=)` | same, plus arbitrary `fontfile=` / `fontbuffer=` / `font_language=` | UAX #14 wrapping with the same optional provider selection; negative return means no drawing |
| `page.add_highlight_annot(...)` | same | appearance stream always generated |
| `doc.embfile_add / names / get / del` | same | |
| `doc.get_page_labels / set_page_labels`, `page.get_label` | same | |
| `page.widgets()` / widget objects | `doc.get_form_fields()` / `doc.set_form_field(name, value, fontfile=)` | document-level; native text/choice/checkbox/radio appearances |
| `page.get_textpage_ocr(...)` | `page.get_text_ocr(...)` / `page.apply_ocr(...)` | offline pure-Rust PP-OCR through `pylopdf[ocr]`; arbitrary positioned results still work with `insert_ocr_text_layer` |
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
- **Resource policy**: `DocumentLimits.web()` caps the complete PDF snapshot
  supplied to rendering and extraction at 64 MiB and cumulative positioned
  text at 65,536 glyph records. Custom policies can set
  `max_interpretation_size` and `max_text_glyphs`; `None` keeps the compatible
  unbounded behavior.
- **`get_text` options** are limited to `text` / `words` / `blocks` / `dict`
  (no `html` / `rawdict` / `xml`). Span dicts carry `font` and pymupdf-style
  `flags` (bold/italic/serif/mono) for embedded fonts.
- **Multicolumn text** follows deterministic whitespace gutters, reading
  top-to-bottom within each column and columns from left to right. Sub-em
  gutters require dense repeated lines with substantial text on both sides;
  aligned labels, dot leaders, and dense multi-separator rows remain row-major.
  A confirmed boundary follows small horizontal drift in scan/OCR text layers.
- **`Page.find_tables()`** reconstructs axis-aligned bordered grids from strokes
  or thin filled rectangles, including rectangular merged cells. Opt in to
  high-confidence borderless detection with `strategy="text"`; aligned
  multicolumn prose remains geometrically ambiguous. Use `clip=` to keep only
  complete tables in a known display-coordinate region, and inspect
  `Table.confidence` / `Table.diagnostics` when ranking borderless results.
- **`to_markdown()`** inserts complete bordered tables in reading order and
  removes their cell text from the surrounding prose. Pass
  `table_strategy="text"` to include conservative borderless candidates, or
  `None` to disable table conversion.
- **Form filling** writes values and native appearances that render in pylopdf
  and external viewers. WinAnsi auto-fits in Helvetica; pass an OpenType font
  for Unicode, or install one of the script/locale font extras. Use
  `font_language=` for locale-specific Han typography. Comb fields
  honor inherited `MaxLen` and alignment. Rich text, pushbuttons, and signatures
  remain outside the API.
- **Vertical CJK writing** is detected conservatively and read top-to-bottom,
  with columns ordered right-to-left. Ruby, warichu and mixed-orientation
  typography are not interpreted.

## Deliberately not implemented — use the ecosystem { #deliberate-scope }

| pymupdf feature | pylopdf answer |
|---|---|
| Story API / `insert_htmlbox` (typesetting) | typst via typst-py — [recipe](ecosystem.md) |
| Digital signatures | pyHanko (MIT) — [recipe](ecosystem.md) |
| Incremental save | not currently supported; pylopdf rewrites the file and keeps this on the watchlist; pyHanko covers signatures |
| Opening XPS / EPUB / CBZ / images | out of scope — PDF only |

## Worked example { #worked-example }

```python
# pymupdf
import pymupdf
doc = pymupdf.open("in.pdf")
page = doc[0]
for rect in page.search_for("total"):
    page.add_highlight_annot(rect)
pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
pix.save("page.png")
doc.save("out.pdf", garbage=4, deflate=True)
```

```python
# pylopdf
import pylopdf
doc = pylopdf.open("in.pdf")
page = doc[0]
page.add_highlight_annot(page.search_for("total"))   # takes the whole list
page.get_pixmap(scale=2).save("page.png")
doc.save("out.pdf", garbage=True, deflate=True)
```
