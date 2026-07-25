---
title: API 개요
description: pylopdf의 Document, Page, Pixmap, Rect, 권한, 경고, 예외를 한눈에 보는 API 지도입니다.
---

# API 개요

전체 docstring은 패키지 안에 있으며`help(pylopdf.Document)`로 볼 수 있습니다.
이 페이지는 API 지도입니다.`get_toc` / `set_toc`만 pymupdf 호환을 위해 1부터 시작하고
나머지 페이지 번호는 모두 0부터 시작합니다. 모든 좌표는 왼쪽 위가 원점인 표시 공간입니다.

## Document { #document }

`pylopdf.Document(filename=None, stream=None, password=None, max_decompressed_size=None)` —
`pylopdf.open()`은 별칭 생성자이며 컨텍스트 관리자를 지원합니다.

| 멤버 | 용도 |
|---|---|
| `doc[i]` / `load_page(pno)` / 반복 | `Page`뷰（음수 지원, 구조 변경 후 다시 가져오기） |
| `page_count` / `len(doc)` | 페이지 수 |
| `needs_pass` / `is_encrypted` / `authenticate(pw)` | 암호화 상태와 잠금 해제（pymupdf 의미론） |
| `metadata` / `set_metadata(dict)` | Info 딕셔너리（UTF-16BE 지원） |
| `get_page_text(pno, option)` | `"text"` / `"words"` / `"blocks"` / `"dict"` |
| `to_markdown(pages=None, table_strategy="lines")` | Markdown 변환(제목, CJK 연결, 강조, 목록, 다단 및 보수적인 세로쓰기 순서, 기본 테두리 표, `"text"`로 테두리 없는 표 추가, `None`으로 표 변환 비활성화) |
| `render_page(...)` / `render_pages(..., workers=)` / `render_page_svg(...)` | PNG, 순서 보장 병렬 PNG 묶음, SVG |
| `set_fallback_font(font, kind=, index=)` | 임베드되지 않은 글꼴의 CJK 대체 글꼴 |
| `select` / `delete_page(s)` / `insert_pdf` / `new_page` / `copy_page` | 페이지 관리 |
| `get_toc()` / `set_toc(toc)` | 목차（페이지는 1부터） |
| `get_page_labels()` / `set_page_labels(labels)` | 페이지 레이블 범위 |
| `get_form_fields()` / `set_form_field(name, value, fontfile=, fontbuffer=, fontindex=)` | 네이티브 widget appearance를 포함한 AcroForm 목록과 입력 |
| `embfile_add / embfile_names / embfile_get / embfile_del` | 첨부 파일 |
| `get_pdfa_claim()` | XMP PDF/A 선언 읽기（검증 아님） |
| `save(...)` / `tobytes(...)` | `garbage=` `deflate=` `object_streams=` `user_pw=` `owner_pw=` `permissions=` |
| `close()` | `with`로도 호출 |

## Page { #page }

| 멤버 | 용도 |
|---|---|
| `number` / `parent` / `get_label()` | 식별 정보와 표시 레이블 |
| `get_text(option)` / `search_for(needle)` | 추출과 대소문자 구분 없는 검색 |
| `get_text_ocr(dpi=, engine=, tile_size=, overlap=, min_confidence=, rotation=, clip=)` | 편집 없이 로컬PP-OCRv6로 위치가 있는 단어 인식, `rotation`은 입력을 시계 방향으로 보정하고 `clip`은 표시 좌표 |
| `apply_ocr(..., rotation=, clip=, skip_existing=True)` | 방향을 유지한 보이지 않는 검색 가능 레이어 삽입, 선택 영역의 기존 텍스트는 기본적으로 건너뜀 |
| `find_tables(strategy="lines", clip=None)` | 완전하거나 보수적으로 보완한 희소 벡터 테두리와 병합 셀. `"text"`로 테두리 없는 표를 감지하고 `clip`으로 표시 좌표 영역 지정 |
| `to_markdown(table_strategy="lines")` | 같은 표 제어를 사용하는 한 페이지의 Markdown |
| `get_images()` | 그려진 이미지（`bbox`, JPEG 패스스루 / PNG） |
| `get_pixmap(scale=, dpi=, background=, clip=)` / `render(...)` / `render_svg()` | 렌더링. `clip`은 표시 좌표 사용 |
| `rotation` / `set_rotation(deg)` | 표시 회전 |
| `mediabox` / `cropbox` / `rect` / `set_mediabox` / `set_cropbox` | 페이지 박스 |
| `insert_image(rect, filename= / stream= / pixmap=, keep_proportion=, overlay=)` | JPEG/PNG 또는 렌더링된 RGBA `Pixmap`을 직접 삽입 |
| `show_pdf_page(rect, src, pno=, keep_proportion=, overlay=)` | 다른 PDF 페이지를 벡터로 겹치기 |
| `insert_text(point, text, fontsize=, fontname=, fontfile=, fontbuffer=, fontindex=, color=, overlay=)` | Standard-14 WinAnsi 또는 서브셋 내장 OpenType Unicode 텍스트 |
| `insert_textbox(rect, text, fontsize=, fontname=, fontfile=, fontbuffer=, fontindex=, color=, align=, expandtabs=, lineheight=, overlay=)` | Core 14 또는 내장 OpenType 실제 폭으로 UAX #14 줄바꿈, 남은 높이를 반환하며 넘치면 그리지 않음 |
| `insert_ocr_text_layer(words, rotation=)` | 방향을 유지한 OCR 비가시 텍스트 레이어（검색 가능한 PDF） |
| `replace_text(search, replacement, default_char=)` | 단순 인코딩 텍스트 교체 |
| `annots()` / `add_highlight_annot(...)` / `add_link_annot(rect, uri)` | 주석 |

