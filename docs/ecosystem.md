# Ecosystem recipes

pylopdf deliberately does **not** reimplement typesetting, PDF/A generation or
digital signatures. Each is solved by pairing with an established library, and
every recipe below is guarded by integration tests in
[`tests/test_interop.py`](https://github.com/yhay81/pylopdf/blob/main/tests/test_interop.py).

## Typesetting & new documents — typst

Typeset reports with [typst](https://typst.app/) (via
[typst-py](https://pypi.org/project/typst/)) and feed the bytes straight into
pylopdf:

```python
import typst
import pylopdf

pdf_bytes = typst.compile("report.typ")   # typesetting: typst
doc = pylopdf.open(stream=pdf_bytes)      # editing / extraction / merging: pylopdf
```

## PDF/A for new documents — typst

typst's PDF backend (krilla) supports validated PDF/A-1b…4 and PDF/UA-1
export:

```python
pdf_a: bytes = typst.compile("report.typ", pdf_standards="a-2b")
pylopdf.open(stream=pdf_a).get_pdfa_claim()   # (2, "B")
```

Converting or validating *existing* PDFs is a different problem —
[veraPDF](https://verapdf.org/) (Java) is the de-facto validator.
`Document.get_pdfa_claim()` reads the self-declaration only.

## CJK watermarks & headers — typst × show_pdf_page

Standard-14 fonts cannot draw Japanese. Instead, typeset a one-page stamp with
typst (fonts get subset-embedded) and burn it onto every page as vectors:

```python
from pylopdf_fonts_cjk import sans_path  # pip install pylopdf[cjk]

stamp_typ = """
#set page(width: 595pt, height: 842pt, fill: none)
#set text(font: "Noto Sans JP", size: 48pt, fill: rgb(255, 0, 0, 40%))
#align(center + horizon)[社外秘]
"""
stamp = pylopdf.open(stream=typst.compile(stamp_typ.encode(), font_paths=[str(sans_path().parent)]))
for page in doc:
    page.show_pdf_page((0, 0, page.rect.width, page.rect.height), stamp)
```

The stamp stays extractable text afterwards — `page.get_text()` finds 社外秘.

## Digital signatures (PAdES) — pyHanko

[pyHanko](https://pypi.org/project/pyHanko/) (MIT) signs with an incremental
update, so the bytes produced by pylopdf remain untouched as a prefix of the
signed file (asserted byte-for-byte in our tests):

```python
import io
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import signers

signer = signers.SimpleSigner.load("key.pem", "cert.pem")
out = signers.sign_pdf(
    IncrementalPdfFileWriter(io.BytesIO(doc.tobytes())),
    signers.PdfSignatureMetadata(field_name="Signature1"),
    signer=signer,
)
signed_pdf: bytes = out.getvalue()
```

pylopdf's own signature-field API intentionally refuses to fill signature
fields and points here instead.

## OCR — bring your own engine

`Page.insert_ocr_text_layer(words)` writes any OCR output — cloud APIs,
Tesseract, anything that yields `(x0, y0, x1, y1, text)` — as an invisible text
layer, making scanned PDFs searchable with near-zero size cost (no font
embedding). `to_markdown()` then works on scans too.
