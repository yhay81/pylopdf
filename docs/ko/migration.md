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
| `pymupdf.open(path)` / `open(stream=…)` / `open("pdf", data)` | 동일 | `open`은 `Document` 그 자체. `filetype=`은 `"pdf"`만 지원. `password=`는 keyword-only이며 UTF-8 127 byte로 제한 |
| `doc[i]`, `len(doc)`, 반복 | 동일 | 0부터 시작, 음수 인덱스 |
| `doc.metadata` / `set_metadata` | 동일 | 같은 키 이름 |
| `page.get_text()` | 동일 | 옵션: `text` / `words` / `blocks` / `dict` |
| `page.search_for(t)` | 동일, 추가로`max_hits=4096` | 상한이 있는`list[Rect]` 반환. 검색어는4,096 byte, `max_hits=None`으로 해제, `quads=` 없음 |
| `page.get_pixmap(matrix=pymupdf.Matrix(2, 2))` / `doc.get_page_pixmap(pno, ...)` | 동일 | 기본은 불투명 RGB, `alpha=True`면 RGBA. `dpi=`가 우선. 배율 matrix만 지원하며 `colorspace=`와 `annots=`는 아직 없음 |
| `page.get_svg_image()` | 동일 | `matrix`는 항등만. `text_as_path=False`는 예외 |
| `pix.samples / width / height / n / alpha / stride / colorspace / save()` | 동일 | RGB, `alpha=True`면 straight-alpha RGBA. pylopdf의 상한이 있는 `tobytes(max_size=64 MiB)`와 streaming `save(filename)`는 PNG를 만들며 `save`는 `.png`가 필요 |
| `page.get_images()` / 추출 | `page.get_images()` | 그려진 이미지와 bbox 반환, JPEG 직접 추출 |
| `page.get_drawings()` | 동일 | 타입 지정path 딕셔너리. line/cubic과 주요paint/stroke 속성. `extended=`의clip/group 계층은 미지원 |
| `doc.rewrite_images(dpi_target=, quality=)` | `doc.compress_images(dpi=, quality=)` | mask 없는 안전한DeviceGray/DeviceRGB DCT/Flate raster를JPEG로 변환. `dpi`는 최대 배치를 직접 제한하며lossless 변환은 미지원 |
| `doc.select`, `delete_page(s)`, `fullcopy_page`, `new_page` | 동일 | `delete_pages`는 pymupdf 형식(번호 하나, 번호 둘, `from_page=`/`to_page=`, iterable)을 받음. 반복된 페이지 번호로 `select`하면 페이지 복제 |
| `doc.copy_page(pno, to)` | `doc.fullcopy_page(pno, to)` | pylopdf는 페이지 객체를 공유하지 않음(G8) |
| `doc.insert_pdf(docsrc, from_page=, to_page=, start_at=)` | 동일 | pymupdf의 범위 규칙(음수 `from_page`는 첫 페이지, 음수 `to_page`는 마지막 페이지이며 둘 다 범위 안으로 맞춤). 가져온 페이지의 폼 필드를 등록하고 충돌하는 최상위 이름에는`+N`을 붙임. `widgets=` / `join_duplicates=`는 없음 |
| `doc.get_toc()` / `set_toc()` | 동일 | 둘 다 페이지 번호는 1부터 시작 |
| `doc.save(garbage=4, deflate=True)` | 동일 | pymupdf와 같은 0~4단계. 페이지, 주석, 폼 필드, 구조 요소, 레이어는 병합하지 않음 |
| `doc.save(encryption=…, user_pw=…)` | 동일 | `PDF_ENCRYPT_NONE`(기본값)과 `PDF_ENCRYPT_AES_256`만 지원하며 다른 방식은 예외. `PDF_ENCRYPT_NONE`에서 password는 무시하지 않고 예외. 각password는UTF-8 127 byte로 제한 |
| `doc.needs_pass` / `authenticate()` | 동일 | 같은 반환값 의미(0/1/2/4/6) |
| `page.rect / rotation / set_rotation` | 동일 | |
| `page.insert_image(rect, filename= / stream= / pixmap=, rotate=)` | 동일 | JPEG 패스스루, PNG 알파, RGBA `Pixmap` 직접 재사용과 시계 방향 직각 회전; 그 밖의 인코딩 형식은 Pillow로 변환; 같은 입력은 이미지 XObject를 공유하므로 `xref=` 재사용이 불필요 |
| `page.show_pdf_page(rect, docsrc, pno)` | 동일 | 같은 문서는 네이티브 편집 전 snapshot을 사용하므로 serialize/open 복제가 불필요; 편집하지 않은 원본을 다시 배치하면 Form XObject를 공유 |
| `page.insert_text(point, text, fontsize=, lineheight=, fontname=, fontfile=)` | 동일, 추가로 `fontbuffer=` / `fontindex=` / `font_language=` | source가 없으면 Standard-14 / WinAnsi 또는 설치된script／locale provider. 다른 source는 shaping 후 subset 내장. 줄 나누기, 줄 위치, 페이지 아래로 벗어나는 줄의 제외는 pymupdf와 같고 그린 줄 수를 반환. 페이지 아래쪽 가장자리는 표시 좌표의 `page.rect` 기준이며 pymupdf는 회전 전 좌표로 잼 |
| `page.insert_textbox(rect, text, align=, lineheight=)` | 동일, 임의의 `fontfile=` / `fontbuffer=` / `font_language=` 지원 | 같은 provider 선택과 UAX #14 줄바꿈, 줄 위치와 들어가는지의 판단은 pymupdf와 같음, 음수 반환 시 그리지 않음 |
| `pymupdf.Font(fontname=, fontfile=, fontbuffer=)` / `font.text_length(text, fontsize)` / `font.has_glyph(chr)` | 동일 | Standard 14 이름과 약칭, OpenType 파일, `language=`나 `family=`로 선택하는 설치된 글꼴 extra. pymupdf의 다른 내장 글꼴(`"cjk"`, `"figo"` 등)과 `script=`, `ordering=`, `embed=`는 쓸 수 없음. `is_bold=`, `is_italic=`, `is_serif=`는 설치된 extra의 글꼴을 정확히 일치하는 것으로 선택하며, pymupdf가 무시하는 `fontname`이나 글꼴 파일과 함께 쓰면 예외. keyword-only `weight=`는 가변 글꼴 파일의 굵기 축에서 인스턴스도 선택. 글꼴은 `insert_text` / `insert_textbox`에 `font=`로 전달. `text_length`는 커닝을 포함하고 `ascender`／`descender`는 pymupdf와 같은 값이며 `insert_text`와 `insert_textbox`는 이 값으로 줄을 배치 |
| `page.add_highlight_annot(...)` | 동일 | appearance stream 항상 생성 |
| `doc.embfile_add / names / get / del` | 동일 | |
| `doc.get_page_labels / set_page_labels`, `page.get_label` | 동일 | |
| `page.widgets()` / widget 객체 | `doc.get_form_fields()` / `doc.set_form_field(name, value, fontfile=)` | 문서 수준, 텍스트／선택／checkbox／radio 네이티브 appearance |
| `page.get_textpage_ocr(...)` | `page.get_text_ocr(...)` / `page.apply_ocr(...)` | `pylopdf[ocr]`의 오프라인 순수 Rust PP-OCR, 임의의 위치 결과에는 `insert_ocr_text_layer`도 사용 가능 |
| `page.new_shape()` / `shape.draw_*` / `shape.finish()` / `shape.insert_text()` / `shape.commit()` | 같음 | 확정 한 번에 Form XObject 하나, 글꼴은 한 번만 포함. `with page.new_shape() as shape:`는 정상 종료 시 확정. `finish()`로 마무리하지 않은 경로의 `commit()`과 아무것도 그리지 않는 `finish()`는 예외. `morph=`와 `oc=`는 쓸 수 없음 |
| `page.draw_line()` / `draw_rect()` / `draw_circle()` / `draw_polyline()` 등 `draw_*` | 같음 | 선과 색 지정은 키워드 전용이며 끝점 `Point`를 반환. `morph=`와 `oc=`는 쓸 수 없음 |
| `pymupdf4llm.to_markdown(doc)` | `doc.to_markdown()` | 내장, MIT |

