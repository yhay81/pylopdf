# pylopdf

[![PyPI](https://img.shields.io/pypi/v/pylopdf)](https://pypi.org/project/pylopdf/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/yhay81/pylopdf/blob/main/LICENSE)

> **Archived — migrate to [pyropdf](https://github.com/yhay81/pyropdf).**
>
> `pylopdf` is archived; `0.16.1` is its final release, and this repository no
> longer accepts issues, pull requests, or security fixes. Development
> continues in **[pyropdf](https://github.com/yhay81/pyropdf)**
> ([documentation](https://pyropdf.haya.works/) ·
> [PyPI](https://pypi.org/project/pyropdf/) ·
> [design](https://github.com/yhay81/pyropdf/blob/main/DESIGN.md)).

## Migrate to pyropdf

```bash
pip uninstall pylopdf
pip install pyropdf
```

`pyropdf` keeps `pylopdf`'s concept and API design, rebuilt on a single
versioned object store in Rust
([why the foundation changed](https://github.com/yhay81/pyropdf/blob/main/DESIGN.md#2-evidence-from-pylopdf)):

- Every engine reads the current document version from one store instead of
  reserializing a `lopdf` graph after each edit, so edits cost what they touch.
- Every edit produces an immutable version: failed transactions roll back
  without cloning the document, and `doc.history` undoes and redoes changes.
- Untouched objects keep their original bytes on save, and `doc.changes`
  reports signatures or PDF/A and PDF/UA claims that a save would invalidate.

```python
import pyropdf

with pyropdf.open("input.pdf") as doc:
    page = doc.pages[0]
    text = page.text()
    hits = page.search("invoice")
    page.pixmap(dpi=150).save("page.png")

    with doc.transaction("annotate"):
        page.annotations.add(pyropdf.Highlight(hits))
    doc.save("output.pdf", incremental=True)
```

### Updating existing code

1. **From `pylopdf` 0.16:** replace `import pylopdf` with `import pyropdf` and
   check the differences below.
2. **From `pylopdf` 0.15 or `pymupdf`:** if you want mechanical rewrites to the
   0.16 API first, run `python -m pylopdf.migrate src/` from
   `pylopdf[migrate]==0.16.1`, then replace `import pylopdf` with
   `import pyropdf`.

Key differences between `pylopdf` 0.16 and `pyropdf`:

- **Extras:** `pyropdf[ocr]`, `pyropdf[jp]`, `pyropdf[ko]`, `pyropdf[zh-cn]`,
  and `pyropdf[zh-tw]` reuse the existing `pylopdf-ocr-models` and
  `pylopdf-fonts-*` data packages. For other scripts (`ar`, `he`, `hi`, `th`),
  pass a font file to `pyropdf.Font.from_file(...)`.
- **Page boxes:** read and assign `page.media_box`, `page.crop_box`,
  `page.bleed_box`, `page.trim_box`, and `page.art_box` (in display space)
  instead of `page.boxes.*`.
- **Creating documents:** create an empty document with `pyropdf.Document()`,
  add pages with `doc.pages.new(...)` or `doc.pages.new_from_image(...)`, and
  draw with `page.draw()`, in place of `pylopdf.create()`.
- **Saving defaults:** `doc.save()` preserves untouched objects by default
  (`compress=False, prune=False`); pass `compress=True, prune=True` when
  rewriting for size, or `incremental=True` to append only changed objects.
- **Warnings:** `PyropdfWarning` replaces `PylopdfWarning`.
- **Untrusted input and WebAssembly:** see
  [pyropdf's status and safety notes](https://pyropdf.haya.works/status/) and
  [limits guide](https://pyropdf.haya.works/limits/#reading-a-pdf-in-a-worker)
  before processing untrusted PDFs; WebAssembly wheels for `pyropdf` are
  tracked on [its roadmap](https://github.com/yhay81/pyropdf/issues/1).
  Existing `pylopdf` releases remain installable from
  [PyPI](https://pypi.org/project/pylopdf/) and
  [GitHub Releases](https://github.com/yhay81/pylopdf/releases).

## About pylopdf (archived)

`pylopdf` was a Python PDF library built on the Rust PDF engines
[lopdf](https://github.com/J-F-Liu/lopdf),
[hayro](https://github.com/LaurenzV/hayro),
[krilla](https://github.com/LaurenzV/krilla), and
[HarfRust](https://github.com/harfbuzz/harfrust). Its
[changelog](https://github.com/yhay81/pylopdf/blob/main/CHANGELOG.md) and
[roadmap](https://github.com/yhay81/pylopdf/blob/main/ROADMAP.md) remain in
this repository as a record.

## License

[MIT](https://github.com/yhay81/pylopdf/blob/main/LICENSE). See
[NOTICE.md](https://github.com/yhay81/pylopdf/blob/main/NOTICE.md) for
third-party licenses.
