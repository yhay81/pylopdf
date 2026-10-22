---
title: 생태계 연동
description: pylopdf를 Typst, pyHanko, veraPDF, OCR 엔진과 연결하는 테스트된 레시피입니다.
---

# 생태계 연동

pylopdf는 조판, PDF/A 출력, 디지털 서명을 구현하지 않습니다. 이미 잘 만들어진 라이브러리가 이런 작업을 잘 처리하며, 아래 레시피는 그 라이브러리를 pylopdf와 함께 쓰는 방법을 보여 줍니다. 각 레시피는 [`tests/test_interop.py`](https://github.com/yhay81/pylopdf/blob/main/tests/test_interop.py)의 통합 테스트로 검증합니다.

## typst로 새 문서 조판하기 { #typesetting }

[typst](https://typst.app/)로 문서를 작성하고 [typst-py](https://pypi.org/project/typst/)로 컴파일한 뒤 결과를 pylopdf로 엽니다.

```python
import typst
import pylopdf

pdf_bytes = typst.compile("report.typ")   # typst가 문서를 조판
doc = pylopdf.open(pdf_bytes)      # pylopdf가 편집, 병합, 추출
```

## typst로 PDF/A 출력하기 { #new-pdfa }

typst는 PDF/A(파트 1–4)와 PDF/UA-1 파일을 쓸 수 있습니다.

```python
pdf_a: bytes = typst.compile("report.typ", pdf_standards="a-2b")
pylopdf.open(pdf_a).get_pdfa_claim()   # (2, "B")
```

`Document.get_pdfa_claim()`은 파일이 어떤 PDF/A 수준을 선언하는지만 읽습니다. 기존 PDF가 실제로 적합한지 확인하려면 표준 검증기인 [veraPDF](https://verapdf.org/)(Java에서 실행)를 사용하세요.

## typst로 디자인한 워터마크와 머리글 { #cjk-watermarks }

단순한 텍스트라면 `insert_text()`로 충분합니다. `pylopdf[jp]`(또는 `[ko]`, `[zh-cn]`, `[zh-tw]`)를 설치하면 CJK 텍스트도 씁니다. 스탬프에 제대로 된 레이아웃이 필요하다면 typst에서 한 페이지로 디자인하고, `show_pdf_page()`로 문서의 모든 페이지에 그 페이지를 그리세요.

```python
from pylopdf_fonts_jp import sans_path  # pip install pylopdf[jp]

stamp_typ = """
#set page(width: 595pt, height: 842pt, fill: none)
#set text(font: "Noto Sans JP", size: 48pt, fill: rgb(255, 0, 0, 40%))
#align(center + horizon)[社外秘]
"""
stamp = pylopdf.open(typst.compile(stamp_typ.encode(), font_paths=[str(sans_path().parent)]))
for page in doc.pages:
    page.show_pdf_page((0, 0, page.rect.width, page.rect.height), stamp)
```

스탬프는 실제 텍스트로 남으므로, 그 뒤에도 `page.get_text()`가 `社外秘`를 찾습니다.

## pyHanko로 디지털 서명하기 { #signatures }

[pyHanko](https://pypi.org/project/pyHanko/)(MIT)는 PAdES 서명을 만듭니다. 서명을 파일 끝에 덧붙이므로 pylopdf가 쓴 바이트는 바뀌지 않습니다.

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

서명은 마지막 단계에서 하세요. 그 뒤에 pylopdf로 저장하면 파일을 다시 쓰므로 서명이 무효가 됩니다. `set_form_field()`는 서명 필드 채우기를 거부합니다.

## 다른 엔진으로 OCR하기 { #ocr }

pylopdf에는 [내장 OCR](ocr.md)이 있습니다. 대신 Tesseract나 클라우드 서비스 같은 다른 엔진을 쓰려면, 그 엔진이 인식한 단어를 `(x0, y0, x1, y1, text)` 형식으로 `Page.insert_ocr_text_layer(words)`에 전달하세요. pylopdf가 이를 보이지 않는 텍스트 레이어로 추가하므로, 파일 크기는 거의 늘지 않으면서 스캔 문서를 검색할 수 있고 `to_markdown()`이 그 텍스트를 읽을 수 있습니다.

```python
import pylopdf

words = [  # from your OCR engine, in page coordinates
    (72, 90, 140, 104, "Invoice"),
    (146, 90, 190, 104, "#1024"),
]
with pylopdf.open("scan.pdf") as doc:
    doc.pages[0].insert_ocr_text_layer(words)
    doc.save("searchable.pdf")
```
