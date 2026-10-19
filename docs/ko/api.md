---
title: API 개요
description: pylopdf의 Document, Page, Pixmap, Rect, 권한, 경고, 예외를 한눈에 보는 API 지도입니다.
---

# API 개요

전체 docstring은 패키지 안에 있으며`help(pylopdf.Document)`로 볼 수 있습니다.
이 페이지는 API 지도입니다.`get_toc` / `set_toc`만 pymupdf 호환을 위해 1부터 시작하고
나머지 페이지 번호는 모두 0부터 시작합니다. 모든 좌표는 왼쪽 위가 원점인 표시 공간입니다.
[API 안정성 정책](stability.md)은 공개 경계와 사용 중단 절차를 정의합니다.

## Document { #document }

`pylopdf.Document(filename=None, stream=None, password=None, max_decompressed_size=None, *, limits=None)` —
`pylopdf.open()`은 별칭 생성자이며 컨텍스트 관리자를 지원합니다.
password input은UTF-8 127 byte로 제한됩니다.

| 멤버 | 용도 |
|---|---|
| `doc[i]` / `load_page(pno)` / 반복 | `Page`뷰（음수 지원, 구조 변경 후 다시 가져오기） |
| `page_count` / `len(doc)` | 페이지 수 |
| `limits` / `complexity` | 열 때의 불변 리소스 정책 / stream 디코딩 없는 저비용 구조 지표 |
| `needs_pass` / `is_encrypted` / `authenticate(pw)` | 127-byte password 상한이 있는 암호화 상태와 잠금 해제（pymupdf 의미론） |
| `is_repaired` | 열 때 손상된 cross-reference data를 복구했는지 여부(offset 보정 또는 table 재구성). 저장하면 xref data를 정규화 |
| `metadata` / `set_metadata(dict)` | 표준Info 8개 필드（UTF-16BE 지원）, aggregate text 1 MiB 및 원자적 쓰기 |
| `get_page_text(pno, option)` | `"text"` / `"words"` / `"blocks"` / `"dict"` |
| `get_text(pages=None)` | 전체 page(`None`) 또는 page 수와 4,096 중 큰 값까지의 명시 entry를 한 번의 batch로 추출하는 plain text. 4,096 entry마다의 chunk가 interpreter font cache를 공유 |
| `to_markdown(pages=None, table_strategy="lines", max_size=64 MiB)` | 상한이 있는 선형entry builder를 사용한page 단위2-pass Markdown 변환. 기본은 전체page, 명시entry는 page 수와4,096 중 큰 값까지이며 누적UTF-8 출력 상한(`None`으로 해제), 제목·CJK·강조·목록·단·세로쓰기 순서·표 제어 |
| `render_page(..., max_size=64 MiB)` / `render_pages(..., workers=, max_size=512 MiB)` / `render_page_svg(..., max_size=64 MiB)` | 상한이 있는PNG, 누적encoded output 상한 안에서 전체page 또는 page 수와4,096 중 큰 값까지의 명시entry를 렌더링하는 순서 보장 병렬PNG 묶음, 상한이 있는UTF-8 SVG(`None`으로 해제) |
| `compress_images(dpi=150, quality=75)` | 실제 배치DPI에 따라 안전한DCT/Flate raster XObject를 손실 축소·JPEG 재압축하고 타입 지정byte/count 통계를 반환 |
| `set_fallback_font(font, kind=, index=, font_language="ja", max_font_size=64 MiB)` | Adobe Japan1／Korea1／GB1／CNS1을 구분하는 제한된CJK fallback font. 신뢰 가능한font input은`None`으로 해제 |
| `select` / `delete_page(s)` / `insert_pdf` / `new_page` / `copy_page` | 페이지 관리. select/delete entry는 page 수와4,096 중 큰 값까지이며 `insert_pdf`는 원본의 전체 범위를 허용하고 가져온 페이지의 폼 필드를 등록하며, navigation 정리와 link 가져오기는 TOC·named destination 상한을 공유 |
| `get_toc()` / `set_toc(toc)` | 대규모 manual에 맞춘, cycle을 처리하는 제한된 목차（페이지는 1부터, 65,536 entry/node, 131,072 edge, 깊이64, text 16 MiB, named destination은`get_links()`와 같음） |
| `get_page_labels()` / `set_page_labels(labels)` | 페이지 레이블 범위. 고정 상한은4,096 entry/node, 깊이32, label text 1 MiB |
| `get_form_fields()` / `set_form_field(name, value, font=, fontfile=, fontbuffer=, fontindex=, font_language=, max_font_size=64 MiB)` | caller 이름／값1 MiB와 field/button state/font input이 제한된AcroForm 목록과 입력 및 네이티브 widget appearance. `font`에는 불러온 OpenType `Font`를 전달할 수 있음 |
| `embfile_add(..., max_size=64 MiB) / embfile_names / embfile_get(name, max_size=64 MiB) / embfile_del` | 입력과 디코딩 출력에 대칭 기본 상한을 적용하고 caller text1 MiB, 추가metadata 및inline FileSpec clone 형상도 제한하는 첨부 파일. `max_size=None`은 상한을 명시적으로 해제 |
| `get_pdfa_claim(max_size=1 MiB)` | 상한이 있는XMP PDF/A 선언 읽기. `max_size=None`으로 명시적 해제하며 검증은 아님 |
| `save(...)` / `tobytes(..., max_size=512 MiB)` | 같은directory의stream 쓰기를 완전히 마친 뒤 원자적으로file 교체／상한이 있는PDF byte. `garbage=` `deflate=` `object_streams=` 및127-byte 상한의`user_pw=`／`owner_pw=`; `max_size=None`으로 해제 |
| `close()` | `with`로도 호출 |

