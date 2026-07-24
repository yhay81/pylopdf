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
| `to_markdown(pages=None)` | Markdown 변환（제목, CJK 연결, 강조, 목록） |
| `render_page(...)` / `render_pages(..., workers=)` / `render_page_svg(...)` | PNG, 순서 보장 병렬 PNG 묶음, SVG |
| `set_fallback_font(font, kind=, index=)` | 임베드되지 않은 글꼴의 CJK 대체 글꼴 |
| `select` / `delete_page(s)` / `insert_pdf` / `new_page` / `copy_page` | 페이지 관리 |
| `get_toc()` / `set_toc(toc)` | 목차（페이지는 1부터） |
| `get_page_labels()` / `set_page_labels(labels)` | 페이지 레이블 범위 |
| `get_form_fields()` / `set_form_field(name, value)` | AcroForm 목록과 입력（NeedAppearances） |
| `embfile_add / embfile_names / embfile_get / embfile_del` | 첨부 파일 |
| `get_pdfa_claim()` | XMP PDF/A 선언 읽기（검증 아님） |
| `save(...)` / `tobytes(...)` | `garbage=` `deflate=` `object_streams=` `user_pw=` `owner_pw=` `permissions=` |
| `close()` | `with`로도 호출 |

## Page { #page }

| 멤버 | 용도 |
|---|---|
| `number` / `parent` / `get_label()` | 식별 정보와 표시 레이블 |
| `get_text(option)` / `search_for(needle)` | 추출과 대소문자 구분 없는 검색 |
| `find_tables(strategy="lines")` | 벡터 테두리와 병합 셀. `"text"`로 테두리 없는 표 감지 |
| `to_markdown()` | 한 페이지의 Markdown |
| `get_images()` | 그려진 이미지（`bbox`, JPEG 패스스루 / PNG） |
| `get_pixmap(scale=, dpi=, background=, clip=)` / `render(...)` / `render_svg()` | 렌더링. `clip`은 표시 좌표 사용 |
| `rotation` / `set_rotation(deg)` | 표시 회전 |
| `mediabox` / `cropbox` / `rect` / `set_mediabox` / `set_cropbox` | 페이지 박스 |
| `insert_image(rect, filename= / stream=, keep_proportion=, overlay=)` | JPEG/PNG 그리기 |
| `show_pdf_page(rect, src, pno=, keep_proportion=, overlay=)` | 다른 PDF 페이지를 벡터로 겹치기 |
| `insert_text(point, text, fontsize=, fontname=, color=)` | Standard-14 텍스트（WinAnsi） |
| `insert_ocr_text_layer(words)` | OCR 비가시 텍스트 레이어（검색 가능한 PDF） |
| `replace_text(search, replacement, default_char=)` | 단순 인코딩 텍스트 교체 |
| `annots()` / `add_highlight_annot(...)` / `add_link_annot(rect, uri)` | 주석 |

## 모듈 수준 { #module-level }

| 이름 | 용도 |
|---|---|
| `peek_metadata(path_or_stream, password=)` | 전체 파싱 없이 메타데이터와 페이지 수를 빠르게 조회 |
| `Permissions` | 암호화 권한 플래그（IntFlag） |
| `Rect` | `width` / `height`가 있는 사각형 NamedTuple |
| `TableFinder` / `Table` | 독립 보관되는 테두리 표 좌표와 셀 텍스트（병합 연속 위치는 `None`） |
| `PdfError` / `PasswordError` / `DocumentClosedError` / `EncryptedDocumentError` / `StalePageError` | 예외 계층（ValueError 호환 기반） |
| `Pixmap` | RGBA8 픽셀:`samples` / `width` / `height` / `stride` / `n` / `tobytes()` |
| `PylopdfWarning` | 인터프리터 경고（글꼴 해석, 이미지 디코딩） |
