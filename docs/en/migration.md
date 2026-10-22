---
title: Migrating from pymupdf
description: Port pymupdf code to pylopdf with the migration tool, a name-by-name mapping, and the behavior differences to watch for.
---

# Migrating from pymupdf

pylopdf aims to do everything pymupdf can do with PDF files, through an API
of its own. Many names are the same as pymupdf's today, and so are the data
shapes that most code depends on, such as `"words"` tuples, the `"dict"`
structure, and `search_for()` returning rectangles. It is not a drop-in
replacement: it opens **PDF files only** (not XPS, EPUB, or images), changes a
few behaviors on purpose, and its next release redesigns the API, which the
migration tool follows as each part lands. Documents and their pages already
use the new API: `pylopdf.open(source)` and `doc.pages`. This page lists what
carries over, what differs, and what to use instead of the parts pylopdf does
not implement yet.

## Automated migration { #codemod }

`python -m pylopdf.migrate` rewrites pymupdf programs in place. Install it
with the `migrate` extra:

```bash
pip install "pylopdf[migrate]"
python -m pylopdf.migrate src/
```

It replaces `import fitz` and `import pymupdf`, rewrites the replacements that
are mechanical (such as opening documents, the page operations that
`doc.pages` replaces, `get_toc`/`set_toc` with their page numbers,
`embfile_*`, the `xref_*` stream readers, and the in-place
`Rect`/`Point`/`Matrix` methods), and adds a `# TODO(pylopdf): …` comment above
every statement that still needs a person. It then lists those places as
`path:line: message`. The same command upgrades programs written for
pylopdf 0.15.

- `--diff` prints the changes without writing them.
- `--check` exits with status 1 when a file would change, for CI.

The tool cannot see the types of your variables, so review the diff. It
indexes, measures, and iterates a variable through `.pages` only when every
assignment to it opens a document, as in `doc = fitz.open(path)`; `doc[0]` on
a function parameter stays as it is. At run time, using a pymupdf name that
pylopdf does not have raises an `AttributeError` whose message names the
replacement or says that the feature is not supported yet, and `doc[0]` or
`for page in doc` raises a `TypeError` that points to `doc.pages`.

## Name mapping { #mapping }

"Same" means the call works unchanged; the notes list what differs.

### Documents and pages { #mapping-documents }

