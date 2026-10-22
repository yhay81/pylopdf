---
title: Migrating from pymupdf
description: Port pymupdf code to pylopdf with the migration tool, a name-by-name mapping, and the behavior differences to watch for.
---

# Migrating from pymupdf

pylopdf follows pymupdf's API: the same names mean the same things, and the
data shapes that most code depends on, such as `"words"` tuples, the `"dict"`
structure, and `search_for()` returning rectangles, match. It is not a drop-in
replacement yet. It covers part of pymupdf's API, opens **PDF files only**
(not XPS, EPUB, or images), and changes a few behaviors on purpose. This page
lists what carries over, what differs, and what to use instead of the parts
pylopdf does not implement.

## Automated migration { #codemod }

`python -m pylopdf.migrate` rewrites pymupdf programs in place. Install it
with the `migrate` extra:

```bash
pip install "pylopdf[migrate]"
python -m pylopdf.migrate src/
```

It replaces `import fitz` and `import pymupdf`, rewrites the replacements that
are mechanical (such as `get_toc`/`set_toc` with their page numbers,
`embfile_*`, the `xref_*` stream readers, `copy_page`, and the in-place
`Rect`/`Point`/`Matrix` methods), and adds a `# TODO(pylopdf): …` comment above
every statement that still needs a person. It then lists those places as
`path:line: message`.

- `--diff` prints the changes without writing them.
- `--check` exits with status 1 when a file would change, for CI.

The tool cannot see the types of your variables, so review the diff. At run
time, using a pymupdf name that pylopdf does not have raises an
`AttributeError` whose message names the replacement or says that the feature
is not supported yet.

## Name mapping { #mapping }

"Same" means the call works unchanged; the notes list what differs.

### Documents and pages { #mapping-documents }

