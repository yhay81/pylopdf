---
title: pymupdf에서 이전
description: pymupdf 작업 흐름을 pylopdf로 옮기고 타입, 동작 및 범위의 의도적인 차이를 이해합니다.
---

# pymupdf에서 이전

pylopdf는 pymupdf와 *비슷한 방식*으로 사용할 수 있지만 완전한 대체품은 아닙니다.
이전 비용을 좌우하는 `"words"` 튜플, `"dict"` 구조, `search_for → list[Rect]`,
1부터 시작하는 목차 페이지 번호는 pymupdf와 일치하므로, 대부분의 텍스트 추출과
페이지 관리 코드는 작은 수정만으로 옮길 수 있습니다. 이 페이지에서는 그대로
사용할 수 있는 부분, 달라진 부분, 의도적으로 구현하지 않은 기능의 대안을 설명합니다.

!!! note
    pylopdf는 **PDF 파일만** 처리합니다. pymupdf가 지원하는 XPS / EPUB / 이미지
    열기는 지원하지 않습니다.

## 빠른 대응표 { #mapping }

| pymupdf | pylopdf | 비고 |
|---|---|---|
| `import pymupdf`(`fitz`는 이전 이름) | `import pylopdf` | |
| `pymupdf.open(path)` / `open(stream=…)` | `pylopdf.open(path)` / `open(stream=…)` | 같은 형태, `password=`도 지원 |
| `doc[i]`, `len(doc)`, 반복 | 동일 | 0부터 시작, 음수 인덱스 |
| `doc.metadata` / `set_metadata` | 동일 | 같은 키 이름 |
| `page.get_text()` | 동일 | 옵션: `text` / `words` / `blocks` / `dict` |
| `page.search_for(t)` | 동일 | `list[Rect]` 반환, `quads=` 없음 |
| `page.get_pixmap(matrix=pymupdf.Matrix(2, 2))` | `page.get_pixmap(scale=2)` | 또는 `dpi=144`, Matrix 클래스 없음 |
| `pix.samples / width / height / stride` | 동일 | 항상 straight-alpha RGBA8, `tobytes()` → PNG |
| `page.get_images()` / 추출 | `page.get_images()` | 그려진 이미지와 bbox 반환, JPEG 직접 추출 |
| `doc.select`, `delete_page(s)`, `copy_page`, `new_page` | 동일 | 반복된 페이지 번호로 `select`하면 페이지 복제 |
| `doc.insert_pdf(src, from_page=, to_page=, start_at=)` | 동일 | |
| `doc.get_toc()` / `set_toc()` | 동일 | 둘 다 페이지 번호는 1부터 시작 |
| `doc.save(garbage=4, deflate=True)` | `doc.save(garbage=True, deflate=True, object_streams=True)` | `garbage`는 bool |
| `doc.save(encryption=…, user_pw=…)` | `doc.save(user_pw=…, owner_pw=…, permissions=…)` | AES-256만 지원 |
| `doc.needs_pass` / `authenticate()` | 동일 | 같은 반환값 의미(0/1/2/4/6) |
| `page.rect / rotation / set_rotation` | 동일 | |
| `page.insert_image(rect, filename= / stream= / pixmap=)` | 동일 | JPEG 패스스루, PNG 알파, 렌더링된 RGBA `Pixmap` 직접 재사용; 그 밖의 인코딩 형식은 Pillow로 변환 |
| `page.show_pdf_page(rect, src, pno)` | 동일 | 같은 문서의 오버레이는 미지원(먼저 복사) |
| `page.insert_text(point, text, fontsize=, fontname=, fontfile=)` | 동일, 추가로 `fontbuffer=` / `fontindex=` | 글꼴 미지정 시 Standard-14 / WinAnsi, 지정 시 HarfRust로 셰이핑하고 krilla로 서브셋 내장 |
| `page.insert_textbox(rect, text, align=, lineheight=)` | 동일, 임의의 `fontfile=` / `fontbuffer=` 지원 | UAX #14 CJK 줄바꿈, 음수 반환 시 그리지 않음 |
| `page.add_highlight_annot(...)` | 동일 | appearance stream 항상 생성 |
| `doc.embfile_add / names / get / del` | 동일 | |
| `doc.get_page_labels / set_page_labels`, `page.get_label` | 동일 | |
| `page.widgets()` / widget 객체 | `doc.get_form_fields()` / `doc.set_form_field(name, value, fontfile=)` | 문서 수준, 텍스트／선택／checkbox／radio 네이티브 appearance |
| `pymupdf4llm.to_markdown(doc)` | `doc.to_markdown()` | 내장, MIT |

