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

| 멤버 | 용도 |
|---|---|
| `doc[i]` / `load_page(pno)` / 반복 | `Page`뷰（음수 지원, 구조 변경 후 다시 가져오기） |
| `page_count` / `len(doc)` | 페이지 수 |
| `limits` / `complexity` | 열 때의 불변 리소스 정책 / stream 디코딩 없는 저비용 구조 지표 |
| `needs_pass` / `is_encrypted` / `authenticate(pw)` | 암호화 상태와 잠금 해제（pymupdf 의미론） |
| `is_repaired` | 열 때 마지막 classic `startxref` 오류를 복구했는지 여부. 저장하면 xref data를 정규화 |
| `metadata` / `set_metadata(dict)` | 표준Info 8개 필드（UTF-16BE 지원）, aggregate text 1 MiB 및 원자적 쓰기 |
| `get_page_text(pno, option)` | `"text"` / `"words"` / `"blocks"` / `"dict"` |
| `to_markdown(pages=None, table_strategy="lines", max_size=64 MiB)` | 상한이 있는 선형entry builder를 사용한page 단위2-pass Markdown 변환. 최대4,096 page 및 누적UTF-8 출력 상한(`None`으로 해제), 제목·CJK·강조·목록·단·세로쓰기 순서·표 제어 |
| `render_page(..., max_size=64 MiB)` / `render_pages(..., workers=, max_size=512 MiB)` / `render_page_svg(..., max_size=64 MiB)` | 상한이 있는PNG, 4,096 page 및 누적encoded output 상한이 있는 순서 보장 병렬PNG 묶음, 상한이 있는UTF-8 SVG(`None`으로 해제) |
| `compress_images(dpi=150, quality=75)` | 실제 배치DPI에 따라 안전한DCT/Flate raster XObject를 손실 축소·JPEG 재압축하고 타입 지정byte/count 통계를 반환 |
| `set_fallback_font(font, kind=, index=, max_font_size=64 MiB)` | 임베드되지 않은 글꼴의 상한이 있는CJK fallback font. 신뢰 가능한font input은`None`으로 해제 |
| `select` / `delete_page(s)` / `insert_pdf` / `new_page` / `copy_page` | 페이지 관리. select/delete/insert batch는4,096 entry로 제한 |
| `get_toc()` / `set_toc(toc)` | cycle을 처리하는 제한된 목차（페이지는 1부터, 4,096 entry/node, 8,192 edge, 깊이64, text 1 MiB） |
| `get_page_labels()` / `set_page_labels(labels)` | 페이지 레이블 범위. 고정 상한은4,096 entry/node, 깊이32, label text 1 MiB |
| `get_form_fields()` / `set_form_field(name, value, fontfile=, fontbuffer=, fontindex=, max_font_size=64 MiB)` | field/button state/font input이 제한된AcroForm 목록과 입력 및 네이티브 widget appearance |
| `embfile_add / embfile_names / embfile_get(name, max_size=64 MiB) / embfile_del` | 디코딩, 추가metadata 및inline FileSpec clone 형상에 상한이 있는 첨부 파일. `max_size=None`은 디코딩 상한을 명시적으로 해제 |
| `get_pdfa_claim(max_size=1 MiB)` | 상한이 있는XMP PDF/A 선언 읽기. `max_size=None`으로 명시적 해제하며 검증은 아님 |
| `save(...)` / `tobytes(..., max_size=512 MiB)` | 같은directory의stream 쓰기를 완전히 마친 뒤 원자적으로file 교체／상한이 있는PDF byte; `garbage=` `deflate=` `object_streams=` `user_pw=` `owner_pw=` `permissions=`; `max_size=None`으로 해제 |
| `close()` | `with`로도 호출 |

`compress_images()`는 모든 페이지를 해석해 각 간접raster object의 가장 큰 배치 크기를
찾은 뒤 lopdf clone을 원자적으로 편집합니다. `dpi=None`이면 축소 없이quality 재압축만
수행합니다. 보수적 범위는mask나 사용자decode array가 없는 직접 단일filter
8-bit DeviceGray/DeviceRGB DCT/Flate stream입니다. DCT decode parameter는 제외하며,
Flate는predictor가 없거나 사전과 일치하는PNG predictor를 사용할 수 있습니다. 해석된
미지원 간접 이미지와 더 작아지지 않는encoding은 건너뛰며 inline 이미지는 집계하지
않습니다. 같은 설정의 반복 호출은 멱등입니다.

## Page { #page }