내장 글꼴을 사용하는 `insert_text`에는 필요한 모든 글리프를 포함한 단일 글꼴이
필요합니다. 각 줄의 셰이핑은 수행하지만 글꼴 폴백, 양방향 단락 레이아웃 또는 자동
줄바꿈은 제공하지 않습니다. RTL 셰이핑은 올바르게 렌더링되지만 현재 텍스트 추출은
논리 순서가 아닌 시각적 순서를 따릅니다.

`insert_textbox`는 리치 텍스트 엔진이 아니라 명시적 줄바꿈, tab 확장, CJK의 Unicode
줄바꿈 기회, 너무 긴 단어의 grapheme 안전 긴급 줄바꿈을 처리합니다. 정렬 상수는
`TEXT_ALIGN_LEFT`, `TEXT_ALIGN_CENTER`, `TEXT_ALIGN_RIGHT`,
`TEXT_ALIGN_JUSTIFY`입니다. 반환값이 음수이면 세로 공간이 부족하며 페이지 내용이나
글꼴 resource를 추가하지 않습니다.

`set_form_field`는 텍스트, 콤보／목록 선택, checkbox, radio widget의 appearance를
생성합니다. WinAnsi는 Helvetica로 자동 축소하며, Unicode는 OpenType `fontfile`
또는 `fontbuffer`를 지정해 서브셋 내장합니다. `pylopdf[cjk]`가 설치되어 있으면
WinAnsi 밖의 값에 sans 글꼴을 자동 사용합니다. 비어 있지 않은 기존 버튼 appearance는
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
| `peek_metadata(path_or_stream, password=)` | 전체 파싱 없이 메타데이터와 페이지 수를 빠르게 조회 |
| `Permissions` | 암호화 권한 플래그（IntFlag） |
| `Rect` | `width` / `height`가 있는 사각형 NamedTuple |
| `TextPage` / `TextBlock` / `TextLine` / `TextSpan` | `get_text("dict")` TypedDict 계층 |
| `ImageInfo` / `AnnotationInfo` / `LinkInfo` / `FormFieldInfo` | page와 form의 사전 형식 결과를 위한 TypedDict 계약 |
| `PageLabelInfo` / `PageLabelSpec` | 정규화된 페이지 레이블 출력／setter 입력 계약 |
| `DocumentMetadata` / `MetadataUpdate` / `MetadataProbe` | metadata 출력／부분 업데이트／빠른 probe 계약 |
| `OcrEngine` / `OcrWord` | 재사용 가능한 순수Rust PP-OCR 엔진과 위치 결과 계약 |
| `OcrRotation` / `WordEntry` / `BlockEntry` / `FormFieldType` | runtime에서 import 가능한 OCR 회전·tuple·literal 형식 별칭 |
| `TableFinder` / `Table` / `TableDiagnostics` | 독립 보관되는 표 좌표, 셀 텍스트(병합 연속 위치는`None`), strategy와 confidence 근거 |
| `PdfError` / `PasswordError` / `OcrError` / `DocumentClosedError` / `EncryptedDocumentError` / `StalePageError` | 예외 계층（ValueError 호환 기반） |
| `Pixmap` | 불변 RGBA8 픽셀: `samples` / `width` / `height` / `stride` / `n` / `tobytes()`; cp314t에서는 읽기 전용 zero-copy `memoryview()`도 지원 |
| `PylopdfWarning` | 인터프리터 경고（글꼴 해석, 이미지 디코딩） |

`TypedDict` 계약은 정적 타입에만 영향을 주며 값은 기존과 같은 일반 pymupdf 형식의
사전입니다. `LinkInfo`에는 `kind`와 `from`이 필수이고 대상별 키는 선택 사항입니다.
`PageLabelSpec`에는 `startpage`가 필요하며 `style`, `prefix`, `firstpagenum`의
runtime 기본값은 바뀌지 않습니다.