| pymupdf | pylopdf | Notes |
|---|---|---|
| `import pymupdf` (or `fitz`) | `import pylopdf` | |
| `pymupdf.open(path)`, `open(stream=…)`, `open("pdf", data)` | Same | PDF only; `password=` is keyword-only |
| `doc[i]`, `len(doc)`, iteration | Same | |
| `doc.metadata`, `set_metadata()` | Same | |
| `doc.get_xml_metadata()`, `set_xml_metadata()`, `del_xml_metadata()` | Same | Reads and writes stop at 1 MiB by default; `max_size=None` lifts the limit |
| `doc.language`, `set_language(tag)` | `doc.language_tag`, `set_language_tag(tag)` | The complete tag: pymupdf keeps at most three letters, so `zh-TW` and `zh-HK` both become `zh-Hant`; the codemod rewrites `set_language` |
| `doc.pagemode`, `pagelayout`, `markinfo` and their `set_*` methods, `has_links()`, `has_annots()`, `is_closed`, `is_pdf`, `name` | Same | |
| `doc.needs_pass`, `authenticate()`, `permissions`, `is_dirty` | Same | Same return codes and values |
| `doc.save(garbage=4, deflate=True)` | Same | Pages, annotations, form fields, and layers are never merged by `garbage` |
| `doc.save(encryption=…, user_pw=…)` | Same | Only `PDF_ENCRYPT_NONE` and `PDF_ENCRYPT_AES_256`; a password without encryption raises instead of being ignored |
| `doc.select()`, `delete_page()`, `delete_pages()`, `fullcopy_page()`, `new_page()` | Same | Also removes form fields and tagged-PDF structure of deleted pages |
| `doc.copy_page(pno, to)` | `doc.fullcopy_page(pno, to)` | pylopdf never shares one page object between two pages |
| `doc.insert_pdf(docsrc, from_page=, to_page=, start_at=)` | Same | Imported form fields whose names clash are renamed `name+N`; no `widgets=` or `join_duplicates=` |
| `doc.get_toc()`, `set_toc(toc)` | `doc.get_outline()`, `set_outline(entries)` | Page numbers start at 0 instead of 1 |
| `doc.get_page_labels()`, `set_page_labels()`, `get_page_numbers()`, `page.get_label()` | Same | |
| `doc.embfile_add/names/get/del/upd/info/count` | `doc.attachments` | A mapping: `add(name, data, description=…)`, `list(…)`, `[name].read()`, `del …[name]`, `[name].write(data)`, `len(…)`; `desc=` is `description=` |
| `doc.xref_*`, `pdf_catalog()`, `pdf_trailer()` | `doc.objects`, `doc.catalog`, `doc.trailer` | Typed Python values instead of PDF syntax; see [Low-level objects](api.md#low-level-objects) |

### Text, rendering, and images { #mapping-text }

| pymupdf | pylopdf | Notes |
|---|---|---|
| `page.get_text()` | Same | Options `text`, `words`, `blocks`, `dict`, `rawdict`; no `html` or `xml` |
| `page.search_for(text)` | Same | Returns rectangles; no `quads=`; at most 4,096 results unless `max_hits=` is raised |
| `page.get_textpage_ocr()` | `page.get_text_ocr()`, `page.apply_ocr()` | Built-in offline OCR; see [Offline OCR](ocr.md) |
| `pymupdf4llm.to_markdown(doc)` | `doc.to_markdown()` | Built in |
| `page.get_pixmap(matrix=…, dpi=…)`, `doc.get_page_pixmap()` | Same | Only scaling matrices; no `colorspace=` or `annots=` yet |
| `pix.samples`, `width`, `height`, `n`, `alpha`, `stride`, `colorspace`, `save()` | Same | `save()` and `tobytes()` write PNG only |
| `page.get_svg_image()` | Same | Text is always drawn as paths |
| `page.get_images()`, `get_image_info()`, `doc.extract_image()` | Same | `get_image_info(hashes=True)` is not supported yet |
| `page.get_drawings()` | Same | No `extended=` clipping and group information |
| `doc.rewrite_images(dpi_threshold=, dpi_target=, quality=)` | Same | Re-encodes as JPEG only, so resampling needs `quality`; returns statistics instead of `None` |

### Drawing and annotations { #mapping-drawing }

| pymupdf | pylopdf | Notes |
|---|---|---|
| `page.insert_text()`, `insert_textbox()` | Same | Also accept `font=`, `fontbuffer=`, and `font_language=`; see [Text and fonts](api.md#fonts) |
| `pymupdf.Font(...)`, `font.text_length()`, `has_glyph()` | Same | Standard 14 fonts, OpenType files, and installed font extras; pymupdf's other built-in fonts and `script=`, `ordering=`, and `embed=` are not available |
| `page.insert_image(rect, filename=/stream=/pixmap=)` | Same | JPEG, PNG, or `Pixmap`; convert other formats with Pillow. The same image inserted twice is stored once, so `xref=` is not needed |
| `page.show_pdf_page(rect, docsrc, pno)` | Same | The source can be the same document |
| `page.new_shape()`, `shape.draw_*`, `finish()`, `insert_text()`, `commit()` | Same | `with page.new_shape() as shape:` commits automatically; `morph=` and `oc=` are not supported yet |
| `page.draw_line()`, `draw_rect()`, and the other `draw_*` | Same | Style options are keyword-only |
| `page.annots()`, `annot.type`, `rect`, `info`, `colors`, `flags` | Same | Read-only for now |
| `page.add_highlight_annot()`, `page.insert_link()` | Same | `insert_link` creates web links (`LINK_URI`) only |
| `page.widgets()` | `doc.get_form_fields()`, `doc.set_form_field(name, value)` | Forms are handled per document |

### Geometry { #mapping-geometry }

| pymupdf | pylopdf | Notes |
|---|---|---|
| `Point`, `Matrix`, `Rect`, `IRect`, `Quad` and their operators | Same | Immutable |
| `p.transform(m)`, `m.invert()` | `p = p * m`, `m = ~m` | |
| `r.include_point(p)`, `include_rect(o)`, `intersect(o)`, `normalize()`, `transform(m)` | `r = r | p`, `r | o`, `r & o`, `r.normalized()`, `r * m` | |
| `m.prerotate(deg)`, `prescale(sx, sy)`, `preshear(sx, sy)`, `pretranslate(tx, ty)` | `m = Matrix(deg) * m`, `Matrix(sx, sy) * m`, `Matrix(sx, sy, 1) * m`, `Matrix(1, 0, 0, 1, tx, ty) * m` | |

## Behavior differences { #behavioral-differences }

- **Geometry values cannot be changed.** `Point`, `Matrix`, `Rect`, `IRect`,
  and `Quad` are immutable named tuples. Assign the result of an operator
  instead of calling an in-place method. As in pymupdf, `p + q` adds
  coordinates and `Rect(0, 0, 0, 0)` is false, unlike plain tuples.
- **Stale pages raise.** After pages are added, removed, or reordered, a
  `Page` fetched earlier raises `StalePageError` instead of silently pointing
  to a different page. Fetch it again with `doc[i]`.
- **Exceptions.** `PdfError`, a subclass of `ValueError`, is the base class,
  so `except ValueError` keeps working. `FileDataError` and `EmptyFileError`
  keep pymupdf's names. A missing file raises Python's built-in
  `FileNotFoundError`, so replace `except pymupdf.FileNotFoundError` with
  `except FileNotFoundError`.
- **Rotated pages.** Extraction, search, drawing, and rendering all use the
  coordinates of the page as displayed, including on rotated pages.
- **`insert_text` near the page bottom.** pylopdf decides which lines fit using
  the visible page area (`page.rect`), while pymupdf measures down to the
  unrotated page's bottom edge. Both agree on ordinary unrotated pages.
- **Fonts are strict.** pylopdf raises instead of silently using another
  font: for example, when a font lacks a character, or when `is_bold=True`
  asks for a bold face that is not installed.
- **Merging forms.** `insert_pdf()` always copies form fields, like pymupdf's
  default `widgets=True`. Fields whose names already exist in the receiving
  document are renamed `name+1`, `name+2`, and so on, so copies of one form
  keep separate values.
- **Tables and Markdown.** `find_tables()` detects tables drawn with lines by
  default; pass `strategy="text"` for borderless tables. `to_markdown()`
  places detected tables in reading order and removes their text from the
  surrounding paragraphs.

## Not implemented: use these instead { #deliberate-scope }

| pymupdf feature | Alternative |
|---|---|
| Story API, `insert_htmlbox` (typesetting) | [typst](ecosystem.md#typesetting) |
| Digital signatures | [pyHanko](ecosystem.md#signatures) |
| Incremental save | Planned; until then pylopdf rewrites the whole file, and pyHanko handles signatures |
| Opening XPS, EPUB, CBZ, or images | Not planned: pylopdf is PDF only |

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
page.get_pixmap(matrix=pylopdf.Matrix(2, 2)).save("page.png")
doc.save("out.pdf", garbage=4, deflate=True)
```