## 동작 차이 { #behavioral-differences }

- **좌표**는 두 라이브러리 모두 왼쪽 위 원점의 표시 공간을 사용합니다. pylopdf는
  회전된 페이지에서도 추출, 검색, 그리기, 렌더링에 일관되게 적용합니다.
- **타입**: `Rect`는 불변 `NamedTuple`(`x0, y0, x1, y1`과 `width` / `height`)입니다.
  `Point` / `Matrix` / `Quad` 클래스는 없으며, API는 일반 튜플과 `scale=` / `dpi=`
  키워드를 받습니다.
- **오래된 페이지 객체**: 삭제, 삽입, 재정렬 같은 구조 변경 후 이전에 얻은 `Page`
  객체는 다른 페이지를 조용히 가리키는 대신 `StalePageError`를 발생시킵니다.
  `doc[i]`로 다시 가져오세요.
- **예외**: `PdfError`(`ValueError`의 하위 클래스)가 기반이며 `PasswordError`,
  `DocumentClosedError`, `EncryptedDocumentError`, `StalePageError`로
  구체화됩니다. `except ValueError`도 계속 동작합니다.
- **`get_text` 옵션**은 `text` / `words` / `blocks` / `dict`로 제한됩니다
  (`html` / `rawdict` / `xml` 없음). 포함 글꼴의 span dict에는 `font`와
  pymupdf 방식의 `flags`(bold/italic/serif/mono)가 들어갑니다.
- **다단 텍스트**는 결정적인 단 사이 여백 감지로 정렬합니다. 각 단 안에서는
  위에서 아래로, 단 사이는 왼쪽에서 오른쪽으로 읽습니다.
- **`Page.find_tables()`**는 선이나 가는 채움 사각형에서 축에 평행한
  테두리 격자를 재구성하고 직사각형 병합 셀도 지원합니다.
  `strategy="text"`를 지정하면 신뢰도 높은 테두리 없는 표 감지를 켤 수 있지만,
  정렬된 다단 본문과의 기하학적 모호성은 남습니다. 알려진 표시 좌표 영역 안에
  완전히 들어오는 표만 유지하려면 `clip=`을 사용하고, 테두리 없는 결과의 순위를
  정할 때는 `Table.confidence` / `Table.diagnostics`를 확인합니다.
- **`to_markdown()`**는 완전한 테두리 표를 읽기 순서에 삽입하고 주변 본문에서
  셀 텍스트를 제거합니다. `table_strategy="text"`로 보수적인 테두리 없는 후보를
  추가하거나 `None`으로 표 변환을 끌 수 있습니다.
- **폼 입력**은 값과 네이티브 appearance를 기록해 pylopdf와 외부 뷰어 모두에서
  렌더링됩니다. WinAnsi는 Helvetica로 자동 축소하며 Unicode는 OpenType 글꼴을
  지정하거나 `pylopdf[cjk]`를 설치합니다. comb 텍스트 필드는 상속된 `MaxLen`과
  정렬을 따릅니다. rich text, pushbutton, 서명은 API 범위 밖입니다.
- **CJK 세로쓰기**는 보수적으로 감지해 열 안에서는 위에서 아래로,
  열 사이는 오른쪽에서 왼쪽으로 읽습니다. 루비, 행간 주석, 혼합 방향 조판은
  해석하지 않습니다.

## 의도적으로 구현하지 않은 범위와 대안 { #deliberate-scope }

| pymupdf 기능 | pylopdf의 대안 |
|---|---|
| Story API / `insert_htmlbox`(조판) | typst-py를 통한 typst — [사용법](ecosystem.md) |
| OCR(`get_textpage_ocr`, 외부 Tesseract 언어 데이터와 설정 필요) | 원하는 OCR 엔진 + `insert_ocr_text_layer` |
| 디지털 서명 | pyHanko(MIT) — [사용법](ecosystem.md) |
| 증분 저장 | 현재 미지원, 전체 파일을 다시 쓰며 관찰 목록에 유지, 서명은 pyHanko로 해결 |
| XPS / EPUB / CBZ / 이미지 열기 | 범위 밖 — PDF만 지원 |

## 이전 예제 { #worked-example }

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
page.add_highlight_annot(page.search_for("total"))   # 전체 목록을 한 번에 전달
with open("page.png", "wb") as f:
    f.write(page.get_pixmap(scale=2).tobytes())
doc.save("out.pdf", garbage=True, deflate=True)
```
