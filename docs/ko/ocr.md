---
title: 오프라인 OCR
description: pylopdf의 내장 OCR 엔진으로 스캔한 PDF의 텍스트를 로컬에서 인식하거나, 다른 OCR 엔진의 텍스트를 추가합니다.
---

# 오프라인 OCR

pylopdf는 스캔한 페이지의 텍스트를 인식해 보이지 않는 텍스트 레이어로 추가할 수 있습니다. 그러면 PDF를 검색할 수 있고 `text()`와 `markdown()`이 그 텍스트를 읽을 수 있습니다. 모델 패키지를 설치하세요.

```bash
pip install "pylopdf[ocr]"
```

인식은 PP-OCRv6 small 모델을 사용해 pylopdf 자체 엔진에서 로컬로 실행됩니다. 네트워크 연결, Tesseract 같은 시스템 프로그램, 다른 Python 패키지가 필요하지 않습니다. 모델 패키지는 약 27 MB이며 영어, 일본어, 중국어 간체와 번체를 포함한 50개 언어를 지원합니다.

[WebAssembly](wasm.md) 빌드에서는 OCR을 사용할 수 없습니다.

## 편집하지 않고 인식하기 { #recognize }

`Page.get_text_ocr()`은 찾은 단어를 반환하며 문서는 변경하지 않습니다.

```python
import pylopdf

with pylopdf.open("scan.pdf") as doc:
    for word in doc.pages[0].get_text_ocr():
        print(word["bbox"], word["text"], word["confidence"])
```

각 `OcrWord`의 `bbox`는 텍스트 추출, 렌더링과 같은 페이지 좌표를 사용합니다. `confidence`는 순위를 매기거나 거를 때 유용하지만 보정된 확률은 아닙니다.

## 스캔 문서를 검색 가능하게 만들기 { #searchable }

엔진 하나를 만들어 모든 페이지에서 재사용합니다.

```python
import pylopdf

engine = pylopdf.OcrEngine()
with pylopdf.open("scan.pdf") as doc:
    for page in doc.pages:
        page.apply_ocr(engine=engine)
    doc.save("searchable.pdf", dedupe=True, object_streams=True)
```

`apply_ocr()`은 페이지의 겉모습을 바꾸지 않습니다. 이미 텍스트가 있는 페이지는 건너뛰므로, 같은 스크립트를 두 번 실행해도 텍스트가 두 번 추가되지 않습니다. 입력된 텍스트와 스캔 이미지가 섞인 페이지에서는 스캔 영역을 둘러싸는 `clip=(x0, y0, x1, y1)`을 전달하세요. 그러면 그 영역 안의 텍스트만 기존 텍스트로 간주합니다. 기존 텍스트와 관계없이 OCR 텍스트를 추가하려면 `skip_existing=False`를 전달합니다.

## 다른 OCR 엔진 사용하기 { #other-engines }

`Page.insert_ocr_text_layer(words)`는 어떤 OCR 엔진이나 클라우드 서비스의 단어든 추가합니다. 각 단어는 페이지 좌표의 `(x0, y0, x1, y1, text)`이며, `text` 뒤의 추가 항목은 무시됩니다.

```python
words = [(72, 90, 140, 104, "Invoice"), (146, 90, 190, 104, "#1024")]
doc.pages[0].insert_ocr_text_layer(words)
```

이 레이어는 글꼴을 포함하지 않으므로 파일 크기가 거의 늘지 않습니다.

## 회전된 스캔 { #rotation }

페이지가 옆으로 눕거나 뒤집혀 스캔되었다면, 인식 전에 이미지를 시계 방향으로 돌리도록 `rotation=90`, `180`, `270`을 전달합니다.

```python
words = page.get_text_ocr(rotation=270)
page.apply_ocr(rotation=270)
```

페이지 자체는 회전하지 않으며, 반환되는 상자는 페이지 좌표를 유지합니다. 보이지 않는 텍스트는 인식한 읽기 방향을 따르므로 저장한 뒤에도 검색과 추출이 동작합니다. pylopdf는 회전을 자동으로 감지하지 않으며, 약간 기울어진 스캔을 바로잡지도 않습니다.

## 메모리와 속도 { #resources }

기본적으로 페이지는 300 dpi로 렌더링되고, 1,408픽셀 크기의 겹치는 타일로 나뉘어 인식됩니다. 이 설정에서 A4 한 페이지는 측정 시 최대 약 420 MiB를 사용했습니다. 메모리를 덜 쓰려면 `threads`와 `tile_size`를 낮추세요.

```python
engine = pylopdf.OcrEngine(threads=2)
words = page.get_text_ocr(engine=engine, tile_size=1280, min_confidence=0.6)
```

`clip=`도 선택한 영역만 인식하므로 시간과 메모리를 절약합니다.

`OcrEngine`은 여러 스레드와 문서에서 공유할 수 있습니다. 기본적으로 한 번에 하나의 인식만 실행하므로(`max_concurrent=1`) 공유해도 메모리 사용량이 배로 늘지 않습니다. `max_concurrent`는 16까지 올릴 수 있지만 먼저 측정하세요. 테스트에서는 동시에 두 개를 실행해도 빨라지지 않았습니다. `Document` 자체는 여전히 한 번에 한 스레드에서만 사용해야 합니다. [동시성](concurrency.md)을 참고하세요.

## 정확도 { #accuracy }

텍스트를 알고 있는 일본어 테스트 문서 두 개에 대한 문자 오류율(CER, 낮을수록 좋음)입니다. "Strict"는 공백만 무시하고, "NFKC"는 전각과 반각 문자도 같게 취급합니다.

| 문서 | DPI | Strict CER | NFKC CER | 시간 |
|---|---:|---:|---:|---:|
| 정부 보고서(디지털, 1,188자) | 150 | 3.788% | 0.842% | 5.50 s |
| 정부 보고서(디지털, 1,188자) | 300 | 3.704% | 0.842% | 13.87 s |
| 기록 보관 스캔(384자) | 150 | 1.823% | 1.562% | 2.05 s |
| 기록 보관 스캔(384자) | 300 | 1.302% | 1.042% | 5.37 s |

비교하자면, RapidOCR v6 기준 구현은 정부 보고서에서 150 dpi와 300 dpi일 때 NFKC CER 0.926%와 0.758%를 기록했습니다. pylopdf는 150 dpi에서는 더 정확했고 300 dpi에서는 덜 정확했습니다. 시간은 하드웨어에 따라 다릅니다. 저장소에서 `uv run python bench/ocr.py`를 실행하면 보고서를 재현할 수 있습니다.

## 제한 사항 { #limitations }

- 단어 상자는 축에 정렬된 사각형입니다.
- 루비(후리가나), 와리추(割注), 세로쓰기와 가로쓰기가 섞인 텍스트는 해석하지 않습니다.
- 사용자 지정 모델은 RTen 형식으로 변환한 PP-OCR 모델이어야 합니다. 그 경로를 `OcrEngine(detector_path, recognizer_path, dictionary_path)`에 전달하세요.

`pylopdf-ocr-models` 패키지에 모델의 출처, 해시, Apache-2.0 라이선스가 문서화되어 있습니다.
