---
title: Ecosystem recipes
description: Tested recipes connecting pylopdf with Typst, pyHanko, veraPDF and OCR engines.
---

# Ecosystem recipes

pylopdf does not implement typesetting, PDF/A output, or digital signatures.
Established libraries already do these well, and the recipes below show how to
combine them with pylopdf. Each recipe is covered by the integration tests in
[`tests/test_interop.py`](https://github.com/yhay81/pylopdf/blob/main/tests/test_interop.py).

## Typesetting new documents with typst { #typesetting }

Write the document in [typst](https://typst.app/), compile it with
[typst-py](https://pypi.org/project/typst/), and open the result in pylopdf:

```python
import typst
import pylopdf

pdf_bytes = typst.compile("report.typ")   # typst lays out the document
doc = pylopdf.open(pdf_bytes)      # pylopdf edits, merges, and extracts
```

## PDF/A output with typst { #new-pdfa }

typst can write PDF/A (parts 1 to 4) and PDF/UA-1 files:

```python
pdf_a: bytes = typst.compile("report.typ", pdf_standards="a-2b")
pylopdf.open(pdf_a).pdfa_claim   # PdfaClaim(part=2, conformance='B')
```

`Document.pdfa_claim` only reads which PDF/A level a file claims. To
check that an existing PDF really conforms, use
[veraPDF](https://verapdf.org/), the standard validator (it runs on Java).

## Watermarks and headers designed in typst { #cjk-watermarks }

For simple text, `canvas.text()` is enough; with `pylopdf[jp]` (or `[ko]`,
`[zh-cn]`, `[zh-tw]`) installed it writes CJK text too. When a stamp needs
real layout, design it as a page in typst and draw that page onto every page
of the document with `canvas.page()`:

```python
from pylopdf_fonts_jp import sans_path  # pip install pylopdf[jp]

stamp_typ = """
#set page(width: 595pt, height: 842pt, fill: none)
#set text(font: "Noto Sans JP", size: 48pt, fill: rgb(255, 0, 0, 40%))
#align(center + horizon)[社外秘]
"""
stamp = pylopdf.open(typst.compile(stamp_typ.encode(), font_paths=[str(sans_path().parent)]))
for page in doc.pages:
    with page.draw() as canvas:
        canvas.page(page.rect, stamp.pages[0])
```

The stamp stays real text: `page.text()` finds `社外秘` afterwards.

## Digital signatures with pyHanko { #signatures }

[pyHanko](https://pypi.org/project/pyHanko/) (MIT) creates PAdES signatures.
It appends the signature to the end of the file, so the bytes pylopdf wrote stay
unchanged:

```python
import io
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import signers

signer = signers.SimpleSigner.load("key.pem", "cert.pem")
out = signers.sign_pdf(
    IncrementalPdfFileWriter(io.BytesIO(doc.to_bytes())),
    signers.PdfSignatureMetadata(field_name="Signature1"),
    signer=signer,
)
signed_pdf: bytes = out.getvalue()
```

Sign as the last step: saving with pylopdf afterwards rewrites the file and
invalidates the signature. Assigning the value of a signature field in `doc.form` raises.

## OCR with another engine { #ocr }

pylopdf has [built-in OCR](ocr.md). To use another engine instead, such as
Tesseract or a cloud service, pass its words as `OcrWord` records to
`Page.apply_ocr(words=...)`. pylopdf adds them as an invisible text layer,
which makes the scan searchable and lets `markdown()` read it, while adding
very little to the file size.

```python
import pylopdf
from pylopdf import OcrWord, Rect

words = [  # from your OCR engine, in page coordinates
    OcrWord(text="Invoice", bbox=Rect(72, 90, 140, 104), confidence=0.98),
    OcrWord(text="#1024", bbox=Rect(146, 90, 190, 104), confidence=0.95),
]
with pylopdf.open("scan.pdf") as doc:
    doc.pages[0].apply_ocr(words=words)
    doc.save("searchable.pdf")
```