`compress_images()`는 모든 페이지를 해석해 각 간접raster object의 가장 큰 배치 크기를
찾은 뒤 lopdf clone을 원자적으로 편집합니다. `dpi=None`이면 축소 없이quality 재압축만
수행합니다. 보수적 범위는mask나 사용자decode array가 없는 직접 단일filter
8-bit DeviceGray/DeviceRGB DCT/Flate stream입니다. DCT decode parameter는 제외하며,
Flate는predictor가 없거나 사전과 일치하는PNG predictor를 사용할 수 있습니다. 해석된
미지원 간접 이미지와 더 작아지지 않는encoding은 건너뛰며 inline 이미지는 집계하지
않습니다. 한 번의 호출에서 해석하는 간접raster placement가65,536개를 초과하면
거부합니다. 같은 설정의 반복 호출은 멱등입니다.

## Page { #page }

| 멤버 | 용도 |
|---|---|
| `number` / `parent` / `get_label()` | 식별 정보와 표시 레이블 |
| `get_text(option)` / `search_for(needle, max_hits=4096)` | 추출 및 상한이 있는 대소문자 구분 없는 검색. 검색어는UTF-8 4,096 byte, 신뢰 가능한 결과 집합은`None`으로 해제 |
| `get_text_ocr(dpi=, engine=, tile_size=, overlap=, min_confidence=, rotation=, clip=)` | 편집 없이 로컬PP-OCRv6로 위치가 있는 단어 인식, `rotation`은 입력을 시계 방향으로 보정하고 `clip`은 표시 좌표 |
| `apply_ocr(..., rotation=, clip=, skip_existing=True)` | 방향을 유지한 보이지 않는 검색 가능 레이어 삽입, 선택 영역의 기존 텍스트는 기본적으로 건너뜀 |
| `find_tables(strategy="lines", clip=None)` | 완전하거나 보수적으로 보완한 희소 벡터 테두리와 병합 셀. `"text"`로 테두리 없는 표를 감지하고 `clip`으로 표시 좌표 영역 지정 |
| `to_markdown(table_strategy="lines", max_size=64 MiB)` | 같은 표 및UTF-8 출력 제어를 사용하는 단일page Markdown |
| `get_images()` | 그려진 이미지（`bbox`, JPEG passthrough / PNG）. 4,096 placement, 누적64,000,000픽셀, payload 64 MiB를 넘는 부분 결과는 거부 |
| `get_drawings()` | 페이지에서 해석된 벡터fill/stroke 경로. 표시 좌표의line/cubic 도형과 정규화된 그리기 속성. 페이지당 8,192개 경로, 131,072개 명령 또는 누적 131,072개 dash 값을 넘으면 부분 결과 없이 거부 |
| `get_pixmap(scale=, dpi=, background=, clip=)` / `render(max_size=64 MiB)` / `render_svg(max_size=64 MiB)` | 상한이 있는PNG / UTF-8 SVG 렌더링. `clip`은 표시 좌표 사용 |
| `rotation` / `set_rotation(deg)` | 표시 회전 |
| `mediabox` / `cropbox` / `rect` / `set_mediabox` / `set_cropbox` | 페이지 박스 |
| `insert_image(rect, filename= / stream= / pixmap=, rotate=, keep_proportion=, overlay=, max_size=64 MiB, max_pixels=64,000,000)` | 상한이 있는JPEG/PNG를 그리거나 이미 제한된RGBA `Pixmap` 재사용. 신뢰 가능한encoded input／PNG 픽셀은`None`으로 해제. `rotate`는90도 단위 시계 방향 회전. 같은 입력을 다시 배치하면 이미지 XObject를 공유 |
| `show_pdf_page(rect, src, pno=, keep_proportion=, overlay=)` | PDF 페이지를 벡터로 겹치기; `src`는 같은 문서여도 됨; 편집하지 않은 `src`의 같은 페이지를 다시 배치하면 Form XObject를 공유 |
| `insert_text(point, text, fontsize=, font=, fontname=, fontfile=, fontbuffer=, fontindex=, font_language=, color=, overlay=, max_font_size=64 MiB, max_text_size=1 MiB)` | UTF-8과4,096줄 상한이 있는Standard-14 또는 shaping subset text. `font`에는 불러온`Font`를 전달할 수 있음. 설치된script／locale provider를 자동 선택하고 모호한 한자는`font_language`로 지정. 해당 신뢰 가능한input은`None`으로 해제 |
| `insert_textbox(rect, text, fontsize=, font=, fontname=, fontfile=, fontbuffer=, fontindex=, font_language=, color=, align=, expandtabs=, lineheight=, overlay=, max_font_size=64 MiB, max_text_size=1 MiB)` | text와tab 확장을 미리 검사하고 Core 14, OpenType, 불러온`Font` 또는 설치된provider 폭으로UAX #14 줄바꿈. 물리줄과 줄바꿈 후layout은4,096줄이 상한이며 넘치면 그리지 않음 |
| `new_shape()` | 경로와 글자를 한 번에 그리는 pymupdf의 `Shape`. [도형](#shapes) 참조 |
| `draw_line` / `draw_polyline` / `draw_rect` / `draw_quad` / `draw_circle` / `draw_oval` / `draw_sector` / `draw_curve` / `draw_bezier` / `draw_squiggle` / `draw_zigzag`(geometry, *, color=(0,), fill=None, width=1, dashes=None, lineCap=0, lineJoin=0, closePath=, overlay=True, stroke_opacity=1, fill_opacity=1) | 마무리한 도형 하나를 그리고 pymupdf와 같은 끝점 `Point`를 반환. `fill`과 `closePath`는 pymupdf가 제공하는 메서드에만 있고, `draw_rect`에는 `radius=`, `draw_sector`에는 `fullSector=`, 물결선에는 `breadth`가 있음(pymupdf처럼 점 뒤에 위치 인수로도 전달 가능) |
| `insert_ocr_text_layer(words, rotation=)` | 방향을 유지한 OCR 비가시 텍스트 레이어. call당4,096단어와UTF-8 text 1 MiB로 제한 |
| `replace_text(search, replacement, default_char=, max_size=64 MiB)` | 입출력 제한과 copy-on-write를 갖춘 원자적 단순 인코딩 교체 |
| `annots()` / `get_links()` / `add_highlight_annot(...)` / `add_link_annot(rect, uri)` | 제한된 주석／link 읽기와 생성. named destination은 최대65,536 entry/node, 131,072 edge, 깊이32, key 16 MiB의 name tree로 해석하며, tree는 한 번만index화해 다음 편집 전까지 이후 읽기에서 재사용. 렌더링 시 유효한`QuadPoints`를 가진 상한 내 RGB Highlight, Underline, StrikeOut, Squiggly의 누락 appearance를 원본 PDF 변경 없이 보수적으로 보완 |

`get_drawings()`는`type="f"` / `"s"` / `"fs"`, 자체 완결형line/cubic `items`,
`rect`, RGB/opacity, fill rule, width, cap, join, dashes를 포함한`DrawingInfo`
딕셔너리를 반환합니다. pattern paint는 도형을 유지하고 색상과opacity는`None`으로
둡니다. clip path, clip 적용 후 가시성 판단, group/soft-mask 구조, optional-content
layer 이름, text, image, annotation은 반환하지 않지만 optional-content 표시 상태는
적용합니다. 결과가8,192 paths 또는131,072 commands를 넘으면 잘라내지 않고 거부합니다.

내장 글꼴을 사용하는 `insert_text`에는 필요한 모든 글리프를 포함한 단일 글꼴이
필요합니다. `pylopdf[ar]`, `[he]`, `[hi]`, `[jp]`, `[ko]`, `[th]`,
`[zh-cn]`, `[zh-tw]`는 Noto Sans／Serif 생성 font를 제공합니다. Kana, Hangul,
Bopomofo, Arabic, Hebrew, Devanagari, Thai는 설치된 provider를 자동 선택합니다.
순수 한자는 호환성을 위해 JP를 우선하며 지역 glyph가 중요하면
`font_language="ko"`, `"zh-CN"`, `"zh-TW"` 또는 `"ja"`를 지정합니다. run
전체에서 font 하나를 고르며 glyph별 fallback은 아닙니다. 각 줄은 shaping하지만
양방향 문단 layout과 줄바꿈은 제공하지
않습니다. RTL은 올바르게 렌더링됩니다. 순수 RTL 줄과 RTL text 양쪽에 둘러싸인
Latin 또는 숫자 token이 하나뿐인 줄은 논리적 Unicode 순서로 추출합니다. 줄 가장자리
token, 여러 token 및 일반적인 혼합 방향 paragraph는 producer의 visual order를
유지합니다.

`insert_textbox`는 리치 텍스트 엔진이 아니라 명시적 줄바꿈, tab 확장, CJK의 Unicode
줄바꿈 기회, 너무 긴 단어의 grapheme 안전 긴급 줄바꿈을 처리합니다. 정렬 상수는
`TEXT_ALIGN_LEFT`, `TEXT_ALIGN_CENTER`, `TEXT_ALIGN_RIGHT`,
`TEXT_ALIGN_JUSTIFY`입니다. 반환값이 음수이면 세로 공간이 부족하며 페이지 내용이나
글꼴 resource를 추가하지 않습니다.

`Font`는 pymupdf의 `Font(fontname=None, fontfile=None, fontbuffer=None)`을
따르며 `insert_text(font=)`와 `insert_textbox(font=)`에 전달합니다. OpenType 글꼴은
`set_form_field(font=)`에도 전달할 수 있습니다.
`Font("helv")`나 `Font("Times-Bold")`는 Standard 14 글꼴을 가리키며, 포함하지 않고
그리고 Adobe AFM 폭으로 측정합니다. pymupdf의 다른 내장 글꼴은 쓸 수 없습니다.
`Font(fontfile=)`와 `Font(fontbuffer=)`는 OpenType 글꼴을 그대로 불러오며, 가변 글꼴은
기본 인스턴스로 사용합니다. 기본 인스턴스가 Regular라는 보장은 없으며, 가변 글꼴인
Noto Sans CJK JP의 기본 인스턴스는 Thin입니다. `weight=`를 전달하면 가변 글꼴의 굵기 축에서
다른 인스턴스를 선택합니다. `Font(fontfile="NotoSansCJKjp-VF.otf", weight=700)`는 Bold
인스턴스의 advance로 측정하고 `NotoSansCJKjp-Bold`로 포함하며, `weight=650`도 선택할 수
있으므로 이런 글꼴에서는 `weight=400`과 생략한 경우의 결과가 다릅니다. 축 범위를 벗어난
굵기는 `PdfError`가 되며, 정적 글꼴에 없는 굵기도 마찬가지입니다. `insert_text`,
`insert_textbox`, `set_form_field`에 전달하는 `fontfile`과 `fontbuffer`는 기본 인스턴스로
그립니다. 선택한 인스턴스로 그리려면 그 `Font`를 `font=`에 전달합니다.
`Font(language="ko")`나 `Font(family="Noto Sans KR")`는 설치된
글꼴 extra의 글꼴을 선택합니다(`is_serif=True`이면 세리프). `font.text_length(text,
fontsize)`는 그리기와 같은 shaping으로 커닝과 합자를 포함해 측정하므로, 그리는 글꼴의
폭으로 줄바꿈, 정렬, 페이지 나누기를 계산할 수 있습니다. `ascender`와 `descender`는
`insert_textbox`가 줄을 배치할 때 쓰는 em 대비 비율이고, `has_glyph(chr)`는 코드
포인트의 글리프 번호(없으면 0)를 반환합니다. CJK extra는 스타일마다 정적 글꼴 하나만
설치하므로, 글꼴과 맞지 않는 `is_bold`, `is_italic`, OS/2 `weight` 지정은 `PdfError`가
되며 Noto Sans KR Bold 같은 다른 굵기는 파일에서 불러옵니다. 반면 `pylopdf[ar]`,
`[he]`, `[hi]`, `[th]`는 가변 글꼴을 설치하므로 `Font(language="he", is_bold=True)`는
다른 파일 없이 글꼴의 굵기 축에서 Bold 인스턴스를 선택하고, 그 인스턴스의 advance로
측정하며, `NotoSansHebrew-Bold`라는 PostScript 이름으로 포함합니다. 굵기 범위는
100부터 900까지이고, `pylopdf[ar]`의 세리프 글꼴인 Noto Naskh Arabic은 400부터
700까지입니다. 축 밖의 굵기나 기울임 지정은 `PdfError`가 됩니다. 설치된 가변 글꼴은
`insert_text`, `insert_textbox`, `set_form_field`가 자동으로 선택하는 경우를 포함해
Regular 굵기로 그립니다. Noto Sans Hebrew의 기본 인스턴스가 Thin이기 때문입니다. pylopdf는 글꼴 등록표를 두지 않고 OS
글꼴 폴더도 찾지 않습니다. 윤곽선이나 덧칠로 굵기를 흉내 내지 않으며,
`allow_regular_fallback=True`를 명시한 경우에만 extra의 글꼴을 쓰고 `PylopdfWarning`을
냅니다. pymupdf라면 무시하는 경우에도 `fontname`이나 글꼴 파일과 함께 `is_bold`,
`is_italic`, `is_serif`를 전달하면 예외가 납니다. 그리기 호출마다 그 호출이 쓰는 글자의 subset을 포함하는 점은
그대로입니다. [도형](#shapes)으로 그리면 글꼴은 확정 한 번마다 한 번만 포함됩니다.

### 도형 { #shapes }

`page.new_shape()`는 pymupdf의 `Shape`를 반환합니다. `draw_line`, `draw_polyline`,
`draw_rect`(`radius=`로 모서리를 둥글게), `draw_quad`, `draw_circle`, `draw_oval`,
`draw_sector`, `draw_curve`, `draw_bezier`, `draw_squiggle`, `draw_zigzag`는 표시
좌표로 경로를 더하고 pymupdf와 같은 끝점을 반환합니다. 앞 선의 끝점에서 시작하는
선은 같은 하위 경로를 이어 갑니다. `finish(*, width=1, color=(0,), fill=None,
lineCap=0, lineJoin=0, dashes=None, even_odd=False, closePath=True,
fill_opacity=1, stroke_opacity=1)`는 앞의 `finish` 이후 그린 경로를 선과 채우기로
마무리합니다. 색은 0~1의 회색, RGB, CMYK이며, `dashes`는 `"[3 4] 0"` 같은 PDF
점선 지정입니다. `insert_text`와 `insert_textbox`는 `Page.insert_text`,
`Page.insert_textbox`와 같은 글꼴 지정을(`font=` 포함) 받습니다. `insert_text`는
줄 수를, `insert_textbox`는 남은 높이를 반환하며, 들어가지 않으면 음수를 반환하고
아무것도 더하지 않습니다. `commit(overlay=True)`로 페이지에 그립니다. pymupdf와
마찬가지로 마무리한 경로를 순서대로 그리고 그 위에 모든 글자를 그립니다.

```python
with page.new_shape() as shape:
    shape.draw_rect((50, 50, 545, 792))
    shape.finish(color=(0.72, 0.78, 0.82), fill=(0.95, 0.96, 0.97), width=0.5)
    shape.draw_circle((80, 140), 7)
    shape.finish(color=(1, 1, 1), fill=(0.83, 0.26, 0.05), width=1.2)
    shape.insert_text((56, 80), "점검 보고서", font=bold, fontsize=18)
    shape.insert_textbox((56, 160, 540, 400), body, font=regular, fontsize=10.5)
```

확정 한 번에 Form XObject 하나를 둡니다. 경로와 포함 글꼴의 글자를 함께 생성하므로,
몇 줄을 쓰든 글꼴은 확정 한 번마다 한 번만 포함됩니다. 한 페이지의 글자를 도형
하나로 그리면 글꼴은 페이지마다 한 번만 포함됩니다. `with` 블록은 정상적으로 끝나면
확정하고, 예외가 나면 아무것도 그리지 않습니다. 확정하지 않은 채 버린 도형은
`ResourceWarning`을 냅니다. pymupdf가 조용히 버리거나 검정으로 그리는 경우
pylopdf는 `PdfError`를 냅니다. `commit()`은 `finish()`로 마무리하지 않은 경로를
받지 않고, `finish()`는 아무것도 그리지 않는 지정(채우기가 없고 `color=None` 또는
`width=0`)을 받지 않습니다. `morph=`와 `oc=`는 쓸 수 없습니다. 확정 전까지 도형이
가질 수 있는 경로 점은 1,048,576개까지이며, 넘으면 `LimitError`(code
`path_point_count`)가 됩니다. `Page.draw_*`는 같은 지정을 키워드 인수로 받아
마무리한 도형 하나를 그립니다.

`set_form_field`는 텍스트, 콤보／목록 선택, checkbox, radio widget의 appearance를
생성합니다. WinAnsi는 Helvetica로 자동 축소하며, Unicode는 OpenType `fontfile`
또는 `fontbuffer`를 지정해 서브셋 내장합니다. 가변 글꼴의 Bold 인스턴스처럼 불러온
OpenType `Font`를 `font`에 전달할 수도 있습니다. `Font`를 쓰면 `text_length`가 측정하는
advance로 축소와 정렬을 계산하고 comb 필드의 각 위치 중앙에 배치합니다. `font`는 다른
글꼴 지정과 함께 쓸 수 없으며, Standard 14 `Font`는 `PdfError`가 됩니다.
설치된script／locale provider는 `insert_text`와 같은 규칙으로 선택하며 모호한 한자는
`font_language`로 지정합니다.
비어 있지 않은 기존 버튼 appearance는
보존하고 누락된 상태만 벡터로 만듭니다. 다른 WinAnsi 필드의 누락된 appearance도
함께 채우며, 입력 가능한 모든 widget이 자체 완결일 때만 `NeedAppearances`를
해제합니다. comb 텍스트 필드는 상속된 `MaxLen`과 정렬을 따르고 각 Unicode
grapheme을 해당 위치 중앙에 배치하며, 너무 긴 값은 문서를 변경하지 않고 거부합니다.
rich text, pushbutton action, 서명은 생성하지 않습니다.

`Table.confidence`는 0–1의 결정적 순위 지정 heuristic이며 보정된 확률이 아닙니다.
`Table.diagnostics`는 `TableDiagnostics` tuple입니다. 테두리 없는 텍스트 표에서는
em으로 정규화한 정렬 오차, 최소 gutter, 행 간격 변화를 보존합니다. 완전한 벡터
grid는 1.0, 희소 규칙을 보완한 hybrid grid는 0.95이며 두 경우 모두 텍스트 전용
metric은 `None`입니다. `TableFinder.strategy`와
`TableFinder.clip`에는 사용한 설정이 남습니다.

## 모듈 수준 { #module-level }

| 이름 | 용도 |
|---|---|
| `peek_metadata(filename=None, stream=None, password=None, *, max_file_size=None)` | 입력 크기를 선택적으로 제한하고password를127 byte로 제한하는 빠른 메타데이터·페이지 수 조회. `repaired`는 cross-reference 복구를 표시 |
| `Permissions` | 암호화 권한 플래그（IntFlag） |
| `Rect` | `width` / `height`가 있는 사각형 NamedTuple |
| `TextPage` / `TextBlock` / `TextLine` / `TextSpan` | `get_text("dict")` TypedDict 계층 |
| `ImageInfo` / `ImageCompressionResult` / `AnnotationInfo` / `LinkInfo` / `FormFieldInfo` / `DrawingInfo` | page, document 작업, form, vector drawing의 사전 형식 결과를 위한 TypedDict 계약 |
| `DrawingItem` | line/cubic 그리기 명령을 나타내는 타입 별칭 |
| `PageLabelInfo` / `PageLabelSpec` | 정규화된 페이지 레이블 출력／setter 입력 계약 |
| `DocumentMetadata` / `MetadataUpdate` / `MetadataProbe` | metadata 출력／부분 업데이트／빠른 probe 계약 |
| `DocumentLimits` / `DocumentComplexity` | PDF snapshot용`max_interpretation_size`와 위치layout용`max_text_glyphs`를 포함한 신뢰할 수 없는 입력의 불변 예산／저비용 구조TypedDict |
| `OcrEngine` / `OcrWord` | 재사용 가능한 순수Rust PP-OCR 엔진과 위치 결과 계약 |
| `Font(fontname=None, fontfile=None, fontbuffer=None, *, language=None, is_bold=False, is_italic=False, is_serif=False, weight=None, fontindex=0, family=None, allow_regular_fallback=False, max_font_size=64 MiB)` | `text_length`, `ascender`／`descender`, `has_glyph`, `name`, `family`, `weight`, `is_bold`, `is_italic`, `is_serif`, `units_per_em`을 가진 재사용 가능한 Standard 14 또는 OpenType 글꼴. `weight=`는 가변 글꼴 파일의 인스턴스도 선택 |
| `Shape(page)` | 경로와 글자를 한 번에 그리는 pymupdf의 `Shape`. `page.new_shape()`로도 만들 수 있음 |
| `OcrRotation` / `WordEntry` / `BlockEntry` / `FormFieldType` | runtime에서 import 가능한 OCR 회전·tuple·literal 형식 별칭 |
| `TableFinder` / `Table` / `TableDiagnostics` | 독립 보관되는 표 좌표, 셀 텍스트(병합 연속 위치는`None`), strategy와 confidence 근거. `Table.to_markdown(max_size=64 MiB)`은 escape 후UTF-8 출력을 사전 검사 |
| `PdfError` / `LimitError` / `PasswordError` / `OcrError` / `DocumentClosedError` / `EncryptedDocumentError` / `StalePageError` | 예외 계층. 리소스 거부는 안정적인`.code` 제공（ValueError 호환 기반） |
| `Pixmap` | 불변 RGBA8 픽셀: `samples` / `width` / `height` / `stride` / `n` / `tobytes(max_size=64 MiB)` / 스트리밍하며 실패 시 기존file을 보존하는PNG 전용`save(path)`; cp314t와 cp315t에서는 읽기 전용 zero-copy `memoryview()`도 지원 |
| `PylopdfWarning` | 복구 가능한 해석 경고（xref 복구, 글꼴 해석, 이미지 디코딩） |

`TypedDict` 계약은 정적 타입에만 영향을 주며 값은 기존과 같은 일반 pymupdf 형식의
사전입니다. `LinkInfo`에는 `kind`와 `from`이 필수이고 대상별 키는 선택 사항입니다.
`PageLabelSpec`에는 `startpage`가 필요하며 `style`, `prefix`, `firstpagenum`의
runtime 기본값은 바뀌지 않습니다.
