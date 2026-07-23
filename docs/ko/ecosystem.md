---
title: 생태계 연동
description: Typst, pyHanko, veraPDF 및 OCR 엔진을 pylopdf와 연결하는 검증된 사용법.
---

# 생태계 연동

pylopdf는 조판, PDF/A 생성, 디지털 서명을 의도적으로 다시 구현하지 않습니다.
각 기능은 검증된 라이브러리와 조합해 해결하며, 아래의 모든 사용법은
[`tests/test_interop.py`](https://github.com/yhay81/pylopdf/blob/main/tests/test_interop.py)의
통합 테스트로 보호됩니다.

## 조판과 새 문서 — typst { #typesetting }

[typst](https://typst.app/)와
[typst-py](https://pypi.org/project/typst/)로 보고서를 조판하고, 생성된 바이트를
pylopdf에 바로 전달할 수 있습니다.

```python
import typst
import pylopdf

pdf_bytes = typst.compile("report.typ")   # 조판: typst
doc = pylopdf.open(stream=pdf_bytes)      # 편집 / 추출 / 병합: pylopdf
```

## 새 문서의 PDF/A — typst { #new-pdfa }

typst의 PDF 백엔드(krilla)는 검증된 PDF/A-1b…4 및 PDF/UA-1 출력을 지원합니다.

```python
pdf_a: bytes = typst.compile("report.typ", pdf_standards="a-2b")
pylopdf.open(stream=pdf_a).get_pdfa_claim()   # (2, "B")
```

기존 PDF의 변환이나 검증은 다른 문제입니다.
[veraPDF](https://verapdf.org/)(Java)가 사실상의 표준 검증 도구이며,
`Document.get_pdfa_claim()`은 문서가 스스로 선언한 정보만 읽습니다.

## CJK 워터마크와 헤더 — typst × show_pdf_page { #cjk-watermarks }

Standard-14 글꼴로는 CJK 문자를 그릴 수 없습니다. 대신 typst로 한 페이지짜리
스탬프를 조판하고(글꼴은 부분 집합으로 포함됨), 각 페이지에 벡터로 합성합니다.

```python
from pylopdf_fonts_cjk import sans_path  # pip install pylopdf[cjk]

stamp_typ = """
#set page(width: 595pt, height: 842pt, fill: none)
#set text(font: "Noto Sans JP", size: 48pt, fill: rgb(255, 0, 0, 40%))
#align(center + horizon)[대외비]
"""
stamp = pylopdf.open(stream=typst.compile(stamp_typ.encode(), font_paths=[str(sans_path().parent)]))
for page in doc:
    page.show_pdf_page((0, 0, page.rect.width, page.rect.height), stamp)
```

합성 후에도 스탬프는 추출 가능한 텍스트로 남아 `page.get_text()`에서 `대외비`를
찾을 수 있습니다.

## 디지털 서명(PAdES) — pyHanko { #signatures }

[pyHanko](https://pypi.org/project/pyHanko/)(MIT)는 증분 업데이트 방식으로
서명합니다. 따라서 pylopdf가 생성한 바이트는 서명된 파일의 접두부로 그대로
유지되며, 이 동작은 테스트에서 바이트 단위로 검증합니다.

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

pylopdf 자체의 서명 필드 API는 서명 필드를 채우지 않으며, 대신 이 방법을
안내하도록 설계되었습니다.

## OCR — 원하는 엔진 사용 { #ocr }

`Page.insert_ocr_text_layer(words)`는 클라우드 API, Tesseract 등
`(x0, y0, x1, y1, text)`를 생성하는 어떤 OCR 결과든 보이지 않는 텍스트 레이어로
기록합니다. 글꼴을 포함하지 않아 크기 증가가 거의 없고, 스캔 PDF를 검색 가능하게
만듭니다. 이후 스캔 문서에도 `to_markdown()`을 사용할 수 있습니다.
