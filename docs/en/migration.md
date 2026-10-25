---
title: Migrating from pymupdf
description: Port pymupdf code to pylopdf with the migration tool, a name-by-name mapping, and the behavior differences to watch for.
---

# Migrating from pymupdf

pylopdf aims to do everything pymupdf can do with PDF files, through an API
of its own. It is not a drop-in replacement: it opens **PDF files only** (not
XPS, EPUB, or images), changes a few behaviors on purpose, and its next release
redesigns the API, which the migration tool follows as each part lands.
Documents and their pages, saving, reading text, tables, and OCR already use
the new API: `pylopdf.open(source)`, `doc.pages`, `doc.save()`, `page.text()`,
`page.layout()`, `page.tables()`, and `page.ocr()`. This page lists what
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
`embfile_*`, the `xref_*` stream readers, text and table reading, drawings
and image placements, and the in-place `Rect`/`Point`/`Matrix` methods), and
adds a `# TODO(pylopdf): …` comment above every statement that still needs a
person. It then lists those places as
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
| `doc.metadata`, `set_metadata(m)` | `doc.metadata`, `doc.metadata.update(m)` | A live mapping of the entries present: read an absent entry with `.get(key, "")`, remove one with `del doc.metadata[key]`. `created` and `modified` replace `creationDate` and `modDate`, and `doc.version` and `doc.encryption_method` replace `format` and `encryption` |
| `doc.get_xml_metadata()`, `set_xml_metadata(xml)`, `del_xml_metadata()` | `doc.xmp`, `doc.xmp = xml`, `del doc.xmp` | `None` without a packet; reads and writes stop at 1 MiB |
| `doc.language`, `set_language(tag)` | `doc.language`, `doc.language = tag` | The complete tag: pymupdf keeps at most three letters, so `zh-TW` and `zh-HK` both become `zh-Hant`; `del doc.language` removes it |
| `doc.pagemode`, `pagelayout`, `markinfo` and their `set_*` methods | `doc.page_mode`, `page_layout`, `mark_info`, assigned | `PageMode` and `PageLayout` enums and a `MarkInfo` record; names are matched exactly, where pymupdf ignored case |
| `doc.has_links()` | `any(page.links for page in doc.pages)` | |
| `doc.has_annots()` | `any(page.annotations for page in doc.pages)` | |
| `doc.is_closed` | Same | |
| `doc.name` | `doc.path` | A `pathlib.Path`, or `None` for a document opened from bytes or created empty; `is_pdf` is gone because every document is a PDF |
| `doc.needs_pass`, `doc.is_encrypted` | `doc.needs_password` | True until the document is unlocked |
| `doc.authenticate(password)` | `doc.unlock(password)` | Returns `Access.USER` or `Access.OWNER` and raises `PasswordError` for a wrong password instead of returning 0 |
| `doc.permissions`, `is_dirty` | Same | `permissions` is a `Permissions` flag value: `Permissions.ALL` instead of `-4` |
| `doc.save(garbage=4, deflate=True)` | `doc.save(dedupe=True, renumber=True)` | Saving compresses streams and removes unused objects by default; `garbage=0` becomes `prune=False` and `deflate=False` `compress=False`. Pages, annotations, form fields, and layers are never merged |
| `doc.save(encryption=PDF_ENCRYPT_AES_256, user_pw=…, owner_pw=…, permissions=…)` | `doc.save(encryption=Encryption(user_password=…, owner_password=…, permissions=…))` | `PDF_ENCRYPT_AES_128` adds `method="aes-128"`; RC4 is not written |
| `doc.save(encryption=PDF_ENCRYPT_KEEP)`, pymupdf's default | `doc.save(encryption=Encryption.KEEP)` | pylopdf's default, `None`, writes no encryption; `KEEP` keeps AES-128 and AES-256 and refuses RC4 |
| `doc.tobytes(...)`, `doc.write(...)` | `doc.to_bytes(...)` | The options of `save()` |
| `doc.save(use_objstms=True)` | `doc.save(object_streams=True)` | |
| `doc.saveIncr()`, `doc.save(name, incremental=True)` | `doc.save(path, incremental=True)` | Any path, including the file the document came from; refuses `dedupe`, `renumber`, `object_streams`, and `encryption` |
| `doc.can_save_incrementally()` | `doc.can_save_incrementally` | A property |
| `doc.ez_save(path)` | `doc.save(path, dedupe=True, renumber=True, object_streams=True)` | |
| `doc.save(no_new_id=True)`, `expand=255`, `compression_effort=n` | `doc.save(keep_id=True)`, `decompress=True, compress=False`, `compression_level=…` | `compression_level` is zlib's 0 to 9 where `compression_effort` was 0 to 100 |
| `doc.journal_enable()`, `journal_start_op(name)` … `journal_stop_op()` | `with doc.transaction(name):` | Always available; a block that raises is undone |
| `doc.journal_undo()`, `journal_redo()`, `journal_can_do()` | `doc.history.undo()`, `redo()`, `can_undo`, `can_redo` | |
| `doc.journal_position()`, `journal_op_name(i)` | `doc.history.position`, `doc.history.steps[i]` | |
| `doc.journal_save()`, `journal_load()`, `save_snapshot()` | None | Steps stay in memory, within `doc.history.max_size` |
| `doc.select(indexes)` | `doc.pages.select(indexes)` | Also removes form fields and tagged-PDF structure of the pages left out |
| `doc.delete_page(i)`, `delete_pages(a, b)`, `del doc[i]` | `del doc.pages[i]`, `del doc.pages[a:b + 1]` | Keep the other pages of a list with `doc.pages.select(...)` |
| `doc.new_page(pno, width=, height=)` | `doc.pages.new((width, height), index=pno)` | Appended unless `index` is given |
| `paper_size("a4")`, `paper_size("a4-l")`, `paper_rect("a4")` | `PaperSize.A4`, `PaperSize.A4.landscape`, `Rect(0, 0, *PaperSize.A4)` | |
| `get_pdf_now()` | `pdf_date()` | `pdf_date(value)` formats a given `datetime`, and `parse_pdf_date(text)` reads one back |
| `doc.insert_page(pno, text, ...)` | `doc.pages.new(size, index=pno)`, then `page.draw()` and `canvas.text(...)` | |
| `doc.copy_page(pno, to)`, `fullcopy_page(pno, to)` | `doc.pages.insert(to, doc.pages[pno])` | pylopdf never shares one page object between two pages |
| `doc.move_page(pno, to)` | `doc.pages.move(pno, index)` | `index` is where the page ends up; pymupdf inserted before page `to` |
| `doc.insert_pdf(docsrc, from_page=a, to_page=b, start_at=i)` | `doc.pages.insert(i, docsrc.pages[a:b + 1])`, `doc.pages.extend(docsrc.pages)` | Imported form fields whose names clash are renamed `name+N`; no `widgets=` or `join_duplicates=` |
| `doc.get_toc()`, `set_toc(toc)` | `doc.outline.entries()`, `doc.outline = entries` | `OutlineEntry` records whose `destination` is a `Destination` with a page number that starts at 0, instead of `[level, title, page]` lists |
| `doc.outline`, `item.next`, `item.down` | `doc.outline`, `item.children`, `doc.outline.walk()` | `doc.outline` is the sequence of top-level `OutlineItem` handles instead of the first item |
| `doc.set_toc_item(i, ...)`, `del_toc_item(i)` | Assign `title`, `destination`, `color`, `bold`, `italic`, or `is_open` on an `OutlineItem` | `del_toc_item` grayed the item out and removed its target |
| `doc.get_page_labels()`, `set_page_labels(rules)`, `page.get_label()` | `doc.page_labels`, `page.label` | `PageLabelRange(start, style, prefix, first)` records instead of dictionaries |
| `PDF_PAGE_LABEL_DECIMAL`, `PDF_PAGE_LABEL_ROMAN_LC`, ... | `PageLabelStyle.DECIMAL`, `PageLabelStyle.LOWER_ROMAN`, ... | `PDF_PAGE_LABEL_NONE` is `style=None` |
| `doc.get_outline_xrefs()` | `[item.xref for item in doc.outline.walk()]` | |
| `doc.get_page_numbers(label)` | `doc.pages.labeled(label)` | |
| `page.set_rotation(deg)` | `page.rotation = deg` | |
| `page.remove_rotation()` | Same | Returns the matrix from the old user space to the new one, where pymupdf returned its inverse |
| `page.mediabox`, `set_mediabox(r)` | `page.boxes.media`, `page.boxes.media = r` | |
| `page.cropbox`, `artbox`, `bleedbox`, `trimbox`, `set_cropbox(r)`, `set_artbox(r)`, ..., `doc.page_cropbox(pno)` | `page.boxes.crop`, `.art`, `.bleed`, `.trim`, each assignable | PDF user space as stored, where pymupdf flips y against the top of the MediaBox |
| `page.transformation_matrix`, `rotation_matrix`, `derotation_matrix` | `page.user_to_display` | Maps PDF user space to display space, the rotation included |
| `doc.embfile_add/names/get/del/upd/info/count` | `doc.attachments` | A mapping: `add(name, data, description=…)`, `list(…)`, `[name].read()`, `del …[name]`, `[name].write(data)`, `len(…)`; `desc=` is `description=`, `creation_date` and `mod_date` are `created` and `modified`, and `embfile_upd(filename=, desc=)` assigns `filename` and `description` |
| `doc.stream` | The bytes passed to `pylopdf.open`, or `doc.to_bytes()` | pylopdf does not keep the input bytes |
| `doc.get_ocgs()`, `add_ocg(name, on=, intent=)` | `doc.layers`, `doc.layers.add(name, visible=, intent=)` | `Layer` handles instead of a dictionary keyed by xref; `intent` is a tuple of names |
| `PDF_OC_ON`, `PDF_OC_OFF`, `PDF_OC_TOGGLE` | `layer.is_visible = True`, `False`, `not layer.is_visible` | |
| `doc.get_layers()` | `doc.layers.configurations` | `LayerConfiguration` handles, the default configuration first |
| `doc.get_layer(config)`, `set_layer(config, ...)` | `doc.layers.configurations[i]` and its assignable `base_state`, `on`, `off`, `locked`, `radio_groups` | Index 0 is the default configuration, where pymupdf used -1; arrays hold `Layer` handles |
| `doc.add_layer(name, creator, on)`, `switch_layer(config)` | `doc.layers.configurations.add(name, creator=, on=)`, `configuration.apply()` | |
| `doc.get_ocmd(xref)`, `set_ocmd(...)` | `LayerMembership(layers, policy=, expression=)` read from and assigned to `layer` properties | A value instead of an xref; `ve=` is `expression=LayerExpression(...)` |
| `doc.get_oc(xref)`, `set_oc(xref, oc)`, `annot.get_oc()`, `annot.set_oc(oc)` | `doc.images[xref].layer`, `annotation.layer`, `link.layer`, `widget.layer` | Assign a `Layer` or a `LayerMembership`, or `del`; a Form XObject's `/OC` goes through `doc.objects[xref]` |
| `page.get_oc_items()` | `page.resources.layers` | A dictionary from resource names to layers and memberships |
| `doc.layer_ui_configs()`, `set_layer_ui_config(n, action)`, `PDF_LAYER_UI_*` | `configuration.order` and `radio_groups`, `layer.is_visible` | `order` is the tree of `Layer` handles and `LayerOrder(label, items)` values that the flat list was made from |
| `doc.xref_*`, `pdf_catalog()`, `pdf_trailer()` | `doc.objects`, `doc.catalog`, `doc.trailer` | Typed Python values instead of PDF syntax; see [Low-level objects](api.md#low-level-objects) |

### Text, rendering, and images { #mapping-text }

| pymupdf | pylopdf | Notes |
|---|---|---|
| `page.get_text()`, `get_text("text")` | `page.text()` | |
| `page.get_text("words")` | `page.words()` | `Word` records with `text`, `bbox`, `block`, `line`, and `index` instead of tuples |
| `page.get_text("blocks")` | `page.layout().blocks` | `Block` records with `text`, `bbox`, and `lines` instead of tuples |
| `page.get_text("dict")`, `get_text("rawdict")` | `page.layout().to_dict()`, `page.layout(chars=True).to_dict()` | The same dictionaries; `page.layout()` itself returns records |
| `page.get_text("json")`, `get_text("rawjson")` | `json.dumps(page.layout().to_dict())`, `json.dumps(page.layout(chars=True).to_dict())` | |
| `page.get_text("html")`, `get_text("xml")` | `page.layout().to_html()`, `page.layout(chars=True).to_xml()` | One page each; no `ConversionHeader` or `ConversionTrailer` |
| `page.get_text("xhtml")` | `page.markdown()` | The semantic form of headings, paragraphs, lists, and tables |
| `page.get_textpage()`, `TextPage`, `page.extend_textpage()` | The page's own `text()`, `words()`, `layout()`, and `search()` | There is no text-page object: each page caches its interpretation, which every view reads |
| `doc.search_page_for(i, text)` | `doc.pages[i].search(text)` | |
| `page.get_textbox(rect)` | `page.text(clip=rect)` | Keeps the characters whose box's center lies inside |
| `page.get_text_selection(p1, p2, clip)` | `page.selection(p1, p2, clip=clip)` | From the character nearest each point, in reading order |
| `page.search_for(text, clip=…, quads=…)` | `page.search(text, clip=…, quads=…)` | At most 4,096 matches unless `max_hits=` is raised |
| `recover_quad()`, `recover_span_quad()`, `recover_line_quad()`, `recover_char_quad()` | `span.quad`, `line.quad` of `page.layout()` | Characters have no quad of their own; `page.search(text, quads=True)` gives the quad of any run of characters |
| `page.find_tables()` | `page.tables()` | A list of `Table` records: `table.rows` replaces `extract()`, `len(table.rows)` replaces `row_count`, and `table.cells` holds the boxes row by row. No tuning keywords, `header`, or `to_pandas()` yet |
| `page.get_textpage_ocr()` | `page.ocr()`, `page.apply_ocr()` | Built-in offline OCR; see [Offline OCR](ocr.md) |
| `pymupdf4llm.to_markdown(doc)` | `doc.markdown()` | Built in |
| `page.get_pixmap(matrix=…, dpi=…, annots=…)` | `page.pixmap(dpi=…, annotations=…)` | Pass `dpi` or `matrix`, not both; a translation moves no pixels, and the pixmap has no origin; `colorspace=` takes `Colorspace.GRAY` or `Colorspace.RGB`, not CMYK; `doc.get_page_pixmap(i)` is `doc.pages[i].pixmap()` |
| `pix.samples`, `pix.samples_mv` | `bytes(pix)`, `memoryview(pix)` | `numpy.asarray(pix)` reads the pixels without a copy, shaped `(height, width, channels)` |
| `pix.n` | `pix.channels` | |
| `pix.tobytes(output, jpg_quality=…)`, `pix.save(path, output, jpg_quality=…)` | `pix.to_bytes(format, quality=…)`, `pix.save(path, format=…, quality=…)` | PNG and JPEG; other formats belong to Pillow |
| `pix.width`, `height`, `alpha`, `stride`, `colorspace` | Same | `colorspace` is `Colorspace.GRAY` or `Colorspace.RGB`; `csRGB` and `csGRAY` are `Colorspace.RGB` and `Colorspace.GRAY` |
| `page.get_svg_image()` | `page.svg()` | Text is always drawn as paths, at 72 dpi |
| `page.get_images()` | `page.resources.images` | `ImageObject` handles with `xref`, `width`, `height`, `bits`, `colorspace`, `filters`, and masks instead of tuples |
| `page.get_fonts()`, `doc.get_page_fonts(i)` | `page.resources.fonts` | `FontObject` handles with `xref`, `name`, `subtype`, `encoding`, and `is_embedded` instead of tuples |
| `doc.extract_font(xref)` | `doc.fonts[xref].extract()` | A `FontProgram` with `format` and `data`, or `None` for a font that is not embedded |
| `doc.get_char_widths(xref)` | `doc.fonts[xref].widths` | The advances in em indexed by character code |
| `page.get_xobjects()`, `doc.get_page_xobjects(i)` | `page.resources.forms` | `FormObject` handles with `xref`, `bbox`, and `matrix` |
| `doc.extract_image(xref)` | `doc.images[xref].extract()` | An `EncodedImage` with `format`, `data`, and `resolution`; the size, color space, and masks are properties of `doc.images[xref]` |
| `pymupdf.Pixmap(doc, xref)` | `doc.images[xref].to_pixmap()` | `alpha=True` applies the image's masks |
| `pymupdf.Pixmap(filename)`, `pymupdf.Pixmap(stream)` | `Pixmap.from_file(path)`, `Pixmap.from_bytes(data)` | PNG and JPEG; other formats belong to Pillow, then `Pixmap.from_array(numpy.asarray(image))` |
| `page.get_image_info()` | `page.content(text_runs=False, paths=False).images` | `Image` records with `xref`, `bbox`, `matrix` (pymupdf's `transform`), size, color space, mask, and resolution; no `hashes=` yet |
| `page.get_drawings()`, `get_cdrawings()` | `page.content(text_runs=False, images=False).paths` | `Path` records with `segments`, `fill`, `fill_rule`, `stroke`, and `bbox`; each path's `context` holds the clips, groups, and marked content that `extended=True` listed as entries of their own |
| `page.cluster_drawings(x_tolerance=…, y_tolerance=…)` | `page.content(clip=page.rect).path_clusters(x_tolerance=…, y_tolerance=…)` | Boxes of nearby paths, sorted from top to bottom |
| `page.get_texttrace()`, `get_bboxlog()`, `get_displaylist()`, `run()`, `get_image_rects()` | `page.content()` | Text runs with their glyphs, paths, and images as immutable values; see [Page content](api.md#content) |
| `doc.rewrite_images(dpi_threshold=, dpi_target=, quality=)` | Same | Re-encodes as JPEG only, so resampling needs `quality`; returns statistics instead of `None` |
| `doc.subset_fonts(verbose=, fallback=)` | `doc.subset_fonts()` | TrueType programs for now, of Identity-encoded CID fonts and simple TrueType fonts; returns a `FontSubsetResult` instead of `None` |

### Drawing and annotations { #mapping-drawing }

| pymupdf | pylopdf | Notes |
|---|---|---|
| `page.insert_text(point, text)`, `insert_textbox(rect, text)` | `canvas.text(text, at)`, `canvas.textbox(rect, text)` | Inside `with page.draw() as canvas:`. `fontsize=` becomes `size=`, `color=` `fill=Color(...)`, `lineheight=` `line_height=`, and `fontname=`, `fontfile=`, or `fontbuffer=` a `Font` passed as `font=`; `align=` takes `"left"`, `"center"`, `"right"`, or `"justify"`, and `rotate=` turns the text clockwise, where pymupdf turns it anticlockwise, so `rotate=90` becomes `rotate=270`. `textbox` returns a `TextFit` whose `spare_height` is negative when nothing was drawn; see [Writing text](api.md#writing-text) |
| `pymupdf.Font(...)` | `Font.standard(name)`, `Font.from_file(path)`, `Font.from_bytes(data)`, `Font.installed(language=...)` | Standard 14 fonts by PostScript name, such as `"Helvetica"` for `"helv"`, OpenType files, and installed font extras; pymupdf's other built-in fonts and `script=`, `ordering=`, and `embed=` are not available |
| `font.text_length(text, fontsize)`, `pymupdf.get_text_length(text, fontname, fontsize)` | `font.text_width(text, size)` | `size` has no default; `font.text_height(text, size, width=...)` also measures wrapped text as `canvas.textbox` draws it |
| `font.has_glyph(ord(c))` | `font.has_glyph(c)` | Takes the character and returns a bool, not the glyph number |
| `font.glyph_advance(ord(c))`, `font.char_lengths(text, fontsize)` | `font.char_widths(c, 1)[0]`, `font.char_widths(text, size)` | Each character's advance drawn alone; `size` has no default |
| `font.glyph_bbox(ord(c))`, `font.valid_codepoints()`, `font.buffer`, `font.flags` | `font.char_bbox(c)`, `font.chars()`, `font.program()`, `font.is_monospaced` | `chars()` returns the characters as one string; the other flags are `is_bold`, `is_italic`, and `is_serif` |
| `pymupdf.unicode_to_glyph_name(code)`, `glyph_name_to_unicode(name)` | `pylopdf.glyph_name(char)`, `pylopdf.glyph_text(name)` | Take and return characters and strings, not code points; `glyph_text` returns `""` for a name that stands for no character |
| `pymupdf.Base14_fontnames`, `Base14_fontdict` | `typing.get_args(pylopdf.StandardFontName)` | The 14 PostScript names that `Font.standard()` takes |
| `page.insert_image(rect, filename=/stream=/pixmap=)` | `canvas.image(rect, source)` | Inside `with page.draw() as canvas:`; the source is the path, JPEG or PNG bytes, or `Pixmap`, `keep_proportion=` becomes `keep_aspect=`, and `rotate=` turns the image clockwise, where pymupdf turns it anticlockwise. The same image drawn twice is stored once, `xref=` becomes `canvas.image(rect, doc.images[xref])`, and `page.content().images` reports each placement's `xref` |
| `page.replace_image(xref, filename=/stream=/pixmap=)` | `doc.images[xref].replace(source)` | Every placement of the image, on any page, draws the new one; the image keeps its layer and structure parent, and the page gets no extra content stream |
| `page.show_pdf_page(rect, docsrc, pno)` | `canvas.page(rect, docsrc.pages[pno])` | The page can belong to the same document. `keep_proportion=` becomes `keep_aspect=`, `clip=` is a rectangle in the source page's display coordinates, and `rotate=` turns the page clockwise, where pymupdf turns it anticlockwise, so `rotate=90` becomes `rotate=270` |
| `page.new_shape()`, `shape.draw_*`, `finish()`, `insert_text()`, `commit()` | `with page.draw() as canvas:` | The canvas draws paths and text in the order of the calls and writes them when the block ends; `finish()` styles become each call's `fill=` and `stroke=`; see [Canvas](api.md#canvas) |
| `page.draw_line()`, `draw_rect()`, `draw_circle()`, `draw_oval()`, `draw_polyline()`, `draw_bezier()` | `canvas.line()`, `rect()`, `circle()`, `ellipse()`, `polygon(close=False)`, `bezier()` | `color=`, `width=`, `dashes=`, `lineCap=`, and `lineJoin=` become `stroke=Stroke(paint=Color(...), ...)`, `fill=` a `Color`, and the opacities a `Color`'s alpha; `rect(radius=)` is in points, where pymupdf's is a fraction of the shorter side |
| `page.draw_quad()`, `draw_sector()`, `draw_curve()`, `draw_squiggle()`, `draw_zigzag()` | `canvas.polygon()`, `canvas.path(PathBuilder().sector(...))`, `canvas.bezier()`, `PathBuilder().wave()`, `PathBuilder().zigzag()` | `PathBuilder` builds the geometry, with sector angles in degrees clockwise from the x axis |
| `page.annots()`, `page.first_annot`, `annot.type`, `rect`, `info`, `colors`, `flags`, `parent` | `page.annotations`, `annotation.type`, `rect`, `contents` and the other texts, `color`, `interior_color`, `flags`, `page` | `Annotation` handles with typed properties: `type` is an `AnnotationType` member and the `info` and `colors` dictionaries become properties. Links and widgets are not listed, and a popup belongs to its annotation; the properties are read-only for now |
| `page.add_highlight_annot(quads)`, `page.delete_annot(annot)` | `page.annotations.add(Highlight(quads))`, `del page.annotations[i]` | `color` is a `Color` and `content` becomes `contents`; deleting an annotation removes its popup |
| `page.add_underline_annot(quads)`, `add_strikeout_annot(quads)`, `add_squiggly_annot(quads)` | `page.annotations.add(Underline(quads))`, `StrikeOut(quads)`, `Squiggly(quads)` | as for highlights |
| `page.add_text_annot(point, text, icon=)` | `page.annotations.add(Note(point, contents=text, icon=NoteIcon...))` | the icon is a `NoteIcon` member |
| `annot.popup_rect`, `set_popup(rect)`, `has_popup`, `is_open`, `set_open()`, `irt_xref`, `set_irt_xref()`, `delete_responses()` | `annotation.popup_rect` (assignable, `del` removes), `is_open` (assignable), `in_reply_to` (assignable), `replies` | replies are removed with `del page.annotations[...]` |
| `page.add_freetext_annot(rect, text, fontsize=, fontname=, text_color=, fill_color=, align=, rotate=, callout=, line_end=)`, `PDF_ANNOT_Q_*` | `page.annotations.add(FreeText(rect, text, size=, font=Font.standard(...), text_color=Color(...), color=Color(...), align='left', rotate=, callout=[...], callout_ending=LineEnding...))` | `rotate` turns clockwise as the page is displayed; text that does not fit raises |
| `annot.rotation`, `PDF_ANNOT_IT_*` | `annotation.rotation`, `annotation.intent` | `rotation` is relative to the displayed page |
| `page.add_stamp_annot(rect, stamp=)`, `STAMP_*`, `page.add_caret_annot(point)` | `page.annotations.add(Stamp(rect, icon=StampIcon...))`, `StampIcon`, `page.annotations.add(Caret(point))` | stamps are `StampIcon` members, not integers |
| `page.add_file_annot(point, buffer, filename, desc=)`, `annot.file_info`, `get_file()`, `update_file(buffer)` | `page.annotations.add(FileAttachment(point, data, filename=..., description=...))`, `annotation.file`, `file.read()`, `file.write(data)` | `file` is an `AnnotationFile`; a new name or description goes through `annotation.obj['FS']` |
| `page.add_redact_annot(quad, text=, fill=, text_color=, fontsize=)` | `page.annotations.add(Redaction(quad, overlay_text=..., fill=Color(...), text_color=Color(...), size=...))` | `Redaction` fills with black unless `fill=` says otherwise, where pymupdf fills with white; applying redactions comes with `page.redact` |
| `annot.set_rect()`, `set_info()`, `set_colors()`, `set_border()`, `set_opacity()`, `set_flags()`, `set_blendmode()`, `set_line_ends()`, `set_name()`, `set_rotation()`, `set_language()`, `update()` | assignments to `annotation.rect`, `contents`, `author`, `subject`, `color`, `interior_color`, `border`, `opacity`, `flags`, `blend_mode`, `line_endings`, `icon`, `rotation`, `language` | each assignment draws the appearance at once; there is no `update()` |
| `annot.get_pixmap()`, `get_text()`, `get_textbox(rect)`, `get_textpage()`, `annot.parent` | `annotation.page.pixmap(clip=annotation.rect)`, the words of `page.words()` whose `bbox` meets the rectangle, `page.layout()`, `annotation.page` | |
| `doc.resolve_names()`, `doc.resolve_link(uri)` | `doc.destinations`, `doc.destinations[name]` | values are `Destination` records in display coordinates |
| `page.widgets()`, `first_widget`, `load_widget(xref)`, `widget.next` | `page.widgets`, `field.widgets` | `Widget` handles; a widget's field is `widget.field` |
| `widget.border_color`, `border_width`, `border_style`, `border_dashes`, `fill_color`, `text_color`, `text_font`, `text_fontsize`, `button_caption`, `button_states()`, `on_state()`, `field_display`, `rb_parent`, `update()` | `widget.border_color`, `border` (a `Border`), `background_color`, `text_color`, `font`, `font_size`, `caption`, `button_states`, `on_state`, `flags`, `field` | assignments draw the widget at once; `font` is a Standard 14 `Font` |
| `widget.field_label`, `text_maxlen`, `reset()`, `doc.need_appearances()`, `doc.is_form_pdf` | `field.label`, `field.max_length`, `field.reset()`, `doc.form.need_appearances`, `len(doc.form)` | through `widget.field` |
| `widget.script`, `script_focus`, `script_blur`, `script_stroke`, `script_format`, `script_change`, `script_calc`, `text_format`, `PDF_WIDGET_TX_FORMAT_*` | `widget.scripts[ScriptTrigger.ACTIVATE]`, `FOCUS`, `BLUR`, and `field.scripts[ScriptTrigger.KEYSTROKE]`, `FORMAT`, `VALIDATE`, `CALCULATE` | each assignment writes the action at once; text formats are Acrobat format and keystroke scripts |
| `widget.is_signed`, `doc.get_sigflags()`, `SigFlag_SignaturesExist`, `SigFlag_AppendOnly` | `field.is_signed`, `doc.form.signature_flags`, `SignatureFlags.SIGNATURES_EXIST`, `SignatureFlags.APPEND_ONLY` | a document without a form reads no flags where pymupdf returns -1; the codemod rewrites the constants |
| `doc.bake(annots=True, widgets=True)` | `doc.flatten(annotations=True, widgets=True)` | the codemod rewrites the call |
| `page.repair_flat_rbgs()`, `page.refresh()` | `doc.form.repair_radio_groups()`, nothing | page views read the current document on every access |
| `page.add_widget(widget)`, `page.delete_widget(widget)` | `page.widgets.add(TextField(name, rect, ...))` and the other field specifications, `del page.widgets[i]`, `del doc.form[name]` | a specification replaces the configured `Widget` object |
| `page.add_rect_annot(rect)`, `page.add_circle_annot(rect)` | `page.annotations.add(Square(rect, ...))`, `page.annotations.add(Circle(rect, ...))` | colours, border, opacity, and blend mode are fields of the specification rather than later `set_colors`, `set_border`, and `update` calls |
| `page.add_line_annot(p1, p2)`, `add_polygon_annot(points)`, `add_polyline_annot(points)`, `add_ink_annot(handwriting)` | `page.annotations.add(LineAnnotation(p1, p2))`, `Polygon(points)`, `PolyLine(points)`, `Ink(handwriting)` | `set_line_ends` becomes `line_endings=(LineEnding..., LineEnding...)` |
| `annot.vertices`, `annot.line_ends`, `PDF_ANNOT_LE_*` (the codemod rewrites the constants) | `annotation.vertices` or `annotation.strokes` for ink, `annotation.line_endings`, `LineEnding` | points are display `Point` values |
| `annot.border`, `annot.blendmode`, `PDF_BM_*`, `PDF_BORDER_STYLE_*`, `set_border(clouds=)`, `PDF_BORDER_EFFECT_CLOUDY` | `annotation.border`, `annotation.blend_mode`, `BlendMode`, `BorderStyle`, `annotation.clouds` | `border` is a `Border` record or `None`; clouds belong to squares, circles, and polygons |
| `page.get_links()`, `page.links()`, `page.first_link`, `page.load_links()` | `page.links` | A sequence of `Link` handles with `rect` and `target` instead of dictionaries and a chain of `Link` objects |
| `page.insert_link(link)`, `page.update_link(link)`, `page.delete_link(link)` | `page.links.add(rect, target)`, assigning `link.rect` and `link.target`, `del page.links[i]` | A target is a URI string, `Destination(page, point, zoom)`, `RemoteDestination(file, page)`, `Launch(file)`, or `NamedAction(name)`, as in the outline; `isinstance` on it replaces the `LINK_*` kinds |
| `page.widgets()`, `widget.field_name`, `field_type`, `field_value`, `field_flags`, `choice_values`, `widget.update()` | `doc.form`, `field.name`, `type`, `value`, `flags`, `choices`, assigning `doc.form[name].value` | Fields are handled per document as `Field` handles; assigning `value` regenerates the appearances at once, and `doc.form.font` picks the face. Widgets as handles come with the Interaction milestone |

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
- **Text layout.** `canvas.textbox` spaces lines as `canvas.text` does,
  `line_height × size` apart, and needs exactly the height that
  `Font.text_height` measures, from the first line's ascender to the last
  line's descender. pymupdf's `insert_textbox` spaces later lines by its line
  height times the ascender and counts one more descender, so lines sit
  differently and some text fits that pymupdf refuses. `canvas.text` draws
  every line, where pymupdf's `insert_text` leaves out lines below the page.
- **Fonts are strict.** pylopdf raises instead of silently using another
  font: for example, when a font lacks a character, or when `is_bold=True`
  asks for a bold face that is not installed.
- **Merging forms.** `doc.pages.insert()` and `extend()` always copy form fields, like pymupdf's
  default `widgets=True`. Fields whose names already exist in the receiving
  document are renamed `name+1`, `name+2`, and so on, so copies of one form
  keep separate values.
- **Tables and Markdown.** `tables()` detects tables drawn with lines by
  default; pass `strategy="text"` for borderless tables. `markdown()`
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
page.annotations.add(pylopdf.Highlight(page.search("total")))  # takes the whole list
page.pixmap(matrix=pylopdf.Matrix(2, 2)).save("page.png")
doc.save("out.pdf", dedupe=True, renumber=True)
```
