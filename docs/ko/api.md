---
title: API 레퍼런스
description: pylopdf의 Document, Page, Font, Shape, Pixmap, 기하 타입, 저수준 객체, 예외를 한눈에 보는 API 지도입니다.
---

# API 레퍼런스

이 페이지는 공개 API를 작업별로 정리합니다. 모든 함수와 클래스에는 docstring도 있으므로 자세한 내용은 `help(pylopdf.Document)`를 실행해 확인하세요.

전체에 공통으로 적용되는 규칙:

- **페이지 번호는 0부터 시작합니다.** 목차도 마찬가지입니다. 음수 인덱스는 끝에서부터 셉니다.
- **좌표**의 단위는 포인트(1/72인치)이며, 원점은 화면에 표시되는 페이지의 왼쪽 위입니다. 페이지 회전과 보이는 영역(CropBox)이 반영됩니다. 추출, 검색, 그리기, 렌더링이 같은 좌표를 공유합니다.
- **사각형**은 [`Rect`](#rectangles)로 반환됩니다. 사각형이나 점을 받는 곳에는 일반 튜플도 쓸 수 있습니다.
- **크기 제한.** 많은 데이터를 읽거나 만들 수 있는 메서드는 안전한 기본값을 가진 `max_size`(또는 비슷한) 인수를 받습니다. 신뢰할 수 있는 입력이라면 `None`을 전달해 제한을 해제합니다. 거부되면 [`LimitError`](security.md#error-codes)가 발생합니다.
- **pymupdf의 이름은 pymupdf의 의미를 유지합니다.** pylopdf가 다르게 동작하는 부분은 [이전 가이드](migration.md)에 적혀 있습니다.

## Document { #document }

```python
pylopdf.Document(filename=None, stream=None, filetype=None, *,
                 password=None, limits=None, max_decompressed_size=None)
```

`pylopdf.open`은 같은 호출 가능 객체입니다. 경로는 `filename`으로, 파일 내용은 `stream`(`bytes`, `bytearray`, `memoryview`, `io.BytesIO`)으로 전달합니다. 인수 없이 `Document()`를 호출하면 빈 PDF를 만듭니다. `limits`에는 신뢰할 수 없는 입력을 위한 [`DocumentLimits`](security.md#untrusted-pdfs) 정책을 전달합니다. 문서는 컨텍스트 관리자로 사용하거나 `close()`를 호출하세요.

### 열기, 확인, 저장 { #document-io }

| 멤버 | 용도 |
|---|---|
| `page_count`, `len(doc)` | 페이지 수 |
| `metadata` / `set_metadata(m)` | 제목, 작성자 등 Info 필드와 `format`, `encryption`. `set_metadata`는 값이 빈 키를 삭제하고, `{}`이면 모두 삭제 |
| `needs_pass`, `is_encrypted`, `authenticate(password)` | 암호화 상태. `authenticate`는 문서 잠금을 해제하고 pymupdf와 같은 코드를 반환: 비밀번호가 틀리면 0, 그렇지 않으면 1, 2, 4, 6 |
| `is_repaired` | 열 때 손상된 cross-reference 데이터를 복구했는지 여부 |
| `limits`, `complexity` | 문서를 열 때 사용한 정책과, 처리 여부를 판단하기 위한 저비용 구조 정보(페이지, 객체, 스트림) |
| `save(filename, *, garbage=0, deflate=False, use_objstms=False, encryption=, user_pw=, owner_pw=, permissions=)` | PDF를 기록. 쓰기가 완전히 성공한 뒤에만 파일을 교체 |
| `tobytes(..., max_size=512 MiB)` | 같은 출력을 `bytes`로 |
| `close()` | 문서를 해제. `with`가 대신 호출 |

`garbage`는 pymupdf의 단계를 따릅니다. 1은 사용하지 않는 객체를 제거하고, 2는 남은 객체의 번호도 다시 매기며, 3과 4는 중복된 객체와 스트림도 병합합니다. `deflate`는 스트림을 압축하고, `use_objstms`는 객체를 압축된 객체 스트림에 묶습니다. `encryption`은 `PDF_ENCRYPT_NONE`(기본값) 또는 `PDF_ENCRYPT_AES_256`이며, AES-256 없이 비밀번호를 전달하면 예외가 발생합니다.

### 페이지 { #document-pages }

| 멤버 | 용도 |
|---|---|
| `doc[i]`, `load_page(pno)`, 반복 | [`Page`](#page) 객체 |
| `new_page(pno=-1, *, width=595, height=842)` | 빈 페이지 삽입(기본값은 A4) |
| `insert_pdf(docsrc, *, from_page=-1, to_page=-1, start_at=-1)` | 다른 문서의 페이지를 링크, 폼 필드와 함께 복사 |
| `select(page_numbers)` | 나열한 페이지를 나열한 순서로 유지. 번호를 반복하면 페이지를 복제 |
| `delete_page(pno)`, `delete_pages(...)` | 페이지 삭제. `delete_pages`는 번호 하나, 번호 두 개, `from_page=`/`to_page=`, 또는 iterable을 받음 |
| `fullcopy_page(pno, to=-1)` | 페이지의 독립된 복사본을 삽입 |
| `get_outline()` / `set_outline(entries)` | 목차를 `OutlineEntry(level, title, page)` 항목으로 읽거나 교체 |
| `get_page_labels()` / `set_page_labels(labels)` | 앞부분을 로마 숫자로 표시하는 등의 페이지 레이블 범위 |

페이지를 삭제하거나 선택하면 삭제된 페이지만 가리키던 링크, 목차 항목, 폼 필드, 태그 PDF 구조도 함께 제거됩니다. 다만 그 페이지 내용의 모든 사본을 제거하지는 않으므로 안전한 민감 정보 삭제(redaction)는 아닙니다.

### 텍스트와 Markdown { #document-text }

| 멤버 | 용도 |
|---|---|
| `get_text(pages=None)` | 모든 페이지 또는 나열한 페이지의 일반 텍스트를 하나의 문자열로 |
| `get_page_text(pno, option="text")` | `doc[pno].get_text(option)`과 같음 |
| `to_markdown(pages=None, *, table_strategy="lines", max_size=64 MiB)` | 제목, 목록, 강조, 다단 순서, CJK 세로쓰기, 표를 포함한 Markdown |

`table_strategy="lines"`는 괘선으로 그린 표를 변환하고, `"text"`는 정렬이 분명한 테두리 없는 표도 변환하며, `None`은 표 변환을 끕니다.

### 렌더링과 이미지 { #document-rendering }

| 멤버 | 용도 |
|---|---|
| `get_page_pixmap(pno, ...)` | `doc[pno].get_pixmap(...)`과 같음 |
| `render_pages(pages=None, *, matrix=, dpi=, alpha=False, background=, workers=, max_size=512 MiB)` | 모든 페이지 또는 나열한 페이지를 순서대로 병렬 렌더링한 PNG bytes |
| `get_page_images(pno, *, full=False)` | `doc[pno].get_images(full=...)`와 같음 |
| `extract_image(xref)` | pymupdf와 같은 키를 가진 이미지 하나. JPEG 데이터는 그대로, 다른 이미지는 PNG로 반환 |
| `rewrite_images(*, dpi_threshold=None, dpi_target=0, quality=0, ...)` | 이미지를 다운샘플링하고 재압축해 파일 크기를 줄임 |
| `set_fallback_font(font, kind="sans", index=0, *, font_language="ja")` | 글꼴이 포함되지 않은 중국어, 일본어, 한국어 텍스트를 표시할 때 쓰는 글꼴. CJK extra가 자동으로 설정 |

`rewrite_images`는 pymupdf의 키워드를 따릅니다. 그려지는 위치에서 `dpi_threshold`보다 높은 DPI를 가진 이미지는 `dpi_target`으로 리샘플링되고, `quality`(1–100)로 JPEG로 다시 인코딩됩니다. 마스크가 없는 단순한 8비트 회색조 또는 RGB 이미지만 변경하며, 새 인코딩이 더 작아지지 않는 이미지는 그대로 두고, 통계를 반환합니다.

### 폼, 첨부 파일, 기타 데이터 { #document-data }

| 멤버 | 용도 |
|---|---|
| `get_form_fields()` | 모든 폼 필드와 그 이름, 종류, 값, 옵션 |
| `set_form_field(name, value, *, font=, fontfile=, fontbuffer=, fontindex=, font_language=)` | 필드를 채우고 표시 모양을 다시 생성. [폼](#forms) 참고 |
| `attachments` | 포함된 파일을 매핑으로. [첨부 파일](#attachments) 참고 |
| `get_pdfa_claim()` | 파일이 선언한 PDF/A 파트와 적합성 수준(예: `(2, "B")`). 선언만 읽으며 검증하지는 않음 |
| `objects`, `trailer`, `catalog`, `page_xref(pno)` | PDF 객체에 직접 접근. [저수준 객체](#low-level-objects) 참고 |

## Page { #page }

`Page`는 문서의 한 페이지에 대한 뷰입니다. `doc[i]`로 가져옵니다.

### 텍스트와 검색 { #page-text }

| 멤버 | 용도 |
|---|---|
| `get_text(option="text")` | `"text"`, `"words"`, `"blocks"`, `"dict"`, `"rawdict"` |
| `search_for(text, *, max_hits=4096)` | 대소문자 구분 없는 검색. `Rect` 리스트를 반환 |
| `find_tables(*, clip=None, strategy=None)` | 표를 `TableFinder`로. [표](#tables) 참고 |
| `to_markdown(*, table_strategy="lines")` | 이 페이지의 Markdown |
| `get_text_ocr(...)`, `apply_ocr(...)`, `insert_ocr_text_layer(words)` | OCR. [오프라인 OCR](ocr.md) 참고 |
| `content(*, clip=None, annotations=True)` | 페이지가 그리는 모든 텍스트 런, 경로, 이미지. [페이지 내용](#content) 참고 |
| `get_drawings()` | 벡터 경로. [벡터 그림](#drawings) 참고 |
| `get_links()` | 링크와 그 대상 |
| `get_label()` | `"iv"` 같은 페이지 레이블 |

`"words"`는 `Word` 튜플 `(x0, y0, x1, y1, word, block, line, word_no)`를, `"blocks"`는 `Block` 튜플을 반환하며 둘 다 pymupdf와 같은 순서입니다. `"dict"`는 블록, 줄, 스팬을 경계 상자, 글꼴, 크기, 스타일 플래그와 함께 반환합니다. `"rawdict"`는 각 글리프를 추가합니다. [위치가 있는 텍스트](#positioned-text)를 참고하세요.

읽기 순서는 줄과 단을 따르며, 오른쪽에서 왼쪽으로 쓰는 텍스트와 일본어, 중국어 세로쓰기도 포함합니다. 루비, 와리추(割注), 방향이 섞인 텍스트는 해석하지 않습니다.

### 렌더링 { #page-rendering }

| 멤버 | 용도 |
|---|---|
| `get_pixmap(*, matrix=None, dpi=None, clip=None, alpha=False, background=None)` | [`Pixmap`](#pixmap)으로 렌더링 |
| `get_svg_image(*, matrix=None, text_as_path=True)` | SVG 문자열로 렌더링 |
| `get_images(*, full=False)` | 페이지가 사용하는 이미지 객체를 `ImageItem` 튜플로 |
| `get_image_info(*, hashes=False, xrefs=False)` | 각 이미지가 그려지는 위치와 그 크기, 변환 |

pymupdf와 마찬가지로 pixmap은 기본적으로 흰 바탕의 RGB이고, `alpha=True`이면 RGBA입니다. `dpi`가 `matrix`보다 우선하며, `matrix`는 `Matrix(2, 2)` 같은 배율 행렬이어야 합니다. `clip`은 페이지의 일부만 렌더링합니다.

### 페이지 기하 { #page-geometry }

| 멤버 | 용도 |
|---|---|
| `number`, `parent` | 페이지 번호와 문서 |
| `rect` | 회전이 반영된, 보이는 페이지 영역 |
| `rotation` / `set_rotation(deg)` | 90의 배수인 표시 회전 |
| `mediabox`, `cropbox` / `set_mediabox(r)`, `set_cropbox(r)` | pymupdf 규약의 페이지 박스 |
| `xref`, `obj` | 페이지 객체의 번호와 [저수준 핸들](#low-level-objects) |

### 콘텐츠 추가 { #page-content }

| 멤버 | 용도 |
|---|---|
| `insert_text(point, text, ...)` | 한 점에서 시작해 텍스트를 씀. [텍스트와 글꼴](#fonts) 참고 |
| `insert_textbox(rect, text, ...)` | 사각형 안에 줄바꿈한 텍스트를 씀 |
| `insert_image(rect, *, filename=, stream=, pixmap=, rotate=0, keep_proportion=True, overlay=True)` | JPEG, PNG, `Pixmap`을 그리고 객체 번호를 반환 |
| `show_pdf_page(rect, docsrc, pno=0, *, keep_proportion=True, overlay=True)` | 다른 PDF(또는 이 PDF)의 페이지를 벡터로 그림 |
| `draw_line`, `draw_rect`, `draw_circle`, `draw_oval`, `draw_polyline`, `draw_quad`, `draw_sector`, `draw_curve`, `draw_bezier`, `draw_squiggle`, `draw_zigzag` | 도형 하나를 그림. `color`, `fill`, `width`, `dashes` 같은 스타일 옵션은 키워드 전용 |
| `draw(*, underlay=False)` | 경로, 텍스트, 이미지, 페이지를 순서대로 그리는 [캔버스](#canvas) |
| `new_shape()` | 여러 경로와 텍스트를 한 번에 그림. [도형](#shapes) 참고 |
| `replace_text(search, replacement, *, default_char=None)` | 단순(단일 바이트) 글꼴로 그려진 텍스트를 교체 |

같은 이미지나 페이지를 다시 그리면 파일에는 한 번만 저장됩니다.

### 주석과 링크 { #page-annotations }

| 멤버 | 용도 |
|---|---|
| `annots(*, types=None)` | `type`, `rect`, `info`, `colors`, `flags`, `xref`를 가진 읽기 전용 `Annot` 객체 |
| `add_highlight_annot(quads, *, color=, opacity=0.4, content=None)` | 검색 결과 같은 하나 이상의 사각형이나 사변형을 강조 표시 |
| `insert_link(link)` | 웹 링크(`kind=LINK_URI`)를 추가 |

주석 종류와 플래그는 `PDF_ANNOT_*`와 `PDF_ANNOT_IS_*` 상수입니다.

## 위치가 있는 텍스트와 글리프 { #positioned-text }

`page.get_text("rawdict")`는 `"dict"` 구조를 유지하되 각 스팬의 `text`를 `chars` 리스트로 바꿉니다.

| 키 | 의미 |
|---|---|
| `c` | 글리프 하나의 텍스트. "fi" 같은 합자는 두 글자를 모두 유지 |
| `bbox` | 대략적인 경계 `(x0, y0, x1, y1)` |
| `origin` | 글리프의 기준선이 시작하는 위치 `(x, y)` |

```python
with pylopdf.open("report.pdf") as doc:
    layout = doc[0].get_text("rawdict")
    for block in layout["blocks"]:
        for line in block["lines"]:
            for span in line["spans"]:
                for char in span["chars"]:
                    print(char["c"], char["bbox"], char["origin"])
```

경계는 글리프 윤곽이 아니라 기준선과 글꼴 크기로 추정합니다. pylopdf가 간격에서 추론한 공백은 항목으로 추가되지 않으므로, `c` 값을 이어 붙여도 `get_text()`와 항상 같지는 않습니다. PDF에 Unicode 매핑이 없는 글리프는 `PylopdfWarning`과 함께 건너뜁니다.

구조는 [pymupdf의 rawdict](https://pymupdf.readthedocs.io/en/latest/textpage.html#structure-of-dictionary-outputs)와 비슷하지만 텍스트 블록만 반환하며, 이미지나 벡터 블록, 색상, 불투명도, 추가 글꼴 메트릭은 없습니다. [오버레이 예제](https://github.com/yhay81/pylopdf/blob/main/examples/rawdict-overlay.py)는 글리프 경계와 원점을 HTML 미리보기로 그립니다.

## 페이지 내용 { #content }

`page.content()`는 페이지가 그리는 것을 hayro가 해석한 그대로 그리는 순서대로 반환합니다. 표시 좌표의 `TextRun`, `Path`, `Image`로 이루어진 `Content` 시퀀스이며, 모든 값은 불변입니다.

```python
with pylopdf.open("report.pdf") as doc:
    content = doc[0].content()
    for run in content.text_runs:
        print(run.text, run.font.name, run.size, run.fill)
        for glyph in run.glyphs:
            print(glyph.text, glyph.origin, glyph.bbox)
    for path in content.paths:
        print(path.bbox, path.fill, path.stroke)
    for image in content.images:
        print(image.xref, image.bbox, image.width, image.height)
```

| 항목 | 필드 |
|---|---|
| `TextRun` | `glyphs`, `font`, `render_mode`, `fill`, `stroke`, `bbox`. `text`와 `size`는 이들에서 구함 |
| `Glyph` | `glyph_id`, `text`, `matrix`, `advance`, `bbox`. `origin`과 `size`는 행렬에서 구함 |
| `Path` | `segments`(`MoveTo`, `LineTo`, `CurveTo`, `ClosePath`), `fill`, `fill_rule`, `stroke`, `bbox` |
| `Image` | `xref`, `width`, `height`, `bits`, `colorspace`, `components`, `is_stencil`, `mask`, `encoded_size`, `resolution`, `matrix`, `bbox` |

모든 항목에는 `blend_mode`, `soft_mask`, `context`도 있습니다. `context`는 항목을 둘러싼 클립(`clips`), 투명 그룹(`groups`), 마크된 콘텐츠(`marked_content`)를 바깥쪽부터 나열한 `GraphicsContext`이며, `opacity`와 `clip_bbox`는 이들을 합친 값입니다. 같은 컨텍스트에서 그린 항목은 컨텍스트 객체 하나를 공유합니다.

페인트는 `Color`(sRGB와 알파), `ShadingPattern`, `TilingPattern` 중 하나입니다. `Stroke`는 `paint`, `width`, `cap`, `join`, `miter_limit`, `dashes`, `dash_offset`을 표시 공간 크기로 가집니다. 텍스트 런의 `font`는 폰트 프로그램의 `name`, `weight`, 스타일 플래그를 가진 `FontRef`입니다.

- 글리프 상자는 글리프 윤곽선의 정확한 외접 상자이므로 공백에는 상자가 없습니다. Type 3 글리프의 상자는 그 프로시저가 그리는 범위입니다.
- 클립을 함께 하는 렌더링 모드의 텍스트는 채우기나 선으로 그려지며, 클립만 하는 텍스트는 항목이 되지 않습니다. 같은 경로를 채운 직후에 선을 그으면 `B`와 마찬가지로 경로 하나가 됩니다.
- `sh`로 그린 셰이딩은 클립된 영역을 덮고 `ShadingPattern`으로 채우는 `Path`입니다.
- hayro는 모든 이미지를 그 시점의 불투명도, 블렌드 모드, 소프트 마스크를 가진 투명 그룹 안에 그리므로 이미지의 컨텍스트는 그 그룹으로 끝납니다. 보이는 영역 전체를 덮는 클립은 hayro와 마찬가지로 넣지 않습니다.

`clip=`은 외접 상자가 주어진 사각형에 닿는 항목만 남기고, `annotations=False`는 주석의 모양을 뺍니다. 한 번의 호출이 읽는 글리프, 경로와 클립의 세그먼트, 항목은 합쳐서 `max_elements`까지이며 기본값은 262,144입니다.

hayro가 아직 알려 주지 않는 정보는 모델에 없습니다. 색의 색 공간과 성분, 레이어 이름 같은 마크된 콘텐츠의 속성, `FontRef`가 가리키는 폰트 딕셔너리, 이미지 마스크의 객체입니다. 이들은 [엔진 작업](https://github.com/yhay81/pylopdf/blob/main/ROADMAP.md#engine-work)에 나열되어 있습니다.

## 벡터 그림 { #drawings }

`page.get_drawings()`는 채우거나 선을 그은 경로마다 pymupdf 형식의 `DrawingInfo` 딕셔너리를 하나씩 반환합니다. `type`(`"f"`, `"s"`, `"fs"`), `items`(직선과 3차 곡선), `rect`, 색상, 불투명도, 선 너비, 끝 모양(cap), 연결 모양(join), 대시, `seqno`를 포함합니다. 패턴으로 채운 경로의 색상은 `None`입니다. [`page.content()`](#content)의 경로에는 클립, 그룹, 마크된 콘텐츠도 있습니다.

## 캔버스 { #canvas }

`page.draw()`는 `Canvas`를 반환합니다. `with` 블록 안에서 사용하세요. 블록 안에서 그린 모든 것은 블록이 끝날 때 호출한 순서대로 하나의 Form XObject로 기록되며, 블록에서 예외가 발생하면 아무것도 기록되지 않습니다. 좌표는 다른 곳과 마찬가지로 표시 좌표입니다.

```python
from pylopdf import Color, Matrix, PathBuilder, Stroke

navy = Color(0.1, 0.2, 0.4)
with page.draw() as canvas:
    canvas.rect((40, 40, 555, 120), radius=8, fill=Color(0.95, 0.96, 0.97))
    canvas.text("Inspection report", (56, 90), size=20, fill=navy)
    canvas.line((56, 105), (539, 105), stroke=Stroke(paint=navy, width=0.5))
    fit = canvas.textbox((56, 140, 539, 400), body, size=10.5)
    canvas.image((400, 420, 539, 520), "chart.png")
    with canvas.opacity(0.15), canvas.transform(Matrix(1, 0, 0, 1, 0, 600)):
        canvas.page((56, 0, 539, 200), letterhead[0])
```

| 메서드 | 그리는 것 |
|---|---|
| `path(path, *, fill=None, stroke=None, fill_rule=FillRule.NONZERO)` | `PathBuilder`, `Path` 항목의 기하 형태, 또는 세그먼트 리스트 |
| `line`, `rect(rect, *, radius=0)`, `circle`, `ellipse`, `polygon`, `bezier` | 각각 경로 하나. 옵션은 `path`와 같음 |
| `text(text, at, *, font=None, size=11, fill=..., stroke=None, line_height=None)` | 첫 기준선이 `at`에서 시작하는 줄 |
| `textbox(rect, text, *, font=None, size=11, align="left", line_height=None, ...)` | `rect` 안에서 줄바꿈한 텍스트. `TextFit`을 반환 |
| `image(rect, source, *, keep_aspect=True, rotate=0)` | JPEG나 PNG 경로, 인코딩된 bytes, 또는 `Pixmap` |
| `page(rect, source, *, keep_aspect=True)` | 열려 있는 아무 문서(이 문서 포함)의 페이지 |
| `draw(item)` | [`page.content()`](#content)가 반환한 `Path` 항목 |

경로는 `Color` 채우기와 선을 받습니다. 선은 너비, 끝 모양, 연결 모양, 마이터 한계, 대시를 가진 `Stroke`이거나, 1포인트 선을 뜻하는 `Color`입니다. 둘 다 없는 경로는 1포인트 검은 선으로 그립니다. 색의 `alpha`는 그 채우기나 선을 반투명하게 만듭니다. `PathBuilder`는 `move_to`, `line_to`, `curve_to`, `quad_to`, `close`로 기하 형태를 만들고, `rect`, `ellipse`, `circle`, `polygon`, `sector`, `zigzag`, `wave`로 완성된 도형을 추가합니다. 모든 메서드가 빌더 자신을 반환하므로 호출을 이어서 쓸 수 있습니다.

텍스트는 [`Font`](#font)로 그립니다. OpenType 글꼴은 HarfRust로 셰이핑되며, 몇 번의 호출에서 쓰이든 캔버스마다 한 번만 서브셋으로 포함됩니다. `font`가 없으면 서유럽 텍스트는 Helvetica로, 그 밖의 텍스트는 해당 문자 체계를 위한 설치된 [글꼴 extra](getting-started.md#installation)로 그립니다. 텍스트는 채우거나, 선으로 그리거나, 둘 다 할 수 있습니다. `textbox`는 Unicode가 줄바꿈을 허용하는 위치에서 줄을 나눕니다. 텍스트가 들어가지 않으면 아무것도 그리지 않으며, `TextFit.spare_height`는 부족한 만큼을 나타내는 음수가 되므로 `fit.fits`는 거짓입니다.

다음 네 가지 컨텍스트 관리자는 그 안에서 그린 모든 것에 적용되며, 중첩할 수 있습니다.

| 범위 | 효과 |
|---|---|
| `clip(path, *, fill_rule=...)` | 경로 안쪽에만 그림 |
| `transform(matrix)` | 그림을 변환. 중첩된 변환은 가장 안쪽부터 적용 |
| `opacity(alpha)` | 그림을 하나의 그룹으로 합성한 뒤 불투명도 `alpha`로 그림 |
| `blend(mode)` | `BlendMode`로 그림을 그 아래에 있는 것과 혼합 |

`page.draw(underlay=True)`는 페이지의 기존 내용 아래에 씁니다. 캔버스가 여러 번 그리는 이미지나 페이지, 또는 문서가 이전에 배치한 이미지나 페이지는 한 번만 저장됩니다. 페이지는 `canvas.page()`를 호출한 시점의 상태로 그려집니다. `draw(item)`은 항목의 클립, 그룹 불투명도, 블렌드 모드를 재현하지만 소프트 마스크는 재현하지 않습니다. 텍스트 런, 이미지, 패턴 페인트는 아직 그릴 수 없습니다. 캔버스 하나에는 경로 점이 최대 1,048,576개까지 들어가고 범위는 최대 16단계까지 중첩할 수 있으며, 이를 넘으면 `LimitError`가 발생하고 그 코드는 `path_point_count` 또는 `canvas_scope_depth`입니다.

## 표 { #tables }

`page.find_tables()`는 병합된 셀을 포함해 괘선으로 그린 표를 찾습니다. `strategy="text"`는 행과 열이 분명히 정렬된 테두리 없는 표도 감지합니다. 정렬된 다단 본문이 표처럼 보일 수 있으므로 기본값은 아닙니다. `clip=`은 사각형 안에 완전히 들어가는 표만 남깁니다.

결과는 `TableFinder`이며, 그 `tables`는 `bbox`, `row_count`, `col_count`, `cells`, `extract()`(행별 셀 텍스트, 병합 셀이 덮는 셀은 `None`), `to_markdown()`을 가진 `Table` 객체입니다. `Table.confidence`는 후보 순위를 매기기 위한 0–1 점수이며 확률은 아닙니다. `Table.diagnostics`가 그 근거를 설명합니다.

## 텍스트와 글꼴 { #fonts }

### 텍스트 쓰기 { #writing-text }

```python
page.insert_text(point, text, *, fontsize=11, lineheight=None, font=None,
                 fontname="helv", fontfile=None, fontbuffer=None, fontindex=0,
                 font_language=None, color=(0, 0, 0), overlay=True)
page.insert_textbox(rect, text, *, align=pylopdf.TEXT_ALIGN_LEFT,
                    expandtabs=1, ...)   # 글꼴 옵션은 동일
```

글꼴은 다음 인수 중 하나로 선택합니다.

- 지정하지 않거나 `fontname`: 텍스트에 필요하면 설치된 extra의 글꼴, 그렇지 않으면 Standard 14 글꼴(기본값 `"helv"`, 또는 `"Times-Bold"` 등). Standard 14 글꼴은 포함되지 않으며 서유럽(WinAnsi) 문자만 지원합니다.
- `fontfile` 또는 `fontbuffer`: OpenType 글꼴. 사용한 글리프만 포함됩니다.
- `font`: 이미 불러온 [`Font`](#font).

한 글꼴이 텍스트의 모든 문자를 포함해야 합니다. pylopdf는 문자마다 글꼴을 바꾸지 않습니다. 가나, 한글, 주음부호, 아랍 문자, 히브리 문자, 데바나가리, 태국 문자로 된 텍스트는 일치하는 설치된 extra를 자동으로 선택합니다. 한자만 있는 텍스트는 언어를 판단할 수 없으므로 기본적으로 일본어 글꼴을 사용합니다. 선택하려면 `font_language="zh-CN"`, `"zh-TW"`, `"ko"`, `"ja"`를 전달하세요.

`insert_text`는 문자열을 `str.splitlines()`로 줄 단위로 나누거나, 항목 하나가 한 줄인 리스트를 받습니다. 줄 간격은 `lineheight × fontsize`이며, `lineheight`의 기본값은 pymupdf와 마찬가지로 글꼴의 ascender와 descender에서 구합니다. 페이지 아래쪽을 벗어나는 줄은 건너뛰며, 메서드는 그린 줄 수를 반환합니다.

`insert_textbox`는 Unicode가 줄바꿈을 허용하는 위치(UAX #14)에서 텍스트를 줄바꿈하고, 명시적 줄바꿈을 유지하며, 탭을 확장하고, 한 줄에 들어가지 않는 긴 단어를 나눕니다. `align`은 `TEXT_ALIGN_LEFT`, `TEXT_ALIGN_CENTER`, `TEXT_ALIGN_RIGHT`, `TEXT_ALIGN_JUSTIFY`를 받습니다. 남은 높이를 반환하며, 음수이면 텍스트가 들어가지 않았다는 뜻이고 이때는 아무것도 그리지 않습니다.

오른쪽에서 왼쪽으로 쓰는 텍스트는 올바르게 셰이핑됩니다. PDF를 다시 읽으면 완전히 오른쪽에서 왼쪽으로 쓰는 줄, 또는 오른쪽에서 왼쪽으로 쓰는 텍스트 안에 라틴 단어나 숫자 하나가 들어 있는 줄은 논리 순서로 나오고, 그 밖의 방향이 섞인 줄은 시각 순서로 나옵니다. 일부 복잡한 문자 체계의 클러스터, 특히 긴 데바나가리 텍스트는 원래 텍스트와 약간 다르게 추출될 수 있습니다.

### Font { #font }

```python
pylopdf.Font(fontname=None, fontfile=None, fontbuffer=None, *, language=None,
             is_bold=False, is_italic=False, is_serif=False, weight=None,
             fontindex=0, family=None, allow_regular_fallback=False)
```

| 예 | 불러오는 것 |
|---|---|
| `Font("helv")`, `Font("Times-Bold")` | Standard 14 글꼴 |
| `Font(fontfile="NotoSans-Regular.ttf")` | OpenType 파일 |
| `Font(fontfile="NotoSansCJKjp-VF.otf", weight=700)` | 가변 글꼴의 한 굵기 |
| `Font(language="ja")`, `Font(family="Noto Sans JP")` | 설치된 extra의 글꼴. `is_serif=True`이면 세리프 |
| `Font(language="he", is_bold=True)` | 설치된 가변 글꼴의 볼드 굵기 |

메서드와 속성: `text_length(text, fontsize=11)`(커닝을 포함해 그렸을 때의 폭), `ascender`, `descender`, `has_glyph(chr)`, `name`, `family`, `weight`, `is_bold`, `is_italic`, `is_serif`, `units_per_em`.

`weight` 없이 불러온 가변 글꼴은 기본 인스턴스를 사용하며, 기본 인스턴스가 항상 Regular 굵기인 것은 아닙니다. 가변 글꼴인 Noto Sans CJK JP의 기본값은 Thin입니다. Regular를 쓰려면 `weight=400`을 전달하세요. 그리기 메서드의 `fontfile`과 `fontbuffer` 인수도 기본 인스턴스를 사용합니다. 다른 굵기로 그리려면 `Font`를 `font=`로 전달하세요.

아랍어, 히브리어, 힌디어, 태국어 extra는 가변 글꼴을 제공하므로 축 위의 어떤 굵기든 쓸 수 있습니다(100–900, Noto Naskh Arabic은 400–700). CJK extra는 스타일마다 Regular 글꼴 하나만 제공합니다. `Font(language="ja", is_bold=True)`는 `PdfError`를 발생시키므로, 대신 `fontfile=`로 볼드 파일을 불러오거나, `allow_regular_fallback=True`를 전달해 경고와 함께 Regular 글꼴을 받아들이세요. pylopdf는 글리프를 변형해 볼드나 이탤릭을 흉내 내지 않습니다.

pylopdf는 시스템 글꼴 디렉터리를 검색하지 않고 전역 글꼴 레지스트리도 없으므로, 출력은 전달한 글꼴에만 의존합니다.

### 도형 { #shapes }

`page.new_shape()`는 여러 경로와 텍스트를 하나의 단위로 그리는 pymupdf의 도구인 `Shape`를 반환합니다.

```python
regular = pylopdf.Font("helv")
bold = pylopdf.Font("hebo")
body = "Findings are listed below. " * 20

with page.new_shape() as shape:
    shape.draw_rect((50, 50, 545, 792))
    shape.finish(color=(0.72, 0.78, 0.82), fill=(0.95, 0.96, 0.97), width=0.5)
    shape.draw_circle((80, 140), 7)
    shape.finish(color=(1, 1, 1), fill=(0.83, 0.26, 0.05), width=1.2)
    shape.insert_text((56, 80), "Inspection report", font=bold, fontsize=18)
    shape.insert_textbox((56, 160, 540, 400), body, font=regular, fontsize=10.5)
```

- `draw_*` 메서드는 경로 선분을 추가하고 끝점을 반환합니다. 앞 선분이 끝난 곳에서 시작하는 선분은 같은 경로를 이어 갑니다.
- `finish(*, width=1, color=(0,), fill=None, lineCap=0, lineJoin=0, dashes=None, even_odd=False, closePath=True, fill_opacity=1, stroke_opacity=1)`는 이전 `finish` 이후 그린 모든 것에 스타일을 적용합니다. 색상은 0에서 1 사이 값의 회색조, RGB, CMYK 튜플입니다.
- `insert_text`와 `insert_textbox`는 `Page`의 메서드와 같이 동작합니다.
- `commit()`은 도형을 그립니다. 경로를 순서대로 그린 뒤 모든 텍스트를 그 위에 그립니다. `with` 블록은 예외 없이 끝나면 커밋합니다.

각 글꼴은 몇 줄에서 쓰이든 커밋마다 한 번만 포함되므로, 페이지의 텍스트를 하나의 shape로 그리면 파일이 작게 유지됩니다. pymupdf와 달리 pylopdf는 그림을 조용히 버리거나 검게 칠하지 않고 `PdfError`를 발생시킵니다. `commit()`은 `finish()`로 스타일을 지정하지 않은 경로를 거부하고, `finish()`는 아무것도 그리지 않는 스타일을 거부합니다. `morph=`와 `oc=`는 아직 지원하지 않습니다.

### 폼 { #forms }

`set_form_field`는 텍스트, 콤보 박스, 리스트 박스, 체크 박스, 라디오 버튼 필드를 채우고 각각의 표시 모양을 다시 생성합니다. 서유럽(WinAnsi) 텍스트는 Helvetica로 크기를 맞춥니다. 그 밖의 문자에는 `fontfile`, `fontbuffer`, 불러온 `font`를 전달하거나 글꼴 extra를 설치하세요. 콤(comb) 필드, 정렬, 최대 길이를 지키며, 너무 긴 값은 예외를 발생시키고 문서를 변경하지 않습니다. 서식 있는 텍스트(rich text), 푸시 버튼 동작, 서명은 지원하지 않습니다. 서명은 [생태계 연동](ecosystem.md#signatures)을 참고하세요.

## 첨부 파일 { #attachments }

`doc.attachments`는 이름에서 `Attachment` 객체로의 매핑입니다.

```python
doc.attachments.add("data.csv", csv_bytes, description="Source data")
for name in doc.attachments:
    item = doc.attachments[name]
    print(item.filename, item.size, item.description)
data = doc.attachments["data.csv"].read()
doc.attachments["data.csv"].write(new_bytes)
del doc.attachments["data.csv"]
```

`Attachment`에는 `name`, `creation_date`, `mod_date`도 있습니다. 없는 이름은 `KeyError`를 발생시킵니다.

## 저수준 객체 { #low-level-objects }

`doc.objects`는 객체 번호를 PDF 객체에 매핑하며, `doc.trailer`, `doc.catalog`, `page.obj`는 딕셔너리 핸들입니다. 객체는 PDF 구문이 아닌 Python 값입니다.

```python
from pylopdf import Name

catalog = doc.catalog
catalog["PageLayout"] = Name("TwoColumnLeft")   # 이름. 일반 str은 텍스트 문자열
font = doc.objects.add({"Type": Name("Font"), "Subtype": Name("Type1"), "BaseFont": Name("Helvetica")})
content = doc.objects.add_stream(b"BT /F9 24 Tf 72 72 Td (Hi) Tj ET")
page.obj["Resources"] = {"Font": {"F9": doc.objects[font]}}   # 참조로 저장
page.obj["Contents"] = doc.objects[content]
doc.objects[content].read()                     # 디코딩된 스트림 데이터
```

| PDF 값 | Python 값 |
|---|---|
| null, boolean, integer, real | `None`, `bool`, `int`, `float` |
| name | `Name`, 앞의 슬래시가 없는 `str` |
| string | `String`, `.text`로 디코딩하는 `bytes` |
| array, dictionary | `Array`(변경 가능한 시퀀스), `Dictionary`(변경 가능한 매핑) |
| stream | `Stream`, `read(*, decode=True)`와 `write(data, *, filter="FlateDecode")`를 가진 딕셔너리 |

변경은 즉시 적용됩니다. 없는 객체에 대한 참조는 `None`으로 읽힙니다. 핸들을 다른 객체 안에 저장하면 그 객체에 대한 참조가 저장됩니다. 객체가 제거되거나, `save(garbage=2)` 이상이 객체 번호를 다시 매기면 핸들은 `StaleObjectError`를 발생시킵니다. pylopdf는 편집 내용이 PDF로서 타당한지 검사하지 않습니다.

## 기하 { #geometry-types }

`Point`, `Matrix`, `Rect`, `IRect`, `Quad`는 변경할 수 없는 네임드 튜플이므로 인덱싱, 언패킹, 비교가 튜플처럼 동작합니다. 연산자는 새 값을 반환합니다. `transform()`이나 `normalize()` 같은 pymupdf의 제자리 변경 메서드는 없으므로 결과를 대입하세요(`r = r * m`). 모든 좌표는 유한한 수여야 합니다.

### Point와 Matrix { #geometry }

`Point(x, y)`는 위치입니다. `Matrix(a, b, c, d, e, f)`는 아핀 변환이며, `Matrix(sx, sy)`는 배율, `Matrix(degrees)`는 회전, `Matrix(sx, sy, 1)`은 기울이기(shear)입니다.

```python
from pylopdf import Matrix, Point

scale = Matrix(2, 3)
shift = Matrix(1, 0, 0, 1, 20, 30)
transform = scale * shift            # 배율을 먼저, 그다음 이동
position = Point(10, 20) * transform  # Point(x=40.0, y=90.0)
original = position / transform      # Point(x=10.0, y=20.0)
```

| 연산 | 결과 |
|---|---|
| `p + q`, `p - q`, `p * 2`, `-p` | 벡터 연산 |
| `p * m` | 변환된 점: `(a*x + c*y + e, b*x + d*y + f)` |
| `m1 * m2` | `m1`을 먼저, 그다음 `m2`를 적용 |
| `~m` | 역행렬(`m`의 역행렬이 없으면 영행렬) |
| `p / m`, `m1 / m2` | 역행렬을 곱함. 역행렬이 없으면 `ZeroDivisionError` |
| `abs(p)`, `p.unit`, `p.distance_to(other, unit="px")` | 길이, 방향, 거리(`px`, `in`, `cm`, `mm`) |
| `m.is_rectilinear` | 변환이 가장자리를 수평·수직으로 유지하는지 여부 |

y축이 아래를 향하므로 `Matrix(90)`은 `(1, 0)`을 `(0, 1)`로 바꿉니다. `IdentityMatrix`(`Identity`도 가능)는 단위 행렬입니다.

### Rect, IRect, Quad { #rectangles }

`Rect(x0, y0, x1, y1)`는 튜플이나, `Rect(top_left, bottom_right)`처럼 모서리 점도 받습니다. `IRect`는 같은 인수를 받아 바깥쪽으로 반올림한 정수를 사용합니다. `Quad(ul, ur, ll, lr)`는 회전된 사각형 같은 임의의 사변형이며, `rect.quad`와 `quad.rect`로 서로 변환합니다.

```python
from pylopdf import Matrix

hits = page.search_for("Total")
area = hits[0] | hits[-1]                    # 첫 번째와 마지막 결과를 덮음
inside = [hit for hit in hits if hit in page.rect]
box = area * Matrix(1, 0, 0, 1, 0, 20)        # 20포인트 아래로 이동
quad = area.morph(area.tl, Matrix(30))       # 한 모서리를 중심으로 30도 회전
```

| 연산 | 결과 |
|---|---|
| `r | p`, `r | other` | 점이나 사각형까지 덮는 가장 작은 사각형 |
| `r & other` | 겹치는 부분 |
| `r * m`, `r / m` | 변환된 사각형의 경계 상자 |
| `r + other`, `r * 2` | 좌표 연산 |
| `p in r`, `other in r` | 포함 여부 |
| `r.width`, `r.height`, `abs(r)`, `r.get_area(unit="px")` | 크기와 면적 |
| `r.is_empty`, `r.is_valid`, `r.is_infinite`, `r.intersects(other)` | 판정 |
| `r.normalized()` | `x0 <= x1`이고 `y0 <= y1`인 사각형 |
| `r.round()`, `r.irect` | 덮는 `IRect` |
| `r.torect(other)` | 한 사각형을 다른 사각형으로 옮기는 행렬 |
| `r.morph(point, m)`, `q.morph(point, m)` | 고정점을 중심으로 변환한 `Quad` |
| `q * m`, `p in q`, `q.is_rectangular`, `q.is_convex` | Quad 변환과 판정 |

`EMPTY_RECT()`, `INFINITE_RECT()`, 그 `IRECT`와 `QUAD` 형태, `planish_line(p1, p2)`는 pymupdf의 도우미 함수입니다. pymupdf와 달리, 사각형이 quad 안에 있는지 확인할 때는 네 모서리를 모두 검사합니다.

## Pixmap { #pixmap }

`Pixmap`은 렌더링된 페이지의 픽셀을 담으며 변경할 수 없습니다.

| 멤버 | 용도 |
|---|---|
| `width`, `height`, `n`, `alpha`, `stride`, `colorspace` | 크기와 배치. `n`은 RGB이면 3, RGBA이면 4 |
| `samples` | 픽셀을 `bytes`로 |
| `memoryview(pix)`, `np.frombuffer(pix, dtype=np.uint8)` | 복사 없이 픽셀에 접근 |
| `tobytes()` | PNG bytes |
| `save(filename)` | PNG 파일로 기록 |

## 모듈 함수와 타입 { #module-level }

| 이름 | 용도 |
|---|---|
| `open(...)` | `Document(...)`와 같음 |
| `peek_metadata(filename=None, stream=None, password=None, *, max_file_size=None)` | 문서를 불러오지 않고 메타데이터와 페이지 수를 읽음 |
| `DocumentLimits`, `DocumentLimits.web()` | 신뢰할 수 없는 입력을 위한 리소스 제한. [보안](security.md#untrusted-pdfs) 참고 |
| `OcrEngine` | 재사용 가능한 OCR 엔진. [오프라인 OCR](ocr.md) 참고 |
| `Canvas`, `PathBuilder`, `TextFit` | [캔버스](#canvas) 참고 |
| `Font`, `Shape` | [Font](#font)와 [도형](#shapes) 참고 |
| `Base14_fontnames`, `Base14_fontdict` | `fontname=`이 받는 Standard 14 글꼴 이름 |
| `Name`, `String`, `Array`, `Dictionary`, `Stream`, `Objects` | [저수준 객체](#low-level-objects) |
| `Content`, `TextRun`, `Path`, `Image`, `Glyph`, `FontRef`, `Color`, `Stroke`, `GraphicsContext` 등 내용 타입 | [페이지 내용](#content) |
| `Colorspace`, `csRGB`, `csGRAY`, `csCMYK` | pymupdf의 장치 색 공간 |

상수는 pymupdf의 이름을 유지하며 열거형으로도 묶여 있습니다. `Permissions`(`PDF_PERM_*`), `EncryptionMethod`(`PDF_ENCRYPT_*`), `LinkKind`(`LINK_*`), `TextAlign`(`TEXT_ALIGN_*`), `AnnotType`(`PDF_ANNOT_*`), `AnnotFlags`(`PDF_ANNOT_IS_*`)입니다. 페이지 내용의 열거형은 `BlendMode`, `FillRule`, `LineCap`, `LineJoin`, `TextRenderMode`, `ShadingType`, `SoftMaskType`, `ImageMask`입니다.

API가 반환하는 딕셔너리는 일반 `dict` 객체이며, 타입 검사기를 위해 `TextPageDict`, `RawTextPageDict`, `DrawingInfo`, `ImageInfo`, `LinkInfo`, `FormFieldInfo`, `DocumentMetadata`, `OcrWord` 같은 `TypedDict` 클래스로 기술됩니다. `Word`, `Block`, `ImageItem`, `OutlineEntry` 같은 튜플은 네임드 튜플입니다.

## 예외와 경고 { #exceptions }

| 이름 | 발생하는 경우 |
|---|---|
| `PdfError` | pylopdf 오류의 기본 클래스. `ValueError`의 하위 클래스 |
| `FileDataError` | 입력이 읽을 수 있는 PDF가 아님. pymupdf와 마찬가지로 `RuntimeError`이기도 함 |
| `EmptyFileError` | 입력이 비어 있음 |
| `PasswordError` | 주어진 비밀번호로 복호화하지 못함 |
| `EncryptedDocumentError` | 먼저 `authenticate()`로 문서 잠금을 해제해야 함 |
| `LimitError` | [크기 또는 리소스 제한](security.md#error-codes)에 도달함. `.code`가 어느 제한인지 알려 줌 |
| `DocumentClosedError` | 문서가 닫혔음 |
| `StalePageError` | 페이지가 추가, 삭제, 재정렬되기 전에 가져온 `Page`임 |
| `StaleObjectError` | 저수준 핸들이 더 이상 객체를 가리키지 않음 |
| `OcrError` | OCR을 실행할 수 없음 |
| `PylopdfWarning` | 복구된 파일이나 읽을 수 없는 글꼴처럼 복구 가능한 문제 |

없는 파일은 Python 내장 `FileNotFoundError`를 발생시킵니다.