| 멤버 | 용도 |
|---|---|
| `number` / `parent` / `get_label()` | 식별 정보와 표시 레이블 |
| `get_text(option)` / `search_for(needle)` | 추출과 대소문자 구분 없는 검색 |
| `get_text_ocr(dpi=, engine=, tile_size=, overlap=, min_confidence=, rotation=, clip=)` | 편집 없이 로컬PP-OCRv6로 위치가 있는 단어 인식, `rotation`은 입력을 시계 방향으로 보정하고 `clip`은 표시 좌표 |
| `apply_ocr(..., rotation=, clip=, skip_existing=True)` | 방향을 유지한 보이지 않는 검색 가능 레이어 삽입, 선택 영역의 기존 텍스트는 기본적으로 건너뜀 |
| `find_tables(strategy="lines", clip=None)` | 완전하거나 보수적으로 보완한 희소 벡터 테두리와 병합 셀. `"text"`로 테두리 없는 표를 감지하고 `clip`으로 표시 좌표 영역 지정 |
| `to_markdown(table_strategy="lines", max_size=64 MiB)` | 같은 표 및UTF-8 출력 제어를 사용하는 단일page Markdown |
| `get_images()` | 그려진 이미지（`bbox`, JPEG passthrough / PNG）. 4,096 placement, 누적64,000,000픽셀, payload 64 MiB를 넘는 부분 결과는 거부 |
| `get_drawings()` | 페이지에서 해석된 벡터fill/stroke 경로. 표시 좌표의line/cubic 도형과 정규화된 그리기 속성 |
| `get_pixmap(scale=, dpi=, background=, clip=)` / `render(max_size=64 MiB)` / `render_svg(max_size=64 MiB)` | 상한이 있는PNG / UTF-8 SVG 렌더링. `clip`은 표시 좌표 사용 |
| `rotation` / `set_rotation(deg)` | 표시 회전 |
| `mediabox` / `cropbox` / `rect` / `set_mediabox` / `set_cropbox` | 페이지 박스 |
| `insert_image(rect, filename= / stream= / pixmap=, rotate=, keep_proportion=, overlay=, max_size=64 MiB, max_pixels=64,000,000)` | 상한이 있는JPEG/PNG를 그리거나 이미 제한된RGBA `Pixmap` 재사용. 신뢰 가능한encoded input／PNG 픽셀은`None`으로 해제. `rotate`는90도 단위 시계 방향 회전 |
| `show_pdf_page(rect, src, pno=, keep_proportion=, overlay=)` | PDF 페이지를 벡터로 겹치기; `src`는 같은 문서여도 됨 |
| `insert_text(point, text, fontsize=, fontname=, fontfile=, fontbuffer=, fontindex=, color=, overlay=, max_font_size=64 MiB)` | Standard-14 또는 상한이 있는shaping subset text. `pylopdf[cjk]`는JP font 자동 선택. 신뢰 가능한font input은`None`으로 해제 |
| `insert_textbox(rect, text, fontsize=, fontname=, fontfile=, fontbuffer=, fontindex=, color=, align=, expandtabs=, lineheight=, overlay=, max_font_size=64 MiB)` | Core 14, 상한이 있는OpenType 또는 자동JP font 폭으로UAX #14 줄바꿈. 넘치면 그리지 않음 |
| `insert_ocr_text_layer(words, rotation=)` | 방향을 유지한 OCR 비가시 텍스트 레이어. call당4,096단어와UTF-8 text 1 MiB로 제한 |
| `replace_text(search, replacement, default_char=, max_size=64 MiB)` | 입출력 제한과 copy-on-write를 갖춘 원자적 단순 인코딩 교체 |
| `annots()` / `get_links()` / `add_highlight_annot(...)` / `add_link_annot(rect, uri)` | 제한된 주석／link 읽기, call당 하나의cycle-aware named-destination index 및 생성 |

`get_drawings()`는`type="f"` / `"s"` / `"fs"`, 자체 완결형line/cubic `items`,
`rect`, RGB/opacity, fill rule, width, cap, join, dashes를 포함한`DrawingInfo`
딕셔너리를 반환합니다. pattern paint는 도형을 유지하고 색상과opacity는`None`으로
둡니다. clip path, clip 적용 후 가시성 판단, group/soft-mask 구조, optional-content
layer 이름, text, image, annotation은 반환하지 않지만 optional-content 표시 상태는
적용합니다. 결과가8,192 paths 또는131,072 commands를 넘으면 잘라내지 않고 거부합니다.