| pymupdf | pylopdf | Notes |
|---|---|---|
| `import pymupdf` (or `fitz`) | `import pylopdf` | |
| `pymupdf.open(path)`, `open(stream=…)`, `open("pdf", data)` | `pylopdf.open(path)`, `pylopdf.open(data)` | PDF only; `password=` is keyword-only; read a file object first |
| `pymupdf.open()`, `pymupdf.Document()` | `pylopdf.Document()` | An empty document |
| `doc[i]`, `doc.load_page(i)`, `len(doc)`, `doc.page_count`, iteration | `doc.pages[i]`, `len(doc.pages)`, iteration over `doc.pages` | `doc.pages` also takes slices, as `doc.pages(start, stop)` did |
| `page.number`, `page.parent` | `page.index`, `page.document` | |
| `doc.metadata`, `set_metadata()` | Same | |
| `doc.get_xml_metadata()`, `set_xml_metadata()`, `del_xml_metadata()` | Same | Reads and writes stop at 1 MiB by default; `max_size=None` lifts the limit |
| `doc.language`, `set_language(tag)` | `doc.language_tag`, `set_language_tag(tag)` | The complete tag: pymupdf keeps at most three letters, so `zh-TW` and `zh-HK` both become `zh-Hant`; the codemod rewrites `set_language` |
| `doc.pagemode`, `pagelayout`, `markinfo` and their `set_*` methods, `has_links()`, `has_annots()`, `is_closed` | Same | |
| `doc.name` | `doc.path` | A `pathlib.Path`, or `None` for a document opened from bytes or created empty; `is_pdf` is gone because every document is a PDF |
| `doc.needs_pass`, `doc.is_encrypted` | `doc.needs_password` | True until the document is unlocked |
| `doc.authenticate(password)` | `doc.unlock(password)` | Returns `Access.USER` or `Access.OWNER` and raises `PasswordError` for a wrong password instead of returning 0 |
| `doc.permissions`, `is_dirty` | Same | `permissions` is a `Permissions` flag value: `Permissions.ALL` instead of `-4` |
| `doc.save(garbage=4, deflate=True)` | `doc.save(dedupe=True, renumber=True)` | Saving compresses streams and removes unused objects by default; `garbage=0` becomes `prune=False` and `deflate=False` `compress=False`. Pages, annotations, form fields, and layers are never merged |
| `doc.save(encryption=PDF_ENCRYPT_AES_256, user_pw=…, owner_pw=…, permissions=…)` | `doc.save(encryption=Encryption(user_password=…, owner_password=…, permissions=…))` | AES-256 only |
| `doc.tobytes(...)`, `doc.write(...)` | `doc.to_bytes(...)` | The options of `save()` |
| `doc.save(use_objstms=True)` | `doc.save(object_streams=True)` | |
| `doc.select(indexes)` | `doc.pages.select(indexes)` | Also removes form fields and tagged-PDF structure of the pages left out |
| `doc.delete_page(i)`, `delete_pages(a, b)` | `del doc.pages[i]`, `del doc.pages[a:b + 1]` | Keep the other pages of a list with `doc.pages.select(...)` |
| `doc.new_page(pno, width=, height=)` | `doc.pages.new((width, height), index=pno)` | Appended unless `index` is given |
| `doc.copy_page(pno, to)`, `fullcopy_page(pno, to)` | `doc.pages.insert(to, doc.pages[pno])` | pylopdf never shares one page object between two pages |
| `doc.move_page(pno, to)` | `doc.pages.move(pno, index)` | `index` is where the page ends up; pymupdf inserted before page `to` |
| `doc.insert_pdf(docsrc, from_page=a, to_page=b, start_at=i)` | `doc.pages.insert(i, docsrc.pages[a:b + 1])`, `doc.pages.extend(docsrc.pages)` | Imported form fields whose names clash are renamed `name+N`; no `widgets=` or `join_duplicates=` |
| `doc.get_toc()`, `set_toc(toc)` | `doc.get_outline()`, `set_outline(entries)` | Page numbers start at 0 instead of 1 |
| `doc.get_page_labels()`, `set_page_labels()`, `page.get_label()` | Same | |
| `doc.get_page_numbers(label)` | `doc.pages.labeled(label)` | |
| `doc.embfile_add/names/get/del/upd/info/count` | `doc.attachments` | A mapping: `add(name, data, description=…)`, `list(…)`, `[name].read()`, `del …[name]`, `[name].write(data)`, `len(…)`; `desc=` is `description=` |
| `doc.xref_*`, `pdf_catalog()`, `pdf_trailer()` | `doc.objects`, `doc.catalog`, `doc.trailer` | Typed Python values instead of PDF syntax; see [Low-level objects](api.md#low-level-objects) |

### Text, rendering, and images { #mapping-text }

| pymupdf | pylopdf | Notes |
|---|---|---|
| `page.get_text()` | Same | Options `text`, `words`, `blocks`, `dict`, `rawdict`; no `html` or `xml` |
| `page.search_for(text)` | Same | Returns rectangles; no `quads=`; at most 4,096 results unless `max_hits=` is raised |
| `page.get_textpage_ocr()` | `page.get_text_ocr()`, `page.apply_ocr()` | Built-in offline OCR; see [Offline OCR](ocr.md) |
| `pymupdf4llm.to_markdown(doc)` | `doc.to_markdown()` | Built in |
| `page.get_pixmap(matrix=…, dpi=…)` | Same | Only scaling matrices; no `colorspace=` or `annots=` yet; `doc.get_page_pixmap(i)` is `doc.pages[i].get_pixmap()` |
| `pix.samples`, `width`, `height`, `n`, `alpha`, `stride`, `colorspace`, `save()` | Same | `save()` and `tobytes()` write PNG only |
| `page.get_svg_image()` | Same | Text is always drawn as paths |
| `page.get_images()`, `get_image_info()`, `doc.extract_image()` | Same | `get_image_info(hashes=True)` is not supported yet |
| `page.get_drawings()` | Same | No `extended=`; `page.content().paths` carries clips, groups, and marked content |
| `page.get_texttrace()`, `get_bboxlog()`, `get_cdrawings()`, `get_displaylist()`, `run()`, `get_image_rects()` | `page.content()` | Text runs with their glyphs, paths, and images as immutable values; see [Page content](api.md#content) |
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
  to a different page. Fetch it again with `doc.pages[i]`.
- **Exceptions.** `PdfError`, a subclass of `ValueError`, is the base class,
  so `except ValueError` keeps working. Input that is not a readable PDF,
  including empty input, raises `MalformedPdfError` where pymupdf raises
  `FileDataError` or `EmptyFileError`. A missing file raises Python's built-in
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
- **Merging forms.** `doc.pages.insert()` and `extend()` always copy form fields, like pymupdf's
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
| Converting images or CBZ archives to PDF | Planned; until then, convert them with Pillow or img2pdf |
| Opening XPS or EPUB | Not planned: pylopdf is a PDF library |

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
page = doc.pages[0]
page.add_highlight_annot(page.search_for("total"))   # takes the whole list
page.get_pixmap(matrix=pylopdf.Matrix(2, 2)).save("page.png")
doc.save("out.pdf", dedupe=True, renumber=True)
```
