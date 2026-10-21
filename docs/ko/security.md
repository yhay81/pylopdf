---
title: 보안
description: 지원 버전, 비공개 취약점 보고, pylopdf로 신뢰할 수 없는 PDF를 처리할 때의 지침입니다.
---

# 보안

이 페이지에서는 취약점을 보고하는 방법과 pylopdf로 신뢰할 수 없는 PDF를
처리하는 방법을 설명합니다. 문서를 열 때 쓸 리소스 정책, 거부 시 전달되는 오류
코드, 개별 API의 상한, 내부 guard의 동작을 다룹니다. PyPI의 최신 릴리스만 보안
수정을 받습니다.

## 취약점 보고 { #report-a-vulnerability }

[GitHub Security Advisories](https://github.com/yhay81/pylopdf/security/advisories/new)를
통해 비공개로 보고하세요. 공개 Issue를 만들지 마세요. 첫 답변은 일주일 이내에
받을 수 있습니다.

## 신뢰할 수 없는 PDF 처리 { #untrusted-pdfs }

pylopdf는 Rust로 작성되었고 필수 Python 의존성이 없지만, 악의적인 PDF 입력에는
본질적인 위험이 남아 있습니다.

### 빠른 시작 { #quick-start }

!!! warning "전체 리소스 정책을 사용하세요"
    `pylopdf.open()`에 `limits=pylopdf.DocumentLimits.web()`을 전달하세요.
    메모리가 제한된 Web 및 queue worker에서 사용자 업로드를 처리하기 위한
    보수적인 시작점입니다.

!!! warning "CPU deadline을 강제하고 프로세스를 격리하세요"
    CPU deadline은 Worker, 프로세스 또는 컨테이너 호스트에서 강제하세요. 리소스
    예산은 문서화된 할당과 출력 증가를 막지만, 실행 중인 parser나 interpreter를
    wall-clock 시간으로 중단하지는 않습니다. 신뢰할 수 없는 파일을 일괄 처리할
    때는 가능하면 sandbox나 컨테이너에서 실행하세요.

```python
import pylopdf

try:
    with pylopdf.open(
        "upload.pdf",
        limits=pylopdf.DocumentLimits.web(),
    ) as doc:
        facts = doc.complexity
        preview = doc[0].get_pixmap(dpi=144)
except pylopdf.LimitError as error:
    reject_upload(error.code)
```

예산이나 고정 guard가 작업을 거부하면 pylopdf는 `PdfError`의 subclass인
`LimitError`를 발생시킵니다. `code`는 아래 [코드 표](#limit-error-codes)의 안정적인
식별자이며 같은 값이 `error.args[0]`에도 있으므로, 애플리케이션은 메시지를
파싱하지 않고 코드로 분기할 수 있습니다.

`doc.complexity`는 페이지, object, stream 수와 인코딩된 stream byte, 직접 object의
최대 깊이를 보고합니다. stream을 디코딩하지도 renderer를 호출하지도 않으므로 추출
전에 작업을 분류하는 데 적합합니다. 구조 및 압축 해제 예산은 연 source를
검증하므로, 생성한 출력이 다른 신뢰 경계를 넘어야 할 때는 같은 정책으로 다시
여세요.

임베드된 JavaScript는 실행되지 않으며, 설계상 지원하지 않습니다.

### 리소스 예산 { #document-limits }

`DocumentLimits.web()`은 현재 다음 예산을 독립적으로 적용합니다. 호환성을 위해
`DocumentLimits`의 모든 필드는 기본값이 `None`(무제한)입니다.

| 리소스 | `DocumentLimits` 필드 | `web()` 상한 | 거부 코드 |
|---|---|---:|---|
| 입력 파일 | `max_file_size` | 10 MiB | `file_size` |
| 페이지 | `max_pages` | 200 | `page_count` |
| 간접 object | `max_objects` | 100,000 | `object_count` |
| 이미지 RGBA 추정치를 포함한 개별 디코딩 stream | `max_decompressed_size` | 64 MiB | `decompressed_size` |
| 개별 page-content stream | `max_page_content_size` | 10 MiB | `page_content_size` |
| 누적 디코딩 또는 추정 stream byte | `max_total_decompressed_size` | 128 MiB | `total_decompressed_size` |
| 직접 array/dictionary 중첩 | `max_object_depth` | 64 | `object_depth` |
| 해석한 페이지 전체의 누적 UTF-8 glyph payload | `max_text_size` | 1 MiB | `text_size` |
| 렌더링/추출에 전달되는 전체 PDF snapshot | `max_interpretation_size` | 64 MiB | `interpretation_size` |
| 해석한 페이지 전체의 누적 위치 glyph record | `max_text_glyphs` | 65,536 | `text_glyph_count` |

작업에 다른 예산이 필요하면 `DocumentLimits(...)`를 직접 만드세요. `None`이 아닌
값은 모두 양의 정수여야 합니다. `max_decompressed_size=`는 stream당 예산 하나를
지정하는 호환 축약형으로 남아 있으며 `limits=`와 함께 쓸 수 없습니다.

`max_interpretation_size`는 hayro가 보관된 입력을 처음 읽을 때와, pylopdf가 편집이나
복호화 후 또는 text markup appearance 합성처럼 렌더링용 사본을 준비하려고 현재
상태를 직렬화할 때 적용됩니다. 상한이 있는 writer는 경계를 넘는 쓰기를 거부하며
불완전한 renderer/extractor cache를 설치하지 않습니다. 호환성을 위한 기본값은
`None`이고 `DocumentLimits.web()`은 64 MiB입니다.

`max_text_size`를 설정하면 plain-text 추출은 조립된 UTF-8 크기를 정확히 사전
검사하고, 추출 호출 한 번을 glyph payload 예산의 2배로 제한합니다. 비어 있지 않은
glyph는 payload를 최소 1 byte 차지하며, 추론한 간격과 줄바꿈의 합계는 glyph 수를
넘을 수 없습니다. 이 출력 상한은 내부의 4,096 entry chunk 하나가 아니라 호출
전체에 적용되고, 명시적 sequence는 페이지 수와 4,096 중 큰 값까지의 entry만
허용하므로 페이지 번호를 반복해도 정책을 우회할 수 없습니다. 거부 코드는
`text_size`이며 호환성을 위한 기본값은 `None`입니다.

`max_text_glyphs`는 줄 조립 전에 유지하는 record 수를 제한하므로, 구조화 텍스트가
만들 수 있는 block, line, span, word, `rawdict` glyph 항목의 수도 함께 제한합니다.
같은 페이지의 텍스트 해석과 표 해석은 하나의 누적 admission을 공유하며, 거부된
페이지는 예산을 소비하지 않습니다. 호환성을 위한 기본값은 `None`이고
`DocumentLimits.web()`은 65,536입니다.

`get_text("rawdict")` 옵션은 다른 추출 모드와 같은 해석 glyph cache와 누적
`max_text_size`/`max_text_glyphs` admission을 사용합니다. 여러 표현을 요청해도 cache된
같은 페이지가 다시 계상되지 않습니다. 항목 하나에 여러 문자의 Unicode 매핑이 들어갈
수 있으므로 glyph 수와 UTF-8 payload는 별도의 예산으로 유지됩니다. Rust의 raw
layout buffer와 glyph 텍스트 사본은 fallible하게 확장되며, 네이티브 할당이 거부되면
부분 layout을 반환하지 않고 `PdfError`를 발생시킵니다. 이 정책이 제한하는 것은
유지하는 glyph 데이터이지, Python dictionary의 정확한 byte 크기나 호출자가 나중에
수행하는 JSON/HTML 직렬화가 아닙니다. worker에서 raw layout을 내보낼 때는 그
출력을 별도로 제한하세요.

### 오류 코드 { #limit-error-codes }

`LimitError.code`는 다음 식별자 중 하나입니다. 해제 옵션이 없는 코드는
`DocumentLimits` 설정과 관계없이 적용되는 고정 guard에서 나옵니다.

| 코드 | 제한 대상 |
|---|---|
| `file_size` | 입력 파일 byte(`max_file_size`) |
| `page_count` | 페이지 수(`max_pages`) |
| `object_count` | 간접 object(`max_objects`) |
| `object_depth` | 직접 array/dictionary 중첩(`max_object_depth`). 64단계보다 깊게 중첩된 페이지를 해석할 때도 사용([자세히](#object-depth)) |
| `decompressed_size` | 디코딩된 개별 stream(`max_decompressed_size`) |
| `page_content_size` | 개별 page-content stream(`max_page_content_size`) |
| `total_decompressed_size` | 누적 디코딩 stream byte(`max_total_decompressed_size`) |
| `text_size` | 추출의 누적 UTF-8 glyph payload(`max_text_size`) |
| `text_glyph_count` | 누적 위치 glyph record(`max_text_glyphs`) |
| `interpretation_size` | 렌더링/추출에 전달되는 PDF snapshot(`max_interpretation_size`) |
| `embedded_file_size` | 추가, 쓰기, 읽기하는 첨부 파일 데이터(기본 64 MiB) |
| `embedded_file_input_size` | 첨부 파일 조회/삭제 이름과 추가 시 key/filename/description 텍스트(1 MiB) |
| `form_field_input_size` | `set_form_field()` 호출자의 이름과 값(1 MiB) |
| `form_content_size` | Form XObject로 가져오는 원본 페이지의 디코딩된 content |
| `annotation_input_size` | 호출자의 주석 텍스트와 highlight 사각형 |
| `metadata_input_size` | 메타데이터 쓰기(1 MiB) |
| `toc_input_size` | 목차 쓰기(제목 텍스트) |
| `toc_entry_count` | 목차 읽기와 쓰기(65,536 node/entry, 131,072 edge) |
| `toc_text_size` | 목차의 원본/반환 텍스트(16 MiB) |
| `named_destination_count` | named destination 순회(65,536 entry/node, 131,072 edge) |
| `named_destination_text_size` | named destination key byte(16 MiB) |
| `page_entry_count` | 페이지 수와 4,096 중 큰 값을 넘는 명시적 페이지 sequence |
| `page_label_input_size` | 페이지 레이블 쓰기 |
| `xmp_metadata_size` | `Document.get_pdfa_claim()`이 읽는 디코딩된 XMP(기본 1 MiB) |
| `render_output_size` | `render_pages()`의 누적 인코딩 PNG(기본 512 MiB) |
| `markdown_output_size` | 누적 UTF-8 Markdown 출력(기본 64 MiB) |
| `svg_output_size` | `Page.get_svg_image()`의 UTF-8 출력(기본 64 MiB) |
| `replacement_input_size` | `Page.replace_text()`의 검색/교체/fallback 입력(UTF-8 4,096 byte) |
| `replacement_output_size` | `Page.replace_text()`의 디코딩 content, 인코딩 데이터, 증가분, 출력(기본 64 MiB) |
| `pdf_output_size` | `Document.tobytes()`의 직렬화 출력(기본 512 MiB) |
| `image_input_size` | 인코딩된 `Page.insert_image()` 입력(기본 64 MiB) |
| `image_pixel_count` | 디코딩된 `Page.insert_image()` PNG 픽셀(기본 64,000,000) |
| `image_inventory_count` | `Page.get_images()`가 나열하는 이미지와 Form XObject(4,096) |
| `font_input_size` | OpenType 글꼴 입력(기본 64 MiB) |
| `text_input_size` | `insert_text()`/`insert_textbox()`의 생성 텍스트 입력(기본 1 MiB) |
| `text_line_count` | 생성 텍스트의 물리 줄 또는 줄바꿈 후 줄(4,096) |
| `path_point_count` | `Shape` 하나가 보유하는 경로 점(1,048,576) |
| `search_input_size` | `search_for()`의 검색어(UTF-8 4,096 byte) |
| `search_hit_count` | `search_for()`가 반환하는 결과(기본 4,096) |
| `password_input_size` | 비밀번호(UTF-8 127 byte) |
| `pixmap_output_size` | `Pixmap.tobytes()`의 인코딩 PNG(기본 64 MiB) |
| `ocr_model_size` | `OcrEngine`의 detector, recognizer, 사전 파일 누적 입력(기본 64 MiB) |
| `ocr_dictionary_entries` | `OcrEngine` 인식 사전 항목(65,536) |
| `object_value_depth` | 저수준 object API로 쓰는 값의 중첩(32단계) |
| `stream_data_size` | 저수준 `Stream.read()`가 읽는 디코딩 데이터(기본 64 MiB) |
| `stream_filter_count` | 16개를 넘는 연속 `/Filter` layer([자세히](#filter-chains)) |
| `decompression_unverifiable` | 안전하게 상한을 둘 수 없는 filter chain([자세히](#filter-chains)) |
| `recovery_scan_size` | 추정한 cross-reference 재구성 작업량([자세히](#recovery-scan)) |
| `content_nesting_depth` | 64단계를 넘는 content stream 중첩([자세히](#content-nesting)) |
| `resource_nesting_depth` | 16단계를 넘는 colour space와 function chain, 또는 64단계를 넘는 Type 4 procedure([자세히](#resource-nesting)) |
| `structure_tree_count` | 페이지 삭제와 선택 시의 structure tree 정리([자세히](#structure-tree)) |

## API별 상한 { #api-limits }

개별 API에는 `DocumentLimits` 설정과 관계없이 적용되는 고유한 입력·출력 경계가
있습니다. 해제 옵션이 있는 경우에도 신뢰할 수 있는 작업에만 사용하세요.

| API | 기본 상한 | 해제 | 코드 |
|---|---|---|---|
| 렌더링 | 페이지당 6,400만 픽셀 | — | — |
| `Pixmap.tobytes()` | 인코딩 PNG 64 MiB | `max_size=None` | `pixmap_output_size` |
| `render_pages()` | 누적 인코딩 PNG 512 MiB | `max_size=None` | `render_output_size` |
| `Page.get_svg_image()` | UTF-8 64 MiB | `max_size=None` | `svg_output_size` |
| `Document.to_markdown()`, `Table.to_markdown()` | 누적 UTF-8 64 MiB | `max_size=None` | `markdown_output_size` |
| `Document.tobytes()` | 512 MiB | `max_size=None` | `pdf_output_size` |
| `get_text()`, `to_markdown()`, `render_pages()`, `delete_pages()`, `select()` | 페이지 수와 4,096 중 큰 값만큼의 명시적 entry | — | `page_entry_count` |
| `search_for()` | 검색어 UTF-8 4,096 byte, 결과 4,096건 | `max_hits=None` | `search_input_size`, `search_hit_count` |
| `Page.insert_image()` | 인코딩 64 MiB, 디코딩 PNG 64,000,000 픽셀 | `max_size=None`, `max_pixels=None` | `image_input_size`, `image_pixel_count` |
| OpenType 입력(`insert_text`, `insert_textbox`, `set_form_field`, `set_fallback_font`) | 64 MiB | `max_font_size=None` | `font_input_size` |
| `insert_text()`, `insert_textbox()` | UTF-8 1 MiB, 4,096줄 | `max_text_size=None` | `text_input_size`, `text_line_count` |
| `Shape` | 경로 점 1,048,576개 | — | `path_point_count` |
| `Page.replace_text()` | 입력 UTF-8 4,096 byte, content와 출력 64 MiB | `max_size=None` | `replacement_input_size`, `replacement_output_size` |
| 비밀번호 | UTF-8 127 byte | — | `password_input_size` |
| `Page.get_images()` | 이미지 또는 Form XObject 4,096개 | — | `image_inventory_count` |
| `Page.get_image_info()` | placement 4,096개 | — | — |
| `Document.extract_image()` | 64,000,000 픽셀, payload 64 MiB | — | — |
| 첨부 파일 | 데이터 64 MiB, 이름과 텍스트 1 MiB | `max_size=None` | `embedded_file_size`, `embedded_file_input_size` |
| `Document.get_pdfa_claim()` | 디코딩된 XMP 1 MiB | `max_size=None` | `xmp_metadata_size` |
| `Page.insert_ocr_text_layer()` | 비어 있지 않은 단어 4,096개, UTF-8 1 MiB | — | — |
| 메타데이터 | 텍스트 1 MiB | — | `metadata_input_size` |
| 페이지 레이블 | 4,096 entry/node, 32단계, 텍스트 1 MiB | — | `page_label_input_size` |
| AcroForm field tree | 4,096 entry/node, 8,192 edge, 64단계, 1 MiB, choice 값 4,096개 | — | `form_field_input_size` |
| AcroForm 버튼 | widget 4,096개, state 항목 8,192개, 이름 4,096개, 1 MiB | — | — |
| 주석과 링크 | `/Annots` 항목 4,096개, 텍스트 1 MiB | — | `annotation_input_size` |
| named destination | 65,536 entry/node, 131,072 edge, key 16 MiB, 32단계 | — | `named_destination_count`, `named_destination_text_size` |
| 목차(TOC) | 65,536 node/entry, 131,072 edge, 텍스트 16 MiB, 64단계 | — | `toc_entry_count`, `toc_text_size`, `toc_input_size` |

아래 절에서 각 경계를 자세히 설명합니다.

### 렌더링과 이미지 출력 { #rendering-limits }

- 렌더링은 페이지당 6,400만 픽셀로 제한됩니다.
- `Pixmap.tobytes()`의 인코딩 PNG 출력 기본 상한은 64 MiB입니다. writer는 Python
  `bytes`를 반환하기 전에 경계를 넘는 쓰기를 `pixmap_output_size`로 거부하며,
  `max_size=None`으로 명시적으로 해제할 수 있습니다.
- `render_pages()`는 기본적으로 모든 페이지를 렌더링하고, 명시적 entry는 페이지
  수와 4,096 중 큰 값까지 허용하며, 누적 인코딩 PNG 기본 상한은 512 MiB입니다.
  완료된 병렬 결과는 하나의 atomic 예산을 공유하고 실패 시 부분 list를 반환하지
  않습니다. `max_size=None`으로 명시적으로 해제할 수 있습니다. worker admission은
  추정한 live raster/변환 buffer를 별도로 제한하고 각 worker는 4,096 페이지마다
  hayro cache를 교체하므로, 그 바깥에 애플리케이션 수준의 무제한 병렬 처리를
  덧붙이지 마세요.
- `Page.get_svg_image()`의 UTF-8 출력 기본 상한은 64 MiB이며, PyO3가 Python
  문자열을 만들기 전에 초과 출력을 거부합니다. `max_size=None`으로 명시적으로
  해제할 수 있습니다. hayro-svg 0.8은 pylopdf가 이 경계를 적용하기 전에 내부 Rust
  문자열 하나를 만듭니다.
- `Page.get_images()`는 나열하는 이미지나 Form XObject가 4,096개에 이르면
  `image_inventory_count`로, `Page.get_image_info()`는 placement 4,096개에서
  멈춥니다. `Document.extract_image()`는 64,000,000 픽셀과 64 MiB payload 상한,
  content 및 colour space guard 아래에서 이미지 하나를 디코딩하며, Flate로 감싼
  JPEG passthrough도 그 예산에서 압축 해제를 멈춥니다.

### 페이지 batch { #page-batches }

페이지 batch는 한 번 얻은 페이지 수로 entry를 검증합니다. `pages=None`과
`insert_pdf()`의 전체 범위는 페이지 수와 관계없이 모든 페이지를 대상으로 합니다.
명시적 sequence는 같은 페이지를 반복할 수 있지만, 페이지 수와 4,096 중 큰 값을
넘는 첫 entry에서 `page_entry_count`로 materialization을 멈추므로 반복해도 문서
전체를 한 번 처리하는 비용을 넘지 않습니다. 텍스트 추출과 렌더링은 호출 단위 hayro
cache를 4,096 entry마다 교체하고, `max_size`, `max_text_size` 등의 출력 예산은 호출
전체에 적용됩니다. core를 직접 호출해도 entry 검사를 반복하며, core의 페이지
가져오기는 변경 전에 중복된 원본 페이지를 거부합니다.

- `delete_pages()`와 `select()`는 Python과 Rust 모두에서 호출당 페이지 수와 4,096
  중 큰 값까지의 entry를 허용하므로, 같은 페이지를 반복해도 선택 결과가 문서보다
  커지지 않습니다. iterable 읽기는 graph를 변경하기 전에 상한을 넘는 첫 entry에서
  멈추고 `page_entry_count`를 발생시킵니다. `insert_pdf()`는 원본의 어떤 전체
  범위든 가져옵니다. 빈 삭제는 cache, generation, 기존 `Page` view를 유지합니다.
- 페이지 삭제와 선택은 pruning 전에 제외된 페이지를 가리키는 navigation, 그
  페이지의 AcroForm widget과 field, 그 페이지에만 있던 tagged PDF의 structure tree
  content를 정리합니다. 이 작업은 안전한 redaction이 아닙니다. 임의의 사용자 정의
  참조, 첨부 파일, 메타데이터, content의 다른 사본은 별도로 검사하고 제거해야
  합니다. structure tree 순회에는 별도의 [상한](#structure-tree)이 있습니다.

### 텍스트 출력 { #text-output-limits }

- `search_for()`의 검색어는 UTF-8 4,096 byte까지이며 반환 geometry는 기본 4,096건으로
  제한됩니다. Python은 PyO3 복사 전에 초과 검색어를 거부하고 Rust 경계도 두 상한을
  다시 검사합니다. 신뢰할 수 있는 결과 집합은 `max_hits=None`으로 명시적으로 해제할
  수 있습니다. 거부 코드는 `search_input_size` 또는 `search_hit_count`이며 부분
  list는 반환하지 않습니다.
- `Document.to_markdown()`은 기본적으로 모든 페이지를 변환하고, 명시적 entry는
  페이지 수와 4,096 중 큰 값까지 허용하며, 누적 UTF-8 출력 기본 상한은 64 MiB입니다.
  제목 크기 집계 pass와 렌더링 pass 모두에서 한 번에 한 페이지의 해석된 layout, 표,
  단어만 유지합니다. 페이지 출력을 조립하기 전에 각 표에 남은 누적 예산을
  전달합니다. 제목, 단락, 목록, 표는 보관할 때 예산에 계상되며, 전체 크기가 허용된
  뒤 마지막 페이지를 선형으로 조립합니다. `Table.to_markdown()`을 직접 호출할 때도
  같은 기본 상한을 쓰며, 병합 셀 확장을 포함한 escape 후의 정확한 UTF-8 크기를 사전
  검사합니다. 상한을 넘는 변환은 부분 문자열을 반환하지 않으며 `max_size=None`으로
  명시적으로 해제할 수 있습니다.

### 텍스트, 이미지, 그리기 입력 { #insertion-limits }

- `Page.insert_image()`는 인코딩된 JPEG/PNG 입력을 기본 64 MiB, 디코딩된 PNG 입력을
  기본 64,000,000 픽셀로 제한합니다. 파일 이름 입력은 core 직접 호출과 같은 상한
  있는 Rust 경계에서 GIL을 해제한 채 읽으며, PNG 크기는 디코딩 저장 공간을 할당하기
  전에 검사합니다. 신뢰할 수 있는 작업은 `max_size=None`과 `max_pixels=None`으로
  명시적으로 해제할 수 있습니다.
- `insert_text`, `insert_textbox`, `set_form_field`, `set_fallback_font`에 명시적으로
  또는 자동으로 선택되는 OpenType 입력은 기본 64 MiB로 제한됩니다. buffer 입력은
  PyO3 복사 전에 거부하고, 파일 이름 입력은 GIL을 해제한 상한 있는 Rust 경로에서
  읽습니다. 신뢰할 수 있는 작업은 `max_font_size=None`으로 명시적으로 해제할 수
  있습니다.
- `insert_text()`와 `insert_textbox()`의 생성 텍스트 입력은 기본 UTF-8 1 MiB,
  물리 줄 또는 줄바꿈 후 최종 줄 4,096줄로 제한됩니다. `insert_text()`는
  `str.splitlines()`가 나누는 줄, 또는 list 항목 하나를 한 줄로 셉니다. Python은 줄을
  나누고 PyO3로 복사하기 전에 물리 입력을 검사하고, Rust 경계는 이를 반복하면서
  변경 전에 줄바꿈 layout을 멈추며, textbox의 tab 확장은 확장된 문자열을 할당하기
  전에 사전 검사합니다. 신뢰할 수 있는 삽입 입력은 `max_text_size=None`으로
  명시적으로 해제할 수 있으며 거부 코드는 `text_input_size` 또는
  `text_line_count`입니다. AcroForm 텍스트와 choice appearance는 고정된 4,096줄
  layout 상한을 유지합니다. UAX #14와 grapheme index, 줄 collection, 보관하는 줄
  텍스트는 fallible하게 확장되므로 할당 거부는 부분 layout도 문서 편집도 노출하지
  않습니다. pylopdf가 소유한 정규화, grapheme 참조, shaped line/glyph collection,
  WinAnsi 출력, 입력에서 파생된 텍스트 operator 확장도 fallible하며, Standard 14
  폭 검사는 인코딩된 텍스트 사본을 할당하지 않습니다. 할당 거부는 `PdfError`를
  반환하고, 삽입은 문서를 변경하지 않으며 form 채우기는 원자적으로 rollback합니다.
- `Shape.insert_text()`와 `Shape.insert_textbox()`는 모든 호출에 같은 텍스트와 글꼴
  경계를 적용합니다. 도형 하나는 확정될 때까지 경로 점을 최대 1,048,576개
  보유하며, 점을 추가할 때와 Rust 경계에서 다시 검사하고 거부 코드는
  `path_point_count`입니다. 확정은 페이지를 바꾸기 전에 그리기를 생성하고, 가져오기를
  계획하고, 페이지의 그리기 대상을 준비하므로 거부되면 문서는 바뀌지 않습니다.
- `Page.replace_text()`는 검색어, 교체 문자열, fallback의 합계를 UTF-8 4,096 byte로
  제한하고, 디코딩한 페이지 content, 글꼴 인코딩 데이터, 교체 증가분, 최종 stream
  출력에 기본 64 MiB 상한을 적용합니다. 확정 전에 페이지 전용 stream을 준비하므로
  복사된 페이지가 공유 content를 편집할 수 없고, 일치 항목이 없거나 오류가 난
  호출은 문서와 cache를 보존합니다. 호출자 텍스트는 사전 검사를 위해 전부
  인코딩하지 않고 PyO3 복사 전에 점진적으로 셉니다. 신뢰할 수 있는 입력은
  `max_size=None`으로 명시적으로 해제할 수 있습니다.
- 그리기 삽입은 cache 무효화, 입력 디코딩, 종속 object 생성 전에 페이지
  `/Contents`의 raw array와 참조 chain을 사전 검사합니다. raw array는 4,096 entry,
  chain은 32단계, 최종 array는 한 번만 추가되는 `q`/`Q` 격리 쌍을 포함해 stream 참조
  4,096개로 제한됩니다. 실패해도 문서는 변경되지 않습니다. 페이지에 대한 첫 삽입은
  기존 content를 한 번 디코딩하고 hayro의 content parser가 읽는 방식대로 `q`와
  `Q` operator를 세어, 격리 쌍에 content의 짝 없는 `Q`보다 하나 많은 `q`와 content가
  열어 둔 `q`보다 하나 많은 `Q`를 넣습니다. 해석과 마찬가지로 이 계산은 로드 시
  적용한 상한 외의 압축 해제 상한을 다시 적용하지 않으며, 디코딩에 실패한 stream은
  빈 것으로 셉니다.
- `Page.insert_ocr_text_layer()`는 비어 있지 않은 단어 4,096개 또는 UTF-8 텍스트
  합계 1 MiB를 넘으면 iterable materialization을 멈춥니다. core 직접 호출도 같은
  상한을 적용하고, CID 할당은 고유 문자 65,535개에 이르기 전에 멈추며, 입력에서
  파생된 buffer는 모두 PDF 변경 전에 준비합니다.
- 열기, authenticate, 빠른 메타데이터 probe, AES-256 출력에 쓰는 비밀번호는 PyO3
  복사나 비밀번호 KDF 작업 전에 UTF-8 127 byte로 제한됩니다. Rust 직접 호출도 이
  경계를 반복합니다. 거부 코드는 `password_input_size`이며, 저장 거부는 문서 변경이나
  출력 생성 전에 일어납니다.

### 폼, 주석, navigation { #structure-limits }

- AcroForm field tree 읽기는 4,096 entry/node, 8,192 edge, 64단계, 인코딩·디코딩·반환
  이름/값 1 MiB, 또는 choice 값 4,096개를 넘으면 부분 결과를 반환하지 않고
  거부합니다. 참조 cycle은 한 번만 방문하고, 상속된 값은 반환하는 leaf마다 예산에
  계상하며, 채우기는 같은 tree 상한과 호출자 이름/값 입력 1 MiB를 원자적으로
  적용합니다. 호출자 입력은 글꼴 탐색, 버튼 조회, 파일 읽기 전에
  `form_field_input_size`로 멈춥니다.
- `insert_pdf()`는 받는 문서를 바꾸기 전에 최상위 field 이름을 1 MiB 이름 예산
  안에서 비교하고, 부분 범위라면 같은 node, edge, widget, 깊이 상한으로 원본 field
  tree를 순회합니다. 전체 가져오기는 field tree를 순회하지 않고 복사합니다. 공유
  여부를 판단하기 위한 기본 리소스 두 개의 비교는 object 4,096개와 1 MiB까지
  읽으며, 그보다 큰 쌍은 대신 이름을 바꿉니다.
- AcroForm 버튼 field는 widget 4,096개, normal appearance state 항목 8,192개, 고유
  반환 state 이름 4,096개, 또는 인코딩/반환 state 이름 텍스트 1 MiB를 넘으면
  거부합니다. 채우기는 누락된 `Off`와 on state key를 변경 전에 예산에 포함합니다.
- 주석과 링크 읽기는 `/Annots` 항목 4,096개 또는 호출당 인코딩/반환 메타데이터
  텍스트 합계 1 MiB를 넘으면 부분 결과를 반환하지 않고 거부합니다. 추가는 종속
  object를 만들거나 cache를 무효화하기 전에 같은 페이지 상한, 생성 subtype과
  Contents/URI 입력 합계 1 MiB, highlight 사각형 4,096개를 적용합니다. 호출자
  텍스트는 PyO3 복사나 사각형 순회 전에 `annotation_input_size`로 거부되며,
  highlight 순회는 4,097번째 항목에서 멈춥니다.
- named destination 조회는 참조 cycle을 한 번만 방문하며, 65,536 entry/node 또는
  131,072 edge를 넘으면 `named_destination_count`로, key byte가 16 MiB를 넘으면
  `named_destination_text_size`로, 32단계를 넘으면 순회를 거부합니다. 중간에 잘린
  tree를 미해결로 조용히 보고하지 않습니다. `get_outline()`, `Page.get_links()`,
  `insert_pdf()`의 링크 가져오기, 페이지 삭제와 선택 시의 name tree와 구형식
  `/Dests` 재작성은 이 상한을 공유하므로, 해석되는 올바른 형식의 tree는 모두 정리와
  가져오기도 가능합니다. 재작성은 유지하는 destination 값을 직접 object
  1,048,576개(entry당 16개)와 텍스트 16 MiB 안에서 복사합니다. 조회한 destination
  이름은 순회 중인 구조의 예산에 계상됩니다. 목차는 TOC 텍스트, name tree는 key
  텍스트, 링크는 페이지당 1 MiB의 링크 메타데이터 예산을 씁니다.
  `Page.get_links()`는 첫 named 조회 때 tree를 index로 만들고 다음 편집까지 그 index와
  페이지 index를 재사용하므로, 모든 페이지를 읽어도 선형으로 유지됩니다. 유지하는
  index는 같은 entry와 key 텍스트 상한 안에서 서로 다른 key를 한 번씩만 보관하며,
  거부된 tree는 cache하지 않습니다.
- 목차(TOC) 읽기는 반복적인 outline 순회를 사용하고, 참조 cycle을 한 번만 방문하며,
  GIL을 해제합니다. 65,536 node/entry 또는 131,072 edge를 넘으면
  `toc_entry_count`로, 원본/반환 텍스트가 16 MiB를 넘으면 `toc_text_size`로, 64단계
  또는 destination 간접 참조 32단계를 넘으면 부분 결과를 반환하지 않고 거부합니다.
  쓰기는 PyO3 복사와 변경 전에 같은 entry, 깊이, 제목 텍스트 상한을
  `toc_entry_count`와 `toc_input_size`로 적용합니다. 페이지 삭제와 선택도 목차
  상한을 공유하므로, 쓴 목차는 계속 편집할 수 있습니다.
- 페이지 레이블 number tree 읽기는 4,096 entry/node, 32단계, 인코딩 또는 디코딩된
  style/prefix 텍스트 1 MiB를 넘으면 부분 결과를 반환하지 않고 거부합니다. 참조
  cycle은 한 번만 방문하며, 쓰기는 PyO3 복사 전에 같은 entry/텍스트 경계를
  `page_label_input_size`로 적용합니다.

### 메타데이터, 첨부 파일, XMP { #metadata-limits }

- `Document.metadata`는 표준 Info 필드 8개만 디코딩하며, 원본 또는 반환 텍스트
  합계가 1 MiB를 넘으면 거부합니다. 사용자 정의 항목은 Python 출력이 되지 않습니다.
  `peek_metadata(max_file_size=)`는 파싱 전에 경로 또는 byte 입력을 거부할 수 있고
  반환하는 표준 텍스트도 제한합니다. `None`은 무제한 입력 기본값을 유지합니다.
  쓰기는 PyO3 복사 전에 원본/인코딩 텍스트 합계 1 MiB를 `metadata_input_size`로 사전
  검사하고 원자적으로 적용합니다.
- `doc.attachments.add()`와 `Attachment.write()`는 PyO3 복사 전에 64 MiB를 넘는 입력을
  거부하고, `Attachment.read()`는 디코딩하는 모든 filter layer에 같은 기본 상한을
  적용합니다. 크기를 알고 있는 대용량 첨부 파일에는 `max_size=`를 늘리고,
  `max_size=None`은 무제한 입력 또는 materialization을 명시적으로 허용합니다. 첨부
  파일 name tree는 4,096 entry/node, 32단계, 인코딩 또는 디코딩된 이름 1 MiB를 넘으면
  거부합니다. 호출자의 조회/삭제 이름과 추가 시 key/filename/description 텍스트 합계는
  순회나 데이터 복사 전에 `embedded_file_input_size`로 1 MiB에서 멈춥니다. 편집은
  복제하기 전에 inline FileSpec이 직접 object 4,096개, 32단계, 직접
  string/name/stream 데이터 1 MiB를 넘지 않는지 검증하고, rollback을 위해 문서 전체를
  복제하는 대신 Catalog 쓰기 대상을 사전 검사합니다.
- `Document.get_pdfa_claim()`은 모든 filter layer에 XMP 디코딩 크기 기본 상한
  1 MiB를 적용합니다. 크기를 알고 있는 대용량 packet에는 `max_size=`를 늘리고,
  `max_size=None`은 무제한 materialization을 명시적으로 허용합니다.

### 파일 저장 { #saving }

- `Document.tobytes()`는 일반, object/xref stream, 암호화 출력 모두에 기본 512 MiB
  직렬화 출력 상한을 적용합니다. Rust writer는 Python `bytes`로 변환하기 전에
  경계를 넘는 쓰기를 거부하며, `max_size=None`으로 명시적으로 해제할 수 있습니다.
  `save()`는 대상 디렉터리에 안전하게 만든 형제 파일로 stream한 뒤, 쓰기가 완전히
  끝난 경우에만 요청한 경로를 원자적으로 교체하므로 직렬화나 교체에 실패해도 기존
  파일이 보존됩니다. 이 작업은 메모리 내 출력 예산의 대상이 아닙니다. `garbage`,
  `deflate`, `use_objstms` 같은 저장 옵션은 이후 I/O가 실패해도 문서화된 변경
  semantics를 유지합니다.
- `Pixmap.save()`는 대상 디렉터리 안에 예측할 수 없는 이름으로 배타적으로 만든 형제
  파일에 PNG 인코딩을 직접 stream하고, 쓰기가 완전히 성공한 뒤에만 요청한 경로를
  원자적으로 교체합니다. 완성된 PNG를 메모리에 하나 더 보관하지 않습니다. 교체에
  실패하면 기존 출력을 보존하고 임시 파일을 삭제합니다.
- Windows에서는 두 저장 작업 모두 기존 일반 파일의 DACL과 상속 보호 설정을
  유지합니다. 데이터를 쓰기 전에 임시 파일에 접근 제한을 적용하며, 보안 설명자를
  복사할 수 없으면 저장을 거부합니다. 실패해도 읽기 전용이거나 잠긴 대상은 변경되지
  않고, 실패한 임시 출력은 삭제됩니다. `Document.save()`는 상대 경로와 Unicode
  이름을 포함한 긴 Windows 경로를 내부적으로 정규화하므로 시스템 전체의 긴 경로
  정책을 활성화할 필요가 없습니다.

## 손상된 파일과 복구된 파일 { #repaired-files }

관대한 열기는 lopdf의 cross-reference 복구를 사용합니다. `startxref`, `/Prev`,
`/XRefStm` offset이 64 byte 이내로 어긋나면 근처의 `xref` keyword로 옮깁니다. 표준
해석이 여전히 실패하면 lopdf는 줄 시작의 object header와 `/Root`가 존재하는 가장
최근의 classic `trailer`로 table을 재구성합니다. 이후 revision의 object가 우선하지만
손상된 영역의 object는 누락될 수 있습니다. 해석한 table의 entry가 catalog나 page
tree의 root node를 가리키지 못하는 경우(table 앞에서 byte가 사라진 파일 등)에도 같은
방법으로 재구성하며, 재구성한 table이 page tree에 도달하면 그 table을 사용합니다.
classic trailer가 없는 손상된 xref stream 파일은 거부됩니다.

어느 복구든 `PylopdfWarning`을 발생시키고 `doc.is_repaired`(메타데이터 probe에서는
`repaired` 키)를 설정하며, 렌더링과 추출은 저장 시 정규화된 xref 데이터와 함께
기록되는 복구된 object graph를 사용합니다. 복구된 입력을 받아들일 수 없는 호출자는
`is_repaired`인 문서를 거부할 수 있습니다. 19 byte xref entry나 모호하지 않은 object
경계에서 보정한 stream `/Length`처럼 선언된 구조를 유지하는 관대한 처리는 보고하지
않습니다. 복구된 문서에도 `DocumentLimits`가 그대로 적용됩니다. 재구성 비용은 사전
scan으로 제한됩니다. [cross-reference 복구 scan](#recovery-scan)을 참고하세요.

## guard의 동작 방식 { #how-the-guards-work }

이 절은 위 코드 뒤에 있는 고정 guard의 정확한 동작을 따져 봐야 하는 독자를 위한
설명입니다. 네이티브와 Pyodide CI는 같은 악의적 입력 회귀 계약을 공유하며, 정기
Atheris fuzzing은 손상된 xref, cycle, 깊은 object, 깨진 stream, 압축 폭탄을 seed로
사용합니다.

### page tree walker { #page-tree-walker }

??? note "page tree indexing 세부 사항"
    page count와 lookup, complexity, 렌더링 snapshot, 목차와 링크, 주석, Form
    가져오기, 페이지 구조 편집을 포함한 모든 공개 page indexing은 하나의 반복적인
    page tree walker를 사용합니다. 재사용된 Page/Pages object와 cycle, 32개 참조를
    넘는 간접 `/Kids` chain, 256단계를 넘는 내부 tree, 간접 object 수를 넘는 edge
    수를 거부합니다. `max_pages` 순회는 상한을 넘는 첫 페이지에서 멈춥니다. 페이지
    정책 없이 열면 호환성이 유지되며, 이 page tree 검사는 페이지를 indexing하는 첫
    작업까지 미뤄집니다.

    page count와 lookup은 한 번의 순회로 만든 page index를 재사용하므로 모든
    페이지에 접근해도 선형으로 유지됩니다. 회전, page box, 그리기, 주석 편집은 page
    tree를 바꿀 수 없으므로 index를 유지합니다. 각 편집은 사용하지 않은 object
    번호에만 object를 추가하고 순회가 읽은 모든 entry를 그대로 두며, 이를 보일 수
    없으면 index를 버립니다. 그 밖의 편집은 모두 index를 버리고 다음 접근에서 다시
    만듭니다. 실패한 순회는 cache하지 않으므로 잘못된 tree는 접근할 때마다 예외를
    발생시킵니다.

### 할당 실패 { #allocation-failures }

많은 내부 buffer는 fallible하게 확장됩니다. allocator가 메모리를 거부하면 pylopdf는
중단하는 대신 `PdfError`를 발생시키고 부분 결과를 반환하지 않습니다. 예를 들어 직접
깊이 검증과 complexity 검사는 반복 순회 stack을 fallible하게 확장하므로, 할당 거부는
부분 정보 없이 `PdfError`를 발생시킵니다. 이 동작이 적용되는 다른 곳은 위의 API별
절에 적혀 있습니다.

### filter chain { #filter-chains }

안전하게 상한을 둘 수 없는 filter chain은 낙관적으로 디코딩하지 않고 거부합니다.
pylopdf가 소유한 상한 있는 lopdf 디코딩 경로는 filter 목록을 materialize하기 전에
16개를 넘는 연속 `/Filter` layer도 거부합니다.

### content stream 중첩 { #content-nesting }

hayro는 content stream의 array와 dictionary를 64단계까지만 읽고 더 깊은 operand를
조용히 생략합니다. pylopdf는 이 잘림을 `content_nesting_depth` 거부로 바꿉니다.

??? note "content stream 검사가 읽는 것"
    텍스트 추출, layout, 표, 검색, 그리기, 이미지 읽기, 렌더링(PNG, pixmap, SVG),
    Markdown 변환, 이미지 압축이 페이지를 해석하기 전에 pylopdf는 그 페이지가
    도달할 수 있는 content stream을 디코딩합니다. 대상은 `/Contents`, 리소스의
    Form XObject, 주석과 위젯의 appearance(check box와 radio button은 모든
    상태이며, hayro는 `/AS`가 고른 하나를 그립니다), soft-mask group, tiling
    pattern, Type 3 glyph procedure입니다. array나 dictionary가 64단계보다 깊게
    중첩되거나, stream object가 포함되어 있거나, 검사할 수 없는 inline image 경계가
    숨어 있으면 그 페이지를 `content_nesting_depth`로 거부합니다.

    이 한도는 고정이며 `DocumentLimits` 설정과 관계없이 적용되고, 다른 페이지는
    계속 사용할 수 있습니다. 각 페이지는 문서 snapshot마다 한 번 검사되고, 편집하면
    결과가 지워집니다. 검사는 content가 어떤 이름을 쓰는지 분석하지 않으므로, 어떤
    operator도 그리지 않더라도 페이지의 리소스 dictionary에 나열된 항목은 검사
    대상입니다.

### colour space, shading, function { #resource-nesting }

hayro는 colour space, shading, function을 깊이 제한 없는 재귀로 구성하므로 충분히
깊은 chain은 stack을 넘쳐 프로세스를 종료시킬 수 있습니다. pylopdf는 hayro가 이를
구성하기 전에 그런 페이지를 `resource_nesting_depth`로 거부합니다. 이 상한은 시간이
아니라 stack 사용량을 제한하므로, 호스트에서 CPU deadline을 계속 강제하세요.

??? note "리소스 검사가 측정하는 것"
    hayro는 colour space, shading, function을 content stream과 같은 종류의 깊이
    제한 없는 재귀로 구성합니다. Indexed나 Pattern colour space는 base로,
    Separation이나 DeviceN colour space는 alternate space와 tint transform으로,
    ICCBased colour space는 파싱할 수 없는 profile의 `/Alternate`로, stitching
    function은 `/Functions`의 모든 항목으로 구성되고, Type 4 function은 `{ }`
    procedure를 재귀로 파싱합니다. 서로를 참조하는 약 2만 개의 간접 object나 30만
    겹의 중괄호는 stack을 넘쳐 프로세스를 종료시킬 수 있습니다.

    pylopdf는 페이지를 해석하기 전에 그 페이지의 리소스 dictionary에 있는
    `/ColorSpace`와 `/Shading` 항목, shading pattern, image XObject, soft-mask group,
    graphics state와 soft mask의 전달 함수(`/TR`, `/TR2`)가 도달하는 것을 측정하고,
    colour space와 function의 chain이 16단계보다 깊거나 Type 4 function의
    procedure가 64단계보다 깊게 중첩되면 그 페이지를 `resource_nesting_depth`로
    거부합니다. chain에는 colour space array, function, profile stream이 포함됩니다.
    hayro가 스스로 순환을 끊기 때문에 자기 참조나 짧은 고리는 그대로 렌더링되지만,
    서로 도달하는 object가 16개를 넘는 묶음은 거부되고, 공유되는 하위 graph는 한
    번만 측정되므로 object는 몇 개의 항목이 참조하든 한 번만 읽힙니다.

    이 한도는 고정이며 `DocumentLimits` 설정과 관계없이 적용되고, 다른 페이지는
    계속 사용할 수 있습니다. 제한하는 것은 stack 사용량이지 시간이 아닙니다. 각
    단계에서 같은 function을 두 번 참조하거나 Separation을 중첩하는 것처럼 한도
    안에 있는 graph에서도 hayro가 지수 시간의 CPU를 쓸 수 있으므로, 호스트에서 CPU
    deadline을 계속 강제하세요.

### object 중첩 깊이 { #object-depth }

hayro는 그것을 담은 object를 포함해 64단계보다 깊게 중첩된 array나 dictionary를
읽지 않고 그 object 전체를 없는 것으로 취급하며, lopdf는 100단계까지 읽습니다. page
tree에서 상속하는 속성을 포함한 페이지 dictionary가 이보다 깊게 중첩되면, 그 페이지는
작업이 해석할 때마다 `object_depth`로 거부되고 다른 페이지는 계속 사용할 수 있습니다.
trailer, catalog, page tree의 root node가 이 깊이로 중첩되어 있어도 문서 렌더링을
막지는 않습니다. 렌더링과 추출은 지나치게 깊은 항목을 뺀 사본을 읽고, 편집하고
저장하는 문서에는 그 항목이 남습니다. 그 밖에 이 깊이로 중첩된 object는 hayro에서와
마찬가지로 없는 것으로 읽힙니다. `DocumentLimits.web()`은 이런 문서를 열 때 이미
`object_depth`로 거부합니다.

### structure tree 정리 { #structure-tree }

페이지 삭제와 선택이 제외된 페이지의 structure tree content를 제거할 때, structure
tree 순회는 element와 lookup tree node 1,048,576개, kid와 entry 4,194,304개, element
256단계, 또는 `/ParentTree`와 `/IDTree` 32단계에서 문서를 바꾸기 전에
`structure_tree_count`로 멈춥니다.

### renderer panic { #renderer-panics }

hayro는 성분 수가 맞지 않는 값을 돌려주는 Separation이나 DeviceN의 tint transform,
Pattern colour space의 shading처럼 일부 잘못된 색 데이터에서 panic합니다
([LaurenzV/hayro#1546](https://github.com/LaurenzV/hayro/issues/1546)). hayro가
페이지를 해석하게 하는 모든 작업은 이런 panic을 잡아 PyO3의 `PanicException` 대신
`PdfError`를 발생시킵니다. `PanicException`은 `BaseException`에서 파생되어
`except Exception`을 빠져나가기 때문입니다. renderer 상태는 편집할 때와 같이
버려지므로 이후 호출은 처음부터 다시 시작하고 문서는 계속 사용할 수 있습니다. panic
메시지는 여전히 Rust가 표준 오류 출력에 씁니다.

### cross-reference 복구 scan { #recovery-scan }

lopdf의 재구성은 뒤에 `endstream`이 없는 `stream` keyword마다 입력의 나머지를 다시
검색합니다. pylopdf는 관대한 열기와 메타데이터 probe 전에 이 작업량을 선형 시간으로
추정하고, 입력 1 byte당 64 byte에 64 MiB를 더한 값을 넘는 입력을
`recovery_scan_size`로 거부하여 악의적인 입력이 복구를 이차 시간으로 만들지 못하게
합니다. 실제 손상 파일에는 그 위치에 잘린 stream이 많아야 하나 있습니다.

## 의존성 감사 { #dependency-auditing }

CI는 push할 때마다 RustSec 취약점 데이터베이스를 기준으로 Rust 의존성 트리에
`cargo audit`를 실행합니다.

저장소의 정책 원본은
[`SECURITY.md`](https://github.com/yhay81/pylopdf/blob/main/SECURITY.md)입니다.