내장 글꼴을 사용하는 `insert_text`에는 필요한 모든 글리프를 포함한 단일 글꼴이
필요합니다. source를 생략하고 `pylopdf[cjk]`를 설치하면 일본어／한자에 JP subset
Noto Sans를, Times `fontname`에는 Noto Serif를 자동 선택합니다. 이는 run 전체에서
font 하나를 고르는 것이며 glyph별 fallback이 아닙니다. 이 JP subset에는 Hangul이
없으므로 한국어에는 Noto Sans KR 같은 OpenType font를 명시해야 합니다. 다른 script나
서체도 마찬가지입니다. 각 줄은 shaping하지만 양방향 문단 layout과 줄바꿈은 제공하지
않습니다. RTL은 올바르게 렌더링되지만 추출은 현재 visual order입니다.

`insert_textbox`는 리치 텍스트 엔진이 아니라 명시적 줄바꿈, tab 확장, CJK의 Unicode
줄바꿈 기회, 너무 긴 단어의 grapheme 안전 긴급 줄바꿈을 처리합니다. 정렬 상수는
`TEXT_ALIGN_LEFT`, `TEXT_ALIGN_CENTER`, `TEXT_ALIGN_RIGHT`,
`TEXT_ALIGN_JUSTIFY`입니다. 반환값이 음수이면 세로 공간이 부족하며 페이지 내용이나
글꼴 resource를 추가하지 않습니다.

`set_form_field`는 텍스트, 콤보／목록 선택, checkbox, radio widget의 appearance를
생성합니다. WinAnsi는 Helvetica로 자동 축소하며, Unicode는 OpenType `fontfile`
또는 `fontbuffer`를 지정해 서브셋 내장합니다. `pylopdf[cjk]`가 설치되어 있으면
WinAnsi 밖의 값에 JP subset sans를 시도합니다. Hangul에는 Noto Sans KR 같은 font를
명시해야 합니다. 비어 있지 않은 기존 버튼 appearance는
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
| `peek_metadata(filename=None, stream=None, password=None, *, max_file_size=None)` | 입력 크기를 선택적으로 제한하는 빠른 메타데이터·페이지 수 조회. `repaired`는 제한된 classic `startxref` 복구를 표시 |
| `Permissions` | 암호화 권한 플래그（IntFlag） |
| `Rect` | `width` / `height`가 있는 사각형 NamedTuple |
| `TextPage` / `TextBlock` / `TextLine` / `TextSpan` | `get_text("dict")` TypedDict 계층 |
| `ImageInfo` / `ImageCompressionResult` / `AnnotationInfo` / `LinkInfo` / `FormFieldInfo` / `DrawingInfo` | page, document 작업, form, vector drawing의 사전 형식 결과를 위한 TypedDict 계약 |
| `DrawingItem` | line/cubic 그리기 명령을 나타내는 타입 별칭 |
| `PageLabelInfo` / `PageLabelSpec` | 정규화된 페이지 레이블 출력／setter 입력 계약 |
| `DocumentMetadata` / `MetadataUpdate` / `MetadataProbe` | metadata 출력／부분 업데이트／빠른 probe 계약 |
| `DocumentLimits` / `DocumentComplexity` | 신뢰할 수 없는 입력의 불변 예산／저비용 구조TypedDict |
| `OcrEngine` / `OcrWord` | 재사용 가능한 순수Rust PP-OCR 엔진과 위치 결과 계약 |
| `OcrRotation` / `WordEntry` / `BlockEntry` / `FormFieldType` | runtime에서 import 가능한 OCR 회전·tuple·literal 형식 별칭 |
| `TableFinder` / `Table` / `TableDiagnostics` | 독립 보관되는 표 좌표, 셀 텍스트(병합 연속 위치는`None`), strategy와 confidence 근거. `Table.to_markdown(max_size=64 MiB)`은 escape 후UTF-8 출력을 사전 검사 |
| `PdfError` / `LimitError` / `PasswordError` / `OcrError` / `DocumentClosedError` / `EncryptedDocumentError` / `StalePageError` | 예외 계층. 리소스 거부는 안정적인`.code` 제공（ValueError 호환 기반） |
| `Pixmap` | 불변 RGBA8 픽셀: `samples` / `width` / `height` / `stride` / `n` / `tobytes(max_size=64 MiB)` / 스트리밍하며 실패 시 기존file을 보존하는PNG 전용`save(path)`; cp314t에서는 읽기 전용 zero-copy `memoryview()`도 지원 |
| `PylopdfWarning` | 복구 가능한 해석 경고（xref 복구, 글꼴 해석, 이미지 디코딩） |

`TypedDict` 계약은 정적 타입에만 영향을 주며 값은 기존과 같은 일반 pymupdf 형식의
사전입니다. `LinkInfo`에는 `kind`와 `from`이 필수이고 대상별 키는 선택 사항입니다.
`PageLabelSpec`에는 `startpage`가 필요하며 `style`, `prefix`, `firstpagenum`의
runtime 기본값은 바뀌지 않습니다.
