---
title: API 개요
description: pylopdf의 Document, Page, Pixmap, Rect, 권한, 경고, 예외를 한눈에 보는 API 지도입니다.
---

# API 개요

전체 docstring은 패키지 안에 있으며(`help(pylopdf.Document)`), 이 페이지는 API
지도입니다. 페이지 번호는 목차를 포함해 모두 0부터 시작합니다. 모든 좌표는 왼쪽
위가 원점인 표시 공간입니다. [API 안정성 정책](stability.md)은 공개 경계와 사용 중단
절차를 정의합니다. 아래의 byte, 개수, 깊이 상한은 모두 악의적인 입력으로부터
프로세스를 보호하기 위한 것입니다. 정책은
[신뢰할 수 없는 PDF 처리](security.md#untrusted-pdfs)에서 설명하며, 상한을 넘으면
안정적인 `.code`를 가진 `LimitError`가 발생합니다.

## Document { #document }

`pylopdf.Document(filename=None, stream=None, filetype=None, *, password=None, limits=None, max_decompressed_size=None)` —
`pylopdf.open`은 pymupdf와 같이 동일한 호출 가능 객체이며 컨텍스트 관리자를 지원합니다.
`stream`은 bytes, `bytearray`, `memoryview`, `io.BytesIO`를 받고 `filetype`은
`"pdf"`를 받습니다. pymupdf의 `open("pdf", data)`처럼 `stream`과 함께 전달한 문자열
`filename`은 파일 형식을 뜻합니다.

| 멤버 | 용도 |
|---|---|
| `doc[i]`, `load_page(pno)`, 반복 | `Page` 뷰. 음수 인덱스도 사용 가능. 구조를 변경한 뒤에는 페이지를 다시 가져오세요. |
| `page_count`, `len(doc)` | 페이지 수. |
| `limits`, `complexity` | 열 때 정한 불변 리소스 정책, 그리고 stream 디코딩 없이 모은 저비용 구조 지표. |
| `needs_pass`, `is_encrypted`, `authenticate(pw)` | 암호화 상태와 잠금 해제. pymupdf와 같은 반환 코드 0/1/2/4/6을 쓰며, 빈 user password로 열린 문서에서도 코드를 검사합니다. |
| `is_repaired` | 열 때 손상된 cross-reference 데이터를 복구했는지 여부(offset 보정 또는 table 재구성). 저장하면 xref 데이터가 정규화됩니다. |
| `objects`, `trailer`, `catalog`, `page_xref(pno)` | object graph에 대한 타입 있는 접근으로 pymupdf의 `xref_*` 메서드를 대체합니다. [저수준 객체](#low-level-objects) 참조. |
| `metadata`, `set_metadata(m)` | pymupdf의 메타데이터 키 읽기와 쓰기: `format`, `trapped`를 포함한 Info 필드, `encryption`. |
| `get_page_text(pno, option)` | 한 페이지의 텍스트. `option`은 `"text"`, `"words"`, `"blocks"`, `"dict"`, `"rawdict"`. |
| `get_text(pages=None)` | 모든 페이지 또는 페이지 목록의 plain text를 한 번에 추출. |
| `to_markdown(pages=None, table_strategy="lines")` | 제목, CJK, 강조, 목록, 단, 세로쓰기 순서, 표를 포함한 Markdown. |
| `extract_image(xref)`, `get_page_images(pno, *, full=False)` | pymupdf와 같은 키로 이미지 하나의 데이터를 반환, 그리고 페이지의 이미지 목록. |
| `get_page_pixmap(pno, *, matrix=, dpi=, clip=, alpha=False)` | 페이지 `Pixmap`. pymupdf의 `doc[pno].get_pixmap()` 간편형. |
| `render_pages(pages=None, *, dpi=, alpha=False, workers=)` | 모든 페이지 또는 페이지 목록을 순서를 보장하며 병렬로 PNG 렌더링. |
| `rewrite_images(*, dpi_threshold=None, dpi_target=0, quality=0)` | 배치를 고려한 안전한 raster 이미지 축소와 JPEG 재압축. 타입 있는 byte·개수 통계를 반환. |
| `set_fallback_font(font, kind=, index=, font_language="ja")` | 임베드되지 않은 Adobe Japan1, Korea1, GB1, CNS1 글꼴을 위한 지역별 CJK fallback. |
| `select`, `delete_page(s)`, `insert_pdf(docsrc, *, from_page=-1, to_page=-1, start_at=-1)`, `new_page`, `fullcopy_page` | 페이지 관리. |
| `get_outline()`, `set_outline(entries)` | 0부터 시작하는 페이지를 가진 `OutlineEntry(level, title, page)` 항목으로 표현한 목차. |
| `get_page_labels()`, `set_page_labels(labels)` | 페이지 레이블 범위. |
| `get_form_fields()`, `set_form_field(name, value, font=, fontfile=)` | AcroForm 필드 목록, 그리고 네이티브 widget appearance로 필드 하나 채우기. [폼 채우기](#form-filling) 참조. |
| `attachments` | `Attachment` 핸들의 `Attachments` 매핑. |
| `get_pdfa_claim()` | XMP의 PDF/A 선언 읽기. 검증은 아닙니다. |
| `save(...)`, `tobytes(...)` | PDF를 파일로 쓰거나 bytes로 반환. |
| `close()` | 문서 닫기. `with`로도 호출됩니다. |

**세부 사항**

- `set_metadata`는 UTF-16BE를 지원하고, `format`과 `encryption`은 무시하며, 거짓
  값의 항목은 삭제하고, `{}`이면 Info 사전을 삭제하며, 원자적으로 씁니다.
- `get_text`는 명시적 entry를 4,096개씩 chunk로 처리하며 각 chunk는 interpreter의
  글꼴 cache 하나를 공유합니다. `to_markdown`은 상한이 있는 선형 entry builder로
  페이지 단위 2-pass 처리를 합니다.
- `extract_image`는 DCT를 JPEG 그대로 내보내고, 다른 filter는 PNG로 디코딩하며,
  soft mask는 제외합니다.
- `get_page_pixmap`과 `render_pages`는 모두 `matrix=`, `dpi=`, `alpha=`,
  `background=`를 받고, `get_page_pixmap`은 `clip=`도 받습니다.
- `rewrite_images`는 pymupdf의 키워드 `dpi_threshold=None`, `dpi_target=0`,
  `quality=0`, `lossy=True`, `lossless=True`, `color=True`, `gray=True`를 받습니다.
  `dpi_threshold`를 넘는 축은 `dpi_target`으로 재샘플링되고, `quality` 0은 재압축
  없음을 뜻하므로 재샘플링에는 `quality`가 필요합니다. `set_to_gray`와 `options`는
  예외를 발생시킵니다. 상한 아래의 단락을 참고하세요.
- `insert_pdf`는 원본의 어떤 전체 범위든 받고 가져온 페이지의 폼 필드를
  등록합니다. `select`와 `delete_page(s)`는 제외된 페이지의 widget, 필드, tagged
  structure content를 제거합니다.
- 목차의 named destination은 `get_links()`와 같이 해석됩니다.
- `set_form_field`는 `fontbuffer=`, `fontindex=`, `font_language=`도 받으며, `font`는
  불러온 OpenType `Font`로 그립니다.
- `attachments`는 `add(name, data, /, *, filename, description)`와 `del`을
  지원합니다. 각 `Attachment`에는 `filename`, `description`, `size`,
  `creation_date`, `mod_date`, `read()`, `write(data, /)`가 있습니다.
- `save`는 같은 디렉터리에 stream 쓰기를 완전히 마친 뒤 파일을 원자적으로
  교체합니다. `save`와 `tobytes`는 모두 `garbage=0`–`4`(pymupdf와 같은 단계. 3과 4는
  동일한 객체와 스트림을 병합), `deflate=`, `use_objstms=`, `encryption=`(기본값
  `PDF_ENCRYPT_NONE` 또는 `PDF_ENCRYPT_AES_256`. `PDF_ENCRYPT_AES_256` 없이
  비밀번호를 주면 예외), `user_pw=`, `owner_pw=`를 받습니다.

**상한**

- 비밀번호(`password=`, `authenticate`, `user_pw=`, `owner_pw=`)는 UTF-8 127
  byte에서 멈춥니다.
- `set_metadata`: 텍스트 합계 1 MiB.
- `get_text`, `to_markdown`, `render_pages`, `select`, `delete_page(s)`의 명시적
  `pages` entry: 페이지 수와 4,096 중 큰 값까지. `pages=None`은 모든 페이지를
  대상으로 합니다.
- `to_markdown(max_size=64 MiB)`: 누적 UTF-8 출력.
- `render_pages(max_size=512 MiB)`: 누적 인코딩 PNG 출력.
- `tobytes(max_size=512 MiB)`: 직렬화된 PDF byte.
- `set_fallback_font(max_font_size=64 MiB)`와 `set_form_field(max_font_size=64 MiB)`:
  글꼴 입력. `set_form_field`는 호출자의 이름과 값도 1 MiB에서 멈추고 widget
  appearance를 제한합니다.
- `get_outline` / `set_outline`: 65,536 entry/node, 131,072 edge, 64단계, 텍스트
  16 MiB. 대규모 매뉴얼에 맞춘 크기이며 cycle을 처리합니다. `select`와
  `delete_page(s)`의 navigation 정리, `insert_pdf`의 링크 가져오기는 TOC 및 named
  destination 상한을 공유합니다.
- 페이지 레이블: 4,096 entry/node, 32단계, 레이블 텍스트 1 MiB의 고정 상한.
- 첨부 파일: `add(..., max_size=64 MiB)`, `read(*, max_size=64 MiB)`,
  `write(data, /, *, max_size=64 MiB)`는 입력과 디코딩 출력에 대칭적인 기본 상한을
  적용합니다. 호출자 텍스트는 1 MiB로 제한되고, 메타데이터와 inline FileSpec 복제
  형상도 제한됩니다.
- `get_pdfa_claim(max_size=1 MiB)`: 디코딩된 XMP.
- `rewrite_images`: 호출 한 번에 해석한 간접 raster placement가 65,536개를 넘으면
  거부합니다.
- `max_size=None`과 `max_font_size=None`은 신뢰할 수 있는 입력이나 출력을 해당
  byte 상한에서 제외합니다.

`rewrite_images()`는 모든 페이지를 해석해 각 간접 raster object의 가장 큰 배치를
찾은 뒤 lopdf clone을 원자적으로 편집합니다. `dpi=None`이면 축소하지 않고 quality
재압축만 수행합니다. 보수적인 범위는 mask나 사용자 decode array가 없는 직접, 단일
filter, 8-bit DeviceGray/DeviceRGB DCT 또는 Flate stream입니다. DCT decode
parameter는 제외하며, Flate는 predictor가 없거나 일관된 PNG predictor를 쓸 수
있습니다. 지원하지 않는 해석된 간접 이미지와 더 작아지지 않는 인코딩은 건너뛰며,
inline 이미지는 고려하지 않습니다. 같은 설정으로 반복 호출해도 결과는 같습니다.

## Page { #page }

| 멤버 | 용도 |
|---|---|
| `number`, `parent`, `get_label()` | 식별 정보와 표시 레이블. |
| `xref`, `obj` | 페이지 사전의 객체 번호와 실시간 저수준 핸들. 상속되는 속성은 조상에 남습니다. |
| `get_text(option)` | glyph 수준의 `"rawdict"`를 포함한 plain 또는 위치 정보 추출. [위치 정보가 있는 텍스트](#positioned-text) 참조. |
| `search_for(text, max_hits=4096)` | 결과 사각형을 반환하는 대소문자 구분 없는 검색. |
| `get_text_ocr(dpi=, engine=, rotation=, clip=)` | 페이지를 편집하지 않고 로컬 PP-OCRv6로 위치가 있는 단어를 인식. |
| `apply_ocr(..., rotation=, clip=, skip_existing=True)` | 텍스트를 인식해 방향을 고려한 보이지 않는 검색 가능 레이어를 삽입. 선택 영역에 기존 텍스트가 있으면 기본적으로 페이지를 건너뜁니다. |
| `insert_ocr_text_layer(words, rotation=)` | 주어진 단어로 방향을 고려한 보이지 않는 OCR 레이어를 삽입. |
| `find_tables(clip=None, strategy=None)` | 완전하거나 보수적으로 보완한 벡터 테두리 grid와 병합 셀. `"text"`로 테두리 없는 표 감지를 선택. [표](#tables) 참조. |
| `to_markdown(table_strategy="lines")` | 같은 표 및 출력 제어를 쓰는 단일 페이지 Markdown. |
| `get_images(*, full=False)` | pymupdf와 같은 이미지 XObject 목록을 `ImageItem` NamedTuple로 반환. `ImageItemFull`은 `referencer`를 추가. |
| `get_image_info(*, hashes=False, xrefs=False)` | `bbox`, `transform`, 크기, 색 공간, `xrefs=True`이면 `xref`를 담은 배치 사전. |
| `get_drawings()` | 해석된 벡터 fill/stroke 경로. [벡터 그리기](#vector-drawings) 참조. |
| `get_pixmap(*, matrix=None, dpi=None, clip=None, alpha=False)` | pymupdf와 같은 `Pixmap` 렌더링. 기본은 흰 바탕의 불투명 RGB, `alpha=True`이면 straight-alpha RGBA. |
| `get_svg_image(*, matrix=None, text_as_path=True)` | 텍스트를 경로로 그리는 SVG 렌더링. |
| `rotation`, `set_rotation(deg)` | 표시 회전. |
| `mediabox`, `cropbox`, `rect`, `set_mediabox`, `set_cropbox` | pymupdf 규약의 페이지 박스. |
| `insert_image(rect, filename= / stream= / pixmap=, rotate=)` → xref | JPEG나 PNG를 그리거나 이미 제한된 RGBA `Pixmap`을 재사용. 같은 입력을 다시 배치하면 이미지 XObject 하나를 공유합니다. |
| `show_pdf_page(rect, docsrc, pno=)` → Form xref | PDF 페이지를 벡터로 겹치기. `docsrc`는 같은 문서여도 됩니다. |
| `insert_text(point, text, fontsize=, font=)` | Standard-14 WinAnsi 또는 shaping한 subset 텍스트를 그리고 그린 줄 수를 반환. [텍스트 삽입](#text-insertion) 참조. |
| `insert_textbox(rect, text, fontsize=, font=, align=)` | UAX #14 줄바꿈으로 사각형 안에 텍스트를 배치. 넘치면 아무것도 그리지 않습니다. |
| `new_shape()` | 경로와 텍스트를 한 번에 그리는 pymupdf의 `Shape`. [도형](#shapes) 참조. |
| `draw_*` 메서드 | 마무리한 도형 하나를 그리고 pymupdf와 같은 끝점 `Point`를 반환. [그리기 메서드](#drawing-methods) 참조. |
| `replace_text(search, replacement, default_char=)` | 단순 인코딩 글꼴에서의 원자적 copy-on-write 교체. |
| `annots(*, types=None)` | `PDF_ANNOT_*` 종류와 `PDF_ANNOT_IS_*` 플래그를 쓰는 읽기 전용 `Annot` 핸들(`type`, `rect`, `info`, `colors`, `flags`, `xref`). |
| `get_links()` | pymupdf의 `xref`와 `id`(`/NM`)를 가진 링크 사전. |
| `add_highlight_annot(quads, ...)` | highlight를 추가하고 그 `Annot`을 반환. |
| `insert_link(link, /)` | 링크 사전에서 URI 링크를 생성. |

**세부 사항**

- `get_text_ocr`는 `tile_size=`, `overlap=`, `min_confidence=`도 받습니다.
  `rotation`은 입력을 시계 방향으로 보정하고 `clip`은 `apply_ocr`처럼 표시 좌표를
  씁니다. [OCR](ocr.md)을 참고하세요.
- `find_tables(clip=)`은 표시 좌표 영역입니다.
- `get_pixmap`: `dpi`가 배율 `matrix`보다 우선하고, `clip`은 표시 좌표이며,
  `background=`는 불투명 배경을 지정합니다.
- `mediabox`는 PDF 좌표이고 `cropbox`는 MediaBox 위쪽을 기준으로 y축이
  반전됩니다. `set_cropbox`는 MediaBox 안의 박스만 받고, `set_mediabox`는 CropBox,
  BleedBox, TrimBox, ArtBox를 삭제합니다.
- `insert_image`와 `show_pdf_page`는 `keep_proportion=`과 `overlay=`도 받습니다.
  `rotate`는 90도 단위로 시계 방향 회전합니다. 편집하지 않은 `docsrc`의 같은
  페이지를 다시 배치하면 Form XObject를 공유합니다.
- `insert_text`와 `insert_textbox`는 `lineheight=`, `fontname=`, `fontfile=`,
  `fontbuffer=`, `fontindex=`, `font_language=`, `color=`, `overlay=`도 받고,
  `insert_textbox`는 `expandtabs=`도 받습니다. `font`는 불러온 `Font`로 그리며,
  설치된 script/locale provider는 자동으로 선택되고, 모호한 한자는
  `font_language`로 정합니다.
- `get_links()`의 named destination은 한 번만 index로 만들고 다음 편집까지 이후
  읽기에서 재사용합니다.
- 렌더링은 유효한 `QuadPoints`를 가진 상한 내 RGB Highlight, Underline, StrikeOut,
  Squiggly 사전의 누락된 appearance를 원본 PDF를 바꾸지 않고 보수적으로
  보완합니다.

**상한**

- `search_for`: 검색어는 UTF-8 4,096 byte, 결과는 `max_hits=4096`에서 멈춥니다.
  `None`은 신뢰할 수 있는 결과 집합을 상한에서 제외합니다.
- `get_drawings`: 8,192 경로, 131,072 명령, 또는 누적 131,072 dash 값을 넘는 출력은
  부분적으로 반환하지 않고 거부합니다.
- `get_svg_image(max_size=64 MiB)`: UTF-8 출력.
- `insert_image(max_size=64 MiB, max_pixels=64,000,000)`: 인코딩된 입력과 디코딩된
  PNG 픽셀.
- `insert_text`와 `insert_textbox`: `max_font_size=64 MiB`, UTF-8
  `max_text_size=1 MiB`, 4,096줄. `insert_textbox`는 물리 줄과 줄바꿈 후 layout
  모두 4,096줄에서 멈추며, tab 확장은 사전 검사합니다.
- `insert_ocr_text_layer`: 호출당 4,096단어와 UTF-8 텍스트 1 MiB의 고정 상한.
- `replace_text(max_size=64 MiB)`: 입력과 출력을 제한합니다.
- `get_links()`의 named destination: 최대 65,536 entry/node, 131,072 edge,
  32단계, key 16 MiB의 name tree.
- 이 `max_*` 인수 중 어느 것이든 `None`으로 주면 해당하는 신뢰할 수 있는 입력을
  상한에서 제외합니다.

### 위치 정보가 있는 텍스트와 glyph { #positioned-text }

`Page.get_text("rawdict")`와 `Document.get_page_text(pno, "rawdict")`는 glyph 수준의
텍스트를 반환합니다. 결과는 `"dict"` 계층을 유지합니다. 페이지의 `width`/`height`,
텍스트 block(`number`, `type=0`, `bbox`, `lines`), line(`bbox`, `wmode`, `dir`,
`spans`), span 메타데이터(`bbox`, `origin`, `size`, `font`, `flags`)가 있으며, 각
span은 `text` 대신 `chars` 목록을 가집니다.

| `TextChar` 키 | 의미 |
|---|---|
| `c` | 해석된 PDF glyph 하나의 완전한 Unicode 매핑. 여러 Unicode 문자를 담을 수 있음 |
| `bbox` | 기존 glyph geometry를 사용한 근사적인 축 정렬 경계 `(x0, y0, x1, y1)` |
| `origin` | glyph 기준선의 시작점 `(x, y)` |

```python
import pylopdf

with pylopdf.open("report.pdf") as doc:
    layout = doc[0].get_text("rawdict")
    for block in layout["blocks"]:
        for line in block["lines"]:
            for span in line["spans"]:
                for char in span["chars"]:
                    print(char["c"], char["bbox"], char["origin"])
```

위치는 렌더링과 같은 표시 포인트를 씁니다. 원점은 왼쪽 위이고, x는 오른쪽, y는
아래쪽으로 증가하며, 페이지 회전과 CropBox offset이 적용됩니다. 기본값
`/UserUnit=1`에서 1포인트는 1/72인치이며, 현재 엔진은 기본값이 아닌 `/UserUnit`에
대해 물리적 배율을 적용하지 않습니다. 경계는 기준선과 글꼴 크기에서 구한
근사값이며, 정확한 잉크 윤곽이나 사변형이 아닙니다. 글꼴 이름과 스타일 플래그는
기존 `"dict"`의 해석을 그대로 따릅니다.

항목 하나는 매핑된 glyph 하나를 나타내며, 반드시 Unicode 코드 포인트나 grapheme
하나에 대응하지는 않습니다. 합자 등의 매핑은 `c` 안에 그대로 유지되며,
geometry로 추론한 공백이나 줄바꿈을 위한 항목을 새로 만들지 않습니다. 매핑된
glyph에 명시적으로 있는 공백은 남습니다. 따라서 `c` 값을 이어 붙여도
`get_text("text")`의 공백과 같아지지 않을 수 있습니다. 매핑이 없는 glyph는 다른
추출 모드와 같이 중복 제거된 `PylopdfWarning`과 함께 건너뜁니다.

읽기 순서는 기존의 줄, 단, 보수적인 RTL/CJK heuristic을 따릅니다. 세로쓰기 CJK
모드는 glyph 배치로 추론하며, 루비, 와리추, 일반적인 혼합 방향 단락, 혼합 방향
조판은 근사적으로 처리합니다. 임의 각도 기준선의 읽기 순서는 원본이나 의미상의
순서와 다를 수 있으므로, 애플리케이션에 고유한 순서가 필요하면 glyph `origin`과
line `dir`를 사용하세요. `rawdict`는 기존의 보이지 않는 텍스트 레이어를 포함해
추출된 PDF 텍스트를 보고하며, OCR을 실행하지는 않습니다.

계층 구조는
[PyMuPDF rawdict](https://pymupdf.readthedocs.io/en/latest/textpage.html#structure-of-dictionary-outputs)와
비슷하지만 완전한 호환 계약은 아닙니다. pylopdf는 텍스트 block만 반환하고, 여러
문자로 된 glyph 매핑을 항목 하나에 보존하며, PyMuPDF의 추가 이미지/벡터, 색,
불투명도, 글꼴 메트릭, 합성 문자 필드 대신 기존 span 메타데이터를 제공합니다.
이미지나 벡터 추출에는 `get_image_info()`와 `extract_image()`, 또는 `get_drawings()`를
사용하세요.
[overlay 예제](https://github.com/yhay81/pylopdf/blob/main/examples/rawdict-overlay.py)는
glyph 경계와 기준선 origin을 보여 주는 독립형 HTML 미리보기를 만듭니다.

### 벡터 그리기 { #vector-drawings }

`get_drawings()`는 `type="f"`, `"s"`, `"fs"`, 자체 완결형 line/cubic `items`, `rect`,
RGB/불투명도, fill rule, width, cap, join, dashes, 그리고 페이지의 경로·텍스트
run·이미지 가운데 그리기 작업의 위치인 `seqno`를 담은 `DrawingInfo` 사전을
반환합니다. geometry는 표시 공간이며, 칠하기와 선 속성은 정규화됩니다. pattern
paint는 geometry를 유지하고 색과 불투명도는 `None`으로 둡니다. clip 경로, clip을
적용한 가시성, group과 soft-mask 구조, optional-content 레이어 이름, 텍스트, 이미지,
주석은 반환하지 않지만 optional-content의 표시 상태는 적용합니다. 결과가 8,192
경로 또는 131,072 명령을 넘으면 잘라내지 않고 거부합니다.

### 텍스트 삽입 { #text-insertion }

글꼴을 임베드하는 `insert_text`에는 모든 glyph를 포함한 글꼴 하나가 필요합니다.
`pylopdf[ar]`, `[he]`, `[hi]`, `[jp]`, `[ko]`, `[th]`, `[zh-cn]`, `[zh-tw]`는 Noto
Sans/Serif 생성 글꼴을 제공합니다. 가나, 한글, 주음부호, 아랍 문자, 히브리 문자,
데바나가리, 태국 문자는 설치된 provider를 자동으로 선택합니다. 순수 한자는 JP 우선
기본값을 유지하므로, 지역이 중요하면 `font_language="zh-CN"`, `"zh-TW"`, `"ko"`,
`"ja"`를 전달하세요. 이는 run 전체에 글꼴 하나를 쓰는 방식이며 glyph별 fallback이
아닙니다. 각 줄은 shaping하지만 양방향 단락 layout과 줄바꿈은 이 기본 기능의 범위
밖입니다. RTL shaping은 올바르게 렌더링됩니다. 순수 RTL 줄과, RTL 텍스트 사이에
Latin 또는 숫자 token이 하나만 끼어 있는 줄은 논리적 Unicode 순서로 추출됩니다. 줄
가장자리의 token, 여러 token, 일반적인 혼합 방향 단락은 producer의 시각적 순서를
유지합니다.

복잡한 shaping에서는 glyph와 일대일로 대응하지 않는 논리 문자열을 보존하기 위해 PDF
`/ActualText`가 필요할 수 있습니다. 생성된 PDF는 외부 뷰어를 위해 그 데이터를
포함하지만, pylopdf의 현재 추출기는 `/ActualText`를 사용하지 않습니다. 그래서 순서가
바뀌거나 문맥에 따라 달라지는 일부 cluster, 특히 긴 데바나가리 run은 다시 열었을 때
근사적으로 추출될 수 있습니다.

`insert_text`는 문자열을 `str.splitlines()`로 나누고, list는 항목 하나를 한 줄로
씁니다. 페이지 아래로 벗어나는 줄은 빼고 그린 줄 수를 반환합니다.

`insert_textbox`는 리치 텍스트 엔진이 되지 않으면서 줄바꿈을 더합니다. 명시적
줄바꿈을 유지하고, tab을 확장하고, CJK를 Unicode 줄바꿈 기회에서 나누며, 너무 긴
단어에는 grapheme 안전 긴급 줄바꿈을 씁니다. 정렬 상수는 `TEXT_ALIGN_LEFT`,
`TEXT_ALIGN_CENTER`, `TEXT_ALIGN_RIGHT`, `TEXT_ALIGN_JUSTIFY`입니다. 반환값이
음수이면 세로 공간의 부족분이며, 이 경우 페이지 content나 글꼴 리소스를 추가하지
않습니다.

두 메서드 모두 pymupdf와 같은 위치에 줄을 배치합니다. 기본적으로 줄 간격은
`fontsize`의 `lineheight`배이며, `lineheight`의 기본값은 글꼴의 `ascender`에서
`descender`를 뺀 값입니다.

??? note "줄 배치 세부 사항"

    `insert_text`는 각 줄을 앞 줄보다 `fontsize`의 `lineheight`배만큼 아래에서
    시작합니다. `lineheight`가 없으면 배율은 글꼴의 `ascender`에서 `descender`를
    뺀 값이고, 그 차가 1 이하이면 1.2이므로 10포인트 Helvetica의 줄 간격은
    13.74포인트입니다. pymupdf와 같이 이 차는 MuPDF가 보고하는 단정밀도
    메트릭으로 비교하므로, ascender 0.8과 descender -0.2는 1 em을 약간 넘어 줄
    간격이 1 em으로 유지됩니다.

    `insert_textbox`도 같은 기본값을 쓰며, 첫 기준선을 `rect` 위쪽에서 ascender
    하나만큼 아래에 두고 이후 줄은 ascender의 `lineheight`배씩 아래에 둡니다.
    텍스트가 들어가는지 판단하고 남은 높이를 보고할 때는 줄마다 `fontsize`의
    `lineheight`배와 descender 하나를 셉니다.

    `insert_text`는 어떤 줄을 그릴지도 pymupdf와 같이 정합니다. 문자열은
    `str.splitlines()`로 나누므로 끝의 줄바꿈은 줄을 늘리지 않으며, `\v`, `\f`,
    `\x1c`~`\x1e`, `\x85`, `\u2028`, `\u2029`도 `\n`, `\r`, `\r\n`과 같이 줄을
    나눕니다. list나 tuple은 항목 하나를 한 줄로 그대로 씁니다. 첫 줄은 항상
    그리고, 이후 줄은 기준선이 `page.rect`의 아래쪽보다 아래로 내려가지 않는
    동안만 그립니다. 반환값은 그린 줄 수이며, `""`나 `"\n"`처럼 그릴 것이 없는
    텍스트는 아무것도 삽입하지 않고 0을 반환합니다. pymupdf는 회전 전 공간에서
    사용자 공간 y = 0까지 남은 공간을 재고, pylopdf는 표시 좌표에서 `page.rect`의
    아래쪽까지 잽니다. 둘은 회전하지 않았고 보이는 영역이 y = 0에서 끝나는
    페이지에서 일치합니다. 글꼴은 페이지 아래의 줄을 포함해 모든 줄을 여전히
    검사합니다.

### 글꼴 { #fonts }

#### 글꼴 불러오기

`Font`는 pymupdf의 `Font(fontname=None, fontfile=None, fontbuffer=None)`을 따르며
`insert_text(font=)`와 `insert_textbox(font=)`에 전달하고, OpenType 글꼴은
`set_form_field(font=)`에도 전달할 수 있습니다.

```python
helvetica = pylopdf.Font("helv")
noto = pylopdf.Font(fontfile="NotoSansJP-Regular.otf")
page.insert_text((72, 72), "Hello", font=noto, fontsize=12)
```

`Font("helv")`나 `Font("Times-Bold")`는 Standard 14 글꼴을 가리키며, 임베드하지 않고
그리고 Adobe AFM 폭으로 측정합니다. pymupdf의 다른 내장 글꼴은 쓸 수 없습니다.
`Font(fontfile=)`와 `Font(fontbuffer=)`는 OpenType 글꼴을 그대로 불러옵니다.
`Font(language="ja")`나 `Font(family="Noto Sans JP")`는 설치된 글꼴 extra의 글꼴을
선택하며, `is_serif=True`이면 세리프 글꼴을 고릅니다.

`font.text_length(text, fontsize)`는 그리기와 같은 shaping으로 커닝과 합자를 포함해
측정하므로, layout 코드는 그리는 글꼴로 줄바꿈, 정렬, 페이지 나누기를 계산할 수
있습니다. `has_glyph(chr)`는 코드 포인트의 glyph 번호를, 없으면 0을 반환합니다.

pylopdf는 글꼴 등록표를 두지 않고 시스템 글꼴 디렉터리도 찾지 않습니다. 그리기
호출마다 그 호출이 쓰는 glyph의 subset을 임베드하므로, 글꼴을 확정 한 번에 한
번만 임베드하려면 [도형](#shapes)으로 그리세요.

#### 굵기와 가변 글꼴

`Font(fontfile=)`와 `Font(fontbuffer=)`는 가변 글꼴을 기본 인스턴스로 불러오며, 기본
인스턴스가 Regular라는 보장은 없습니다. 가변 글꼴인 Noto Sans CJK JP의 기본값은
Thin입니다. `weight=`는 가변 글꼴의 굵기 축에서 다른 인스턴스를 선택합니다.

```python
bold = pylopdf.Font(fontfile="NotoSansCJKjp-VF.otf", weight=700)
page.insert_text((72, 100), "Summary", font=bold, fontsize=18)
```

`Font(fontfile="NotoSansCJKjp-VF.otf", weight=700)`는 Bold 인스턴스의 advance로
측정하고 `NotoSansCJKjp-Bold`로 임베드합니다. `weight=650`도 쓸 수 있으므로 이런
글꼴에서는 `weight=400`과 굵기를 생략한 경우가 다릅니다. 축 범위를 벗어난 굵기는
`PdfError`를 발생시키며, 정적 글꼴이 선언하지 않은 굵기도 마찬가지입니다.
`insert_text`, `insert_textbox`, `set_form_field`의 `fontfile`과 `fontbuffer` 인수는
기본 인스턴스로 그립니다. 선택한 인스턴스로 그리려면 그 `Font`를 `font=`로
전달하세요.

CJK extra는 스타일마다 정적 글꼴 하나를 설치하므로, 글꼴과 맞지 않는 `is_bold`,
`is_italic`, 정확한 OS/2 `weight` 지정은 `PdfError`를 발생시킵니다. Noto Sans JP
Bold 같은 다른 굵기는 파일에서 불러오세요. 반면 `pylopdf[ar]`, `[he]`, `[hi]`,
`[th]` extra는 가변 글꼴을 설치합니다. `Font(language="he", is_bold=True)`는 다른
파일 없이 글꼴의 굵기 축에서 Bold 인스턴스를 선택하고, 그 인스턴스의 advance로
측정하며, 자체 PostScript 이름인 `NotoSansHebrew-Bold`로 임베드합니다. 굵기 범위는
100부터 900까지이고, `pylopdf[ar]`의 세리프 글꼴인 Noto Naskh Arabic은 400부터
700까지입니다. 축 밖의 굵기나 기울임 지정은 `PdfError`를 발생시킵니다.

설치된 가변 글꼴은 `insert_text`, `insert_textbox`, `set_form_field`가 자동으로
선택하는 경우에도 Regular 굵기로 그립니다. Noto Sans Hebrew의 기본 인스턴스가
Thin이기 때문입니다. pylopdf는 glyph의 윤곽선을 그리거나 덧칠해서 굵기를 흉내 내지
않습니다. `allow_regular_fallback=True`는 extra의 글꼴을 `PylopdfWarning`과 함께
명시적으로 받아들입니다. `is_bold`, `is_italic`, `is_serif`를 `fontname`이나 글꼴
파일과 함께 쓰면, pymupdf라면 무시하는 경우에도 예외가 발생합니다.

#### 줄 메트릭

`ascender`와 `descender`는 pymupdf가 보고하는 em 대비 비율이며, `insert_text`와
`insert_textbox`는 이 값으로 줄을 배치합니다. OpenType 글꼴에서는 글꼴이
`USE_TYPO_METRICS`를 설정했으면 OS/2의 typographic 메트릭을, 그렇지 않으면 `hhea`
메트릭을 쓰며, FreeType이 MuPDF를 위해 읽는 값과 같습니다. Standard 14 글꼴에서는
MuPDF가 그릴 때 쓰는 URW 글꼴의 외접 사각형 값이며, Helvetica는 1.075와
-0.299입니다.

### 도형 { #shapes }

`page.new_shape()`는 pymupdf의 `Shape`를 반환합니다. `draw_line`, `draw_polyline`,
`draw_rect`(`radius=`로 모서리를 둥글게), `draw_quad`, `draw_circle`, `draw_oval`,
`draw_sector`, `draw_curve`, `draw_bezier`, `draw_squiggle`, `draw_zigzag`는 표시
좌표로 경로 구간을 더하고 pymupdf와 같은 끝점을 반환합니다. 앞 구간이 끝난
곳에서 시작하는 구간은 같은 하위 경로를 이어 갑니다. `finish(*, width=1,
color=(0,), fill=None, lineCap=0, lineJoin=0, dashes=None, even_odd=False,
closePath=True, fill_opacity=1, stroke_opacity=1)`는 앞의 `finish` 이후 그린 모든
것을 선과 채우기로 마무리합니다. 색은 0부터 1까지의 회색, RGB, CMYK이고 점선은
`"[3 4] 0"` 같은 PDF dash pattern입니다. `insert_text`와 `insert_textbox`는
`Page.insert_text`와 `Page.insert_textbox`의 글꼴 옵션을(`font=`와 `lineheight=`
포함) 받고 그 메서드와 같이 줄을 배치합니다. `insert_text`는 `Page.insert_text`와
같이 텍스트를 나누고 페이지 아래의 줄을 빼며 더한 줄 수를 반환합니다.
`insert_textbox`는 남은 높이를 반환하며, 텍스트가 들어가지 않으면 음수를 반환하고
아무것도 더하지 않습니다. `commit(overlay=True)`는 도형을 그립니다. pymupdf와
같이 마무리한 경로를 순서대로 그리고 그 위에 모든 텍스트를 그립니다.

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

확정 한 번에 Form XObject 하나를 배치합니다. 경로와 임베드 글꼴의 텍스트를 함께
생성하므로 몇 줄이 쓰든 글꼴은 확정 한 번에 한 번만 임베드됩니다. 한 페이지의
텍스트를 도형 하나로 그리면 글꼴은 페이지마다 한 번만 임베드됩니다. `with` 블록은
정상적으로 끝나면 확정하고, 예외가 나면 아무것도 그리지 않으며, 확정하지 않은
그리기를 가진 채 버려진 도형은 `ResourceWarning`을 냅니다. pymupdf가 그리기를
조용히 버리거나 검정으로 그리는 경우 pylopdf는 `PdfError`를 발생시킵니다.
`commit()`은 `finish()`로 스타일을 지정하지 않은 그리기를 거부하고, `finish()`는
아무것도 그리지 않는 스타일(채우기가 없고 `color=None` 또는 `width=0`)을
거부합니다. `morph=`와 `oc=`는 쓸 수 없습니다. 도형은 확정 전까지 경로 점을 최대
1,048,576개 가질 수 있으며, 넘으면 code가 `path_point_count`인 `LimitError`가
발생합니다.

#### 그리기 메서드 { #drawing-methods }

`Page.draw_*` 메서드는 같은 옵션을 키워드 인수로 받아 마무리한 도형을 하나씩
그리고, pymupdf와 같은 끝점 `Point`를 반환합니다.

- 메서드: `draw_line`, `draw_polyline`, `draw_rect`, `draw_quad`, `draw_circle`,
  `draw_oval`, `draw_sector`, `draw_curve`, `draw_bezier`, `draw_squiggle`,
  `draw_zigzag`. 각각 geometry를 첫 인수로 받습니다.
- 공통 키워드: `color=(0,)`, `fill=None`, `width=1`, `dashes=None`, `lineCap=0`,
  `lineJoin=0`, `closePath=`, `overlay=True`, `stroke_opacity=1`, `fill_opacity=1`.
  `fill`과 `closePath`는 pymupdf가 제공하는 메서드에만 있습니다.
- 메서드별 키워드: `draw_rect`의 `radius=`, `draw_sector`의 `fullSector=`, 물결선
  (`draw_squiggle`, `draw_zigzag`)의 `breadth`. `breadth`는 pymupdf와 같이 점 뒤에
  위치 인수로도 전달할 수 있습니다.

### 폼 채우기 { #form-filling }

`set_form_field`는 텍스트, 콤보/목록 선택, checkbox, radio widget의 appearance를
생성합니다. WinAnsi 텍스트는 Helvetica로 자동 맞춤하며, 서브셋 임베드 Unicode에는
OpenType `fontfile`이나 `fontbuffer`를, 또는 가변 글꼴의 Bold 인스턴스 같은 불러온
OpenType `Font`를 `font`로 전달하세요. `Font`는 `text_length`가 측정하는 advance로
맞춤, 정렬, comb 위치의 가운데 배치를 계산하며, 다른 글꼴 인수와 함께 쓸 수
없습니다. Standard 14 `Font`는 `PdfError`를 발생시킵니다. 설치된 locale/script
provider는 `insert_text`와 같은 규칙으로 선택되고, 모호한 한자는 `font_language`로
정합니다.

비어 있지 않은 기존 checkbox/radio appearance는 보존하고, 누락된 상태에는 벡터
표시를 만듭니다. 다른 WinAnsi 필드의 누락된 appearance도 함께 채우며,
`NeedAppearances`는 입력 가능한 모든 widget이 자체 완결일 때만 해제합니다. comb
텍스트 필드는 상속된 `MaxLen`과 정렬을 따르고, 각 Unicode grapheme을 해당 위치
가운데에 배치하며, 너무 긴 값은 원자적으로 거부합니다. 리치 텍스트, pushbutton
action, 서명은 생성하지 않습니다.

### 표 { #tables }

`Table.confidence`는 0–1의 결정적 순위 heuristic이며 보정된 확률이 아닙니다.
`Table.diagnostics`는 strategy와, 테두리 없는 텍스트 표에서는 em으로 정규화한 정렬
오차, 최소 gutter, 행 간격 변화를 담은 `TableDiagnostics` tuple입니다. 완전한 벡터
grid는 1.0, 희소한 규칙을 보완한 hybrid grid는 0.95이며, 둘 다 텍스트 전용 지표는
`None`입니다. `TableFinder.strategy`와 `TableFinder.clip`에는 사용한 설정이
남습니다.

## 저수준 객체 { #low-level-objects }

`doc.objects`는 객체 번호를 문서의 간접 객체에 대응시키고, `doc.trailer`,
`doc.catalog`, `page.obj`는 사전 핸들입니다. 이 타입 있는 모델은 pymupdf의 문자열
기반 `xref_*` 메서드를 대체합니다. 객체는 PDF 구문이 아니라 Python 값입니다.

```python
from pylopdf import Name

catalog = doc.catalog
catalog["PageLayout"] = Name("TwoColumnLeft")   # 이름. 일반 str은 텍스트 문자열이 됨
font = doc.objects.add({"Type": Name("Font"), "Subtype": Name("Type1"), "BaseFont": Name("Helvetica")})
content = doc.objects.add_stream(b"BT /F9 24 Tf 72 72 Td (Hi) Tj ET")
page.obj["Resources"] = {"Font": {"F9": doc.objects[font]}}   # 간접 핸들은 참조로 저장됨
page.obj["Contents"] = doc.objects[content]
doc.objects[content].read()                    # 디코딩됨, 기본 상한 64 MiB
```

| 값 | Python |
|---|---|
| null, 불리언, 정수, 실수 | `None`, `bool`, `int`, `float` |
| 이름 | `Name`, 슬래시를 뺀 `str` 서브클래스 |
| 문자열 | `String`, `.text`로 PDF 텍스트 문자열을 디코딩하는 `bytes` 서브클래스 |
| 배열 / 사전 / 스트림 | 실시간 `Array`(가변 시퀀스) / `Dictionary`(가변 매핑) / `Stream`(`read(*, decode=True, max_size=64 MiB)`와 `write(data, *, filter="FlateDecode")`를 가진 사전) |

참조는 읽을 때 해석되며, 없는 객체에 대한 참조는 `None`으로 읽힙니다. 쓰기는 즉시
반영되고 렌더링과 추출 cache를 버립니다. 페이지 순서를 바꾸는 쓰기는 기존 `Page`
뷰도 무효화합니다. 리스트와 매핑은 직접 객체로 쓰이고, 컨테이너에 저장한 간접
핸들은 참조가 되며, `doc.objects[xref]`에 대입하거나 `add()`에 넘긴 핸들은
복사됩니다. 객체가 제거되거나 `save(garbage=2)` 이상이 객체 번호를 다시 매기면
핸들은 `StaleObjectError`를 발생시킵니다. 원시 편집을 의미적으로 올바르게 유지하는
것은 호출자의 책임입니다.

## 모듈 수준 이름 { #module-level }

| 이름 | 용도 |
|---|---|
| `peek_metadata(filename=None, stream=None, password=None, *, max_file_size=None)` | 빠른 메타데이터·페이지 수 조회. `repaired`는 cross-reference 복구를 보고합니다. |
| `Permissions` | 멤버가 pymupdf의 `PDF_PERM_*` 상수인 암호화 권한 플래그(IntFlag). |
| `EncryptionMethod` | 멤버가 pymupdf의 `PDF_ENCRYPT_*` 상수인 `save(encryption=)` 방식(IntEnum). |
| `Base14_fontnames`, `Base14_fontdict` | pymupdf의 Standard 14 PostScript 이름, 그리고 `fontname=`이 쓰는 읽기 전용 대응표. 약칭과 정식 이름을 대소문자 구분 없이 받습니다. |
| `LinkKind`, `TextAlign` | 멤버가 pymupdf의 `LINK_*`와 `TEXT_ALIGN_*` 상수인 IntEnum. |
| `Rect`, `IRect` | 포함, 합집합, 교집합, 변환, 반올림을 지원하는 불변 `(x0, y0, x1, y1)` NamedTuple. |
| `Quad` | 변환, 포함, 형태 판정을 지원하는, `Point` 네 개로 된 불변 `(ul, ur, ll, lr)` NamedTuple. |
| `Point` | 벡터 연산, 아핀 변환, 거리, 방향을 지원하는 불변 `(x, y)` NamedTuple. |
| `Matrix`, `IdentityMatrix`, `Identity` | 합성과 역행렬을 지원하는 불변 아핀 `(a, b, c, d, e, f)` NamedTuple과 항등 행렬. |
| `EMPTY_RECT()`, `INFINITE_RECT()`와 그 `IRECT` / `QUAD` 형식, `FZ_MIN_INF_RECT`, `FZ_MAX_INF_RECT`, `planish_line(p1, p2)` | pymupdf의 geometry 상수와 선을 수평으로 펴는 행렬. |
| `TextPageDict`, `TextBlock`, `TextLine`, `TextSpan` | `get_text("dict")`의 TypedDict 계층. |
| `RawTextPageDict`, `RawTextBlock`, `RawTextLine`, `RawTextSpan`, `TextChar` | glyph 매핑, 경계, 기준선 origin을 담은 `get_text("rawdict")`의 TypedDict 계층. |
| `ImageInfo`, `ImageCompressionResult`, `DrawingInfo`, `AnnotationInfo`, `LinkInfo`, `FormFieldInfo` | 페이지, 문서 작업, 폼의 사전 형식 결과를 위한 TypedDict 계약. |
| `PageLabelInfo`, `PageLabelSpec` | 정규화된 페이지 레이블 출력과 setter 입력 계약. |
| `DocumentMetadata`, `MetadataUpdate`, `MetadataProbe` | 메타데이터 출력, 부분 업데이트, 빠른 probe 계약. |
| `DocumentLimits`, `DocumentComplexity` | 신뢰할 수 없는 입력을 위한 불변 예산, 그리고 저비용 구조 TypedDict. |
| `OcrEngine`, `OcrWord` | 재사용 가능한 순수 Rust PP-OCR 엔진과 위치 결과 계약. |
| `Font(fontname=None, fontfile=None, fontbuffer=None, *, language=None, weight=None)` | 재사용 가능한 Standard 14 또는 OpenType 글꼴. [글꼴](#fonts) 참조. |
| `Shape(page)` | 경로와 텍스트를 한 번에 그리는 pymupdf의 builder. `page.new_shape()`로 만듭니다. |
| `Name`, `String`, `Array`, `Dictionary`, `Stream`, `Objects` | 저수준 PDF 값과 실시간 객체 핸들([저수준 객체](#low-level-objects)). |
| `Word`, `Block` | pymupdf의 tuple 순서를 따르는 `get_text("words")`와 `get_text("blocks")` NamedTuple. |
| `OcrRotation`, `DrawingItem`, `FormFieldType` | runtime에서 import 가능한 OCR 회전, 벡터 명령, literal 타입 별칭. |
| `TableFinder`, `Table`, `TableDiagnostics` | 독립적으로 보관되는 표 geometry, 셀 텍스트(병합된 연속 위치는 `None`), strategy와 confidence 근거. [표](#tables) 참조. |
| `PdfError`, `LimitError`, `PasswordError`, `FileDataError`, `EmptyFileError`, `OcrError`, `DocumentClosedError`, `EncryptedDocumentError`, `StalePageError`, `StaleObjectError` | ValueError 호환 기반을 가진 예외 계층. |
| `Pixmap` | 불변 RGB(`n == 3`) 또는 straight-alpha RGBA(`n == 4`) 픽셀. |
| `Colorspace`, `csRGB`, `csGRAY`, `csCMYK`, `CS_RGB`, `CS_GRAY`, `CS_CMYK` | pymupdf의 장치 색 공간과 그 코드. |
| `PylopdfWarning` | 복구 가능한 해석 경고(xref 복구, 글꼴 해석, 이미지 디코딩). |

**세부 사항**

- `peek_metadata`는 `max_file_size`를 설정했을 때 그보다 큰 입력을 거부하며,
  비밀번호는 UTF-8 127 byte에서 멈춥니다.
- `DocumentLimits`에는 PDF snapshot용 `max_interpretation_size`와 위치 layout용
  `max_text_glyphs`가 있습니다.
- `OcrEngine.recognize(pixmap, *, tile_size=, overlap=, min_confidence=, rotation=)`는
  어떤 `Pixmap`이든 픽셀 좌표로 읽습니다.
- `Font`는 `is_bold=False`, `is_italic=False`, `is_serif=False`, `fontindex=0`,
  `family=None`, `allow_regular_fallback=False`, `max_font_size=64 MiB`도 받습니다.
  `text_length`, `ascender` / `descender`, `has_glyph`, `name`, `family`, `weight`,
  `is_bold`, `is_italic`, `is_serif`, `units_per_em`을 제공하며, `weight=`는 가변
  글꼴 파일의 인스턴스도 선택합니다.
- `Table.to_markdown(max_size=64 MiB)`는 escape 후의 UTF-8 출력을 사전 검사합니다.
- 상한 실패는 안정적인 `.code`를 제공합니다. 읽을 수 없는 입력은
  `FileDataError`(`RuntimeError`이기도 함), 빈 입력은 `EmptyFileError`, 없는 파일은
  내장 `FileNotFoundError`를 발생시킵니다.
- `Pixmap`은 `samples`, `width`, `height`, `stride`, `n`, `alpha`, `colorspace`,
  `tobytes(max_size=64 MiB)`, 그리고 스트리밍하며 실패해도 원자적인 PNG 전용
  `save(filename)`을 제공합니다. 모든 wheel에서 읽기 전용 zero-copy `memoryview()`도
  지원합니다.

`TypedDict` 계약은 정적 타입에만 영향을 주며 값은 일반적인 pymupdf 형식의
사전입니다. `LinkInfo`에는 `kind`와 `from`이 필수이고 대상별 키는 선택 사항입니다.
`PageLabelSpec`에는 `startpage`가 필요하며 `style`, `prefix`, `firstpagenum`은
runtime 기본값을 유지합니다.

## Point와 Matrix geometry { #geometry }

`Point()`는 원점을 만들고, `Point(x, y)`와 `Point((x, y))`는 위치를 만듭니다.
`Matrix()`는 영행렬을 만들고, `Matrix(sx, sy)`는 배율, `Matrix(degrees)`는 회전,
`Matrix(sx, sy, 1)`은 기울이기입니다. 명시적인 변환에는 좌표 여섯 개 또는 항목
여섯 개짜리 시퀀스 하나를 전달하세요. 좌표 키워드 인수는 모든 좌표를 지정할 때
지원됩니다. 두 클래스는 불변 NamedTuple이므로 인덱싱, 언패킹, tuple 비교, 해시,
pickle은 tuple처럼 동작하고, 산술 연산은 geometry 값을 반환합니다.

```python
from pylopdf import Matrix, Point

scale = Matrix(2, 3)
shift = Matrix(1, 0, 0, 1, 20, 30)
transform = scale * shift
position = Point(10, 20) * transform  # Point(x=40.0, y=90.0)
original = position / transform      # Point(x=10.0, y=20.0)
page.insert_text(position, "Transformed position")
```

곱셈은 왼쪽에서 오른쪽으로 변환을 합성합니다. 점 `(x, y)`에
`(a, b, c, d, e, f)`를 곱하면 `(a*x + c*y + e, b*x + d*y + f)`가 됩니다. 표시
공간의 y축은 아래를 향하므로 `Matrix(90)`은 `(1, 0)`을 `(0, 1)`로 보냅니다.
`~matrix`는 역행렬을, 특이 행렬이면 영행렬을 반환합니다. `point / matrix`와
`matrix / other`는 나누는 행렬이 특이 행렬이면 `ZeroDivisionError`를 발생시킵니다.
유한한 변환이라도 결과를 표현할 수 없으면 `PdfError`를 발생시킵니다.

| 연산 | 결과 |
|---|---|
| `point + other`, `point - other` | 점, 항목 두 개짜리 시퀀스, 스칼라와의 성분별 연산 |
| `matrix + other`, `matrix - other` | 행렬, 항목 여섯 개짜리 시퀀스, 스칼라와의 성분별 연산 |
| `value * scalar`, `value / scalar`, `-value`, `+value` | 새 값. 단항 `+`는 같은 불변 값 |
| `abs(value)`, `value.norm()` | 좌표의 유클리드 길이 |
| `bool(value)` | 모든 좌표가 0일 때만 거짓 |
| `point.unit`, `point.abs_unit` | 단위 방향(선택적으로 성분을 음이 아닌 값으로). 원점은 0으로 유지 |
| `point.distance_to(other, unit="px")` | 점, 또는 정규화된 사각형의 가장 가까운 위치까지의 거리. 단위는 `px`(PDF 포인트), `in`, `cm`, `mm` |
| `matrix.is_rectilinear` | 축이 정렬된 채로 유지되는지 여부. 0 허용 오차는 `1e-5` |

좌표는 유한한 수여야 합니다. 좌표 시퀀스 길이가 틀리거나 유한하지 않은 값은
`PdfError`를, 잘못된 인수 타입은 `TypeError`를 발생시킵니다. 입력은 임의의 iterable이
아니라 길이가 제한된 시퀀스입니다. `transform`, `invert`, `pretranslate` 같은 제자리
변경 메서드는 없으므로 연산자의 결과를 대입하세요. 덧셈과 스칼라 곱셈에서는
geometry를 왼쪽에 둡니다.

이들은 값 연산이므로, 변환한 점을 그리기에 전달하면 위치가 바뀔 뿐 텍스트의 방향이나
크기는 바뀌지 않습니다. `get_pixmap(matrix=)`는 배율 행렬만 받으며 회전과 기울이기는
지원하지 않습니다.

## 사각형과 사변형 { #rectangles }

`Rect()`는 영 사각형입니다. `Rect(x0, y0, x1, y1)`, `Rect((x0, y0, x1, y1))`,
`Rect(top_left, bottom_right)`, `Rect(top_left, x1, y1)`, `Rect(x0, y0, bottom_right)`는
좌표나 모서리 점을 받습니다. `IRect`는 같은 인수를 받고 소수 가장자리를 바깥쪽으로
반올림합니다. `x0`과 `y0`은 내림, `x1`과 `y1`은 올림이며 산술 연산 뒤에도
마찬가지입니다. `Quad(ul, ur, ll, lr)`는 점 네 개를 받고, `rect.quad`는 사각형을
사변형으로 바꿉니다. 사각형을 반환하는 모든 페이지 API는 `Rect`를 반환하고, 사각형을
받는 모든 API는 일반적인 항목 네 개짜리 시퀀스도 받습니다.

```python
from pylopdf import Matrix, Point, Rect

hits = page.search_for("Total")
area = hits[0] | hits[-1]                    # 첫 결과와 마지막 결과의 합집합
inside = [hit for hit in hits if hit in page.rect]
box = area * Matrix(1, 0, 0, 1, 0, 20)        # 20포인트 아래로 이동
quad = area.morph(area.tl, Matrix(30))       # 모서리를 중심으로 30도 회전
```

| 연산 | 결과 |
|---|---|
| `rect + other`, `rect - other` | 사각형, 항목 네 개짜리 시퀀스, 스칼라와의 성분별 연산 |
| `rect * scalar`, `rect / scalar` | 모든 좌표에 배율 적용 |
| `rect * matrix`, `rect / matrix` | 변환한 모서리들의 외접 사각형. 무한 사각형은 무한으로 유지 |
| `rect \| point`, `rect \| other` | 점이나 사각형도 포함하는 가장 작은 사각형 |
| `rect & other` | 교집합. 겹치지 않는 사각형은 유효하지 않은 빈 결과 |
| `point in rect` | `x0 <= x < x1`이고 `y0 <= y < y1` |
| `other in rect`, `quad in rect` | 유효한 사각형, 또는 사변형의 외접 사각형이 가장자리 안에 있는지. 가장자리는 안쪽으로 셈 |
| `rect.width`, `rect.height` | 음수가 되지 않음 |
| `rect.is_empty`, `rect.is_valid`, `rect.is_infinite` | `x0 >= x1` 또는 `y0 >= y1`; `x0 <= x1`이고 `y0 <= y1`; `INFINITE_RECT()`와 같음 |
| `rect.intersects(other)` | 유한하고 비어 있지 않은 두 사각형이 면적을 공유하는지 여부 |
| `rect.round()`, `rect.irect` | 각 가장자리에서 0.001의 오차를 허용하는 덮개 `IRect` |
| `rect.torect(other)` | 한 사각형을 다른 사각형에 대응시키는 행렬 |
| `rect.morph(point, matrix)`, `quad.morph(point, matrix)` | 고정점을 중심으로 변환한 `Quad` |
| `rect.normalized()` | 가장자리를 정렬한 사각형. pymupdf의 제자리 `normalize()` 대신 사용 |
| `abs(rect)`, `rect.get_area(unit="px")` | 면적. 빈 사각형이나 무한 사각형의 `abs()`는 0 |
| `quad * matrix`, `quad / matrix`, `quad + other` | 모든 모서리를 변환하거나 이동 |
| `point in quad`, `rect in quad`, `other_quad in quad` | 가장자리를 포함한 포함 판정. 사각형이나 사변형은 모든 모서리가 안에 있어야 함 |
| `quad.rect`, `quad.width`, `quad.height` | 외접 사각형, 그리고 마주 보는 변 쌍 각각의 더 긴 변 |
| `quad.is_rectangular`, `quad.is_convex`, `quad.is_empty` | `rect.quad`의 회전 방향으로 1e-5 이내의 직각; 볼록한 윤곽; 1e-5보다 짧은 변 쌍 |

pymupdf의 제자리 메서드 `include_point`, `include_rect`, `intersect`, `normalize`,
`transform`은 없으므로 `r | p`, `r & other`, `r.normalized()`, `r * m`을 대입하세요.
pymupdf와 같이 `IRect`에는 `abs()`가 없고 항상 참이며, `Rect`는 네 좌표가 모두 0일
때만, `Quad`는 비어 있을 때만 거짓입니다. pymupdf는 사변형 안의 사각형을 모서리 두
개로만 판정하지만, pylopdf는 네 개를 모두 검사합니다.