## 동작 차이 { #behavioral-differences }

- **좌표**는 두 라이브러리 모두 왼쪽 위 원점의 표시 공간을 사용합니다. pylopdf는
  회전된 페이지에서도 추출, 검색, 그리기, 렌더링에 일관되게 적용합니다.
- **타입**: `Rect`는 불변 `NamedTuple`(`x0, y0, x1, y1`과 `width` / `height`)이고,
  `Point`는 `x, y`의 `NamedTuple`로 `draw_*`가 반환합니다. `Matrix` / `Quad`
  클래스는 없으며, API는 일반 튜플(quad는 네 점)과 `scale=` / `dpi=` 키워드를
  받습니다.
- **오래된 페이지 객체**: 삭제, 삽입, 재정렬 같은 구조 변경 후 이전에 얻은 `Page`
  객체는 다른 페이지를 조용히 가리키는 대신 `StalePageError`를 발생시킵니다.
  `doc[i]`로 다시 가져오세요.
- **예외**: `PdfError`(`ValueError`의 하위 클래스)가 기반이며 `PasswordError`,
  `DocumentClosedError`, `EncryptedDocumentError`, `StalePageError`로
  구체화됩니다. `except ValueError`도 계속 동작합니다. pymupdf의
  `FileDataError`와 `EmptyFileError`는 같은 이름이며 `RuntimeError`도
  상속합니다. 없는 파일은 Python 내장 `FileNotFoundError`가 되므로
  `except pymupdf.FileNotFoundError`는 `except FileNotFoundError`로 바꿉니다.
- **리소스 정책**: `DocumentLimits.web()`은 rendering／extraction에 전달되는 전체
  PDF snapshot을64 MiB, 누적 위치text를65,536 glyph record로 제한합니다. 사용자
  정책은`max_interpretation_size`와`max_text_glyphs`를 설정할 수 있고`None`은 호환되는
  무제한 동작을 유지합니다.
- **`get_text` 옵션**은 `text` / `words` / `blocks` / `dict`로 제한됩니다
  (`html` / `rawdict` / `xml` 없음). 포함 글꼴의 span dict에는 `font`와
  pymupdf 방식의 `flags`(bold/italic/serif/mono)가 들어갑니다.
- **다단 텍스트**는 결정적인 단 사이 여백 감지로 정렬합니다. 각 단 안에서는
  위에서 아래로, 단 사이는 왼쪽에서 오른쪽으로 읽습니다. 1 em보다 좁은 여백은
  양쪽에 충분한 본문이 있는 행이 조밀하게 반복될 때만 채택합니다. 정렬된 label,
  dot leader, 구분 위치가 여러 개인 조밀한 행은 행 우선 순서를 유지합니다. 확인된
  단 경계는 scan/OCR text layer의 작은 수평 이동을 따라갑니다.
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
  지정하거나 대응하는script／locale font extra를 설치합니다. 지역별 한자는
  `font_language=`로 지정합니다. comb 텍스트 필드는 상속된 `MaxLen`과
  정렬을 따릅니다. rich text, pushbutton, 서명은 API 범위 밖입니다.
- **폼 병합**에서 `insert_pdf()`는 pymupdf의 기본값`widgets=True`처럼 가져온
  페이지의 필드를 항상 등록합니다. 받는 문서에서 이미 쓰인 최상위 이름은 qpdf처럼
  쓰이지 않은 가장 작은`name+N`이 되어, 같은 폼을 병합한 각 사본은 독립된 값을
  가집니다. 이를 하나의 필드로 합치는`join_duplicates=`는 없습니다. 페이지 범위를
  지정하면 다른 페이지의 widget과 widget이 없어진 필드는 가져오지 않습니다.
- **CJK 세로쓰기**는 보수적으로 감지해 열 안에서는 위에서 아래로,
  열 사이는 오른쪽에서 왼쪽으로 읽습니다. 루비, 행간 주석, 혼합 방향 조판은
  해석하지 않습니다.

## 의도적으로 구현하지 않은 범위와 대안 { #deliberate-scope }

| pymupdf 기능 | pylopdf의 대안 |
|---|---|
| Story API / `insert_htmlbox`(조판) | typst-py를 통한 typst — [사용법](ecosystem.md) |
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
page.get_pixmap(matrix=pylopdf.Matrix(2, 2)).save("page.png")
doc.save("out.pdf", garbage=4, deflate=True)
```
