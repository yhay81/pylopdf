---
title: 보안
description: 지원 버전, 비공개 취약점 보고, pylopdf로 신뢰할 수 없는 PDF를 안전하게 처리하는 방법을 설명합니다.
---

# 보안

## 지원 버전 { #supported-versions }

PyPI의 최신 릴리스만 보안 수정을 받습니다.

## 취약점 보고 { #report-a-vulnerability }

취약점은 [GitHub Security Advisories](https://github.com/yhay81/pylopdf/security/advisories/new)를 통해 비공개로 보고하세요. 공개 이슈를 열지 마세요. 일주일 안에 첫 응답을 받게 됩니다.

## 신뢰할 수 없는 PDF 다루기 { #untrusted-pdfs }

pylopdf는 Rust로 작성되었고 필수 Python 의존성이 없지만, 출처를 알 수 없는 PDF는 여전히 메모리나 시간을 과도하게 쓰도록 만들어질 수 있습니다. 사용자 업로드처럼 직접 관리하지 않는 파일을 처리할 때는 리소스 정책을 지정해 여세요.

```python
import pylopdf

try:
    with pylopdf.open("upload.pdf", limits=pylopdf.DocumentLimits.web()) as doc:
        if doc.complexity["page_count"] > 50:
            reject_upload("too many pages")
        preview = doc.pages[0].pixmap(dpi=144)
except pylopdf.LimitError as error:
    reject_upload(error.code)
```

!!! warning "실행 시간을 제한하고 프로세스를 격리하세요"

    제한은 메모리와 출력 크기를 묶어 두지만, 오래 걸리는 호출을 멈추지는 않습니다. 작업자, 프로세스 또는 컨테이너에서 CPU 시간이나 경과 시간 기한을 강제하고, 가능하면 신뢰할 수 없는 파일은 샌드박스나 컨테이너에서 처리하세요.

`DocumentLimits.web()`은 메모리가 제한된 웹 서버와 큐 작업자를 위한 보수적인 출발점이며, 다음 제한을 설정합니다.

| 리소스 | 제한 |
|---|---:|
| 파일 크기 | 10 MiB |
| 페이지 | 200 |
| 객체 | 100,000 |
| 배열과 딕셔너리의 중첩 | 64단계 |
| 디코딩된 스트림 하나(디코딩된 이미지 포함) | 64 MiB |
| 한 페이지의 콘텐츠 스트림 | 10 MiB |
| 디코딩된 모든 스트림의 합계 | 128 MiB |
| 추출한 텍스트의 합계 | 1 MiB |
| 추출한 문자(글리프)의 합계 | 65,536 |
| 렌더러에 넘기는 PDF 데이터 | 64 MiB |

제한을 바꾸려면 직접 정책을 만드세요. 모든 인수는 선택 사항이며, `None`은 제한 없음을 뜻합니다.

```python
limits = pylopdf.DocumentLimits(
    max_file_size=50 * 1024 * 1024,
    max_pages=1_000,
    max_decompressed_size=128 * 1024 * 1024,
)
```

| 인수 | 제한하는 대상 |
|---|---|
| `max_file_size` | 입력 파일의 크기 |
| `max_pages` | 페이지 수 |
| `max_objects` | 객체 수 |
| `max_object_depth` | 배열과 딕셔너리의 중첩 |
| `max_decompressed_size` | 디코딩된 스트림 하나의 크기 |
| `max_page_content_size` | 디코딩된 한 페이지 콘텐츠의 크기 |
| `max_total_decompressed_size` | 디코딩된 모든 스트림의 합계 크기 |
| `max_text_size` | 추출한 텍스트의 UTF-8 합계 크기 |
| `max_text_glyphs` | 추출한 문자의 합계 수 |
| `max_interpretation_size` | 렌더링과 추출이 읽는 PDF 데이터의 크기 |

`doc.complexity`는 아무것도 디코딩하지 않고 페이지, 객체, 스트림 수와 인코딩된 스트림 크기, 가장 깊은 중첩을 보고합니다. 무거운 작업을 하기 전에 문서를 분기하거나 거부하는 데 사용하세요.

`limits` 없이 연 문서에는 열 때의 제한이 없지만, 아래의 호출별 제한은 여전히 적용됩니다.

### 개별 호출의 제한 { #default-limits }

큰 데이터를 읽거나 만드는 메서드에는 각자의 제한이 있으며, 기본값은 대부분의 용도에 안전합니다. 표에 나온 키워드로 제한을 높이거나, 신뢰할 수 있는 입력이라면 `None`을 전달해 해제하세요.

| 메서드 | 기본 제한 | 키워드 |
|---|---|---|
| `Document.to_bytes()` | 출력 512 MiB | `max_size` |
| `Document.render()` | PNG 출력 합계 512 MiB | `max_size` |
| `markdown()`, `Table.markdown()` | 출력 64 MiB | `max_size` |
| `Pixmap.to_bytes()`, `Page.svg()` | 출력 64 MiB | `max_size` |
| `Page.search()` | 결과 4,096개 | `max_hits` |
| `Page.content()` | 글리프, 경로와 클립의 세그먼트, 선의 대시 길이, 항목을 합쳐 262,144개 | `max_elements` |
| `Page.insert_text()`, `insert_textbox()` | 텍스트 1 MiB와 4,096줄 | `max_text_size` |
| 그리기, 폼, `Font`에 전달하는 글꼴 입력 | 글꼴당 64 MiB | `max_font_size` |
| `Page.insert_image()` | 입력 64 MiB와 6,400만 픽셀 | `max_size`, `max_pixels` |
| `Page.replace_text()` | 페이지 콘텐츠 64 MiB | `max_size` |
| 첨부 파일: `add()`, `read()`, `write()` | 파일당 64 MiB | `max_size` |
| `Stream.read()` | 64 MiB | `max_size` |
| `Document.xmp`, `pdfa_claim` | XMP 메타데이터 1 MiB | `doc.catalog["Metadata"]`의 `Stream.read(max_size=)` |
| `OcrEngine()` | 모델 파일 64 MiB | `max_model_size` |

일부 제한은 고정되어 있습니다. 렌더링은 페이지당 6,400만 픽셀로 제한됩니다. 비밀번호는 PDF 2.0의 최대치인 UTF-8 127바이트까지입니다. `doc.pages.select()`, `insert()`, `extend()`, `doc.text()`, `doc.markdown()`, `doc.render()`에 전달하는 페이지 목록은 페이지 수와 4,096 중 큰 값까지의 항목을 담을 수 있습니다. 목차, 페이지 레이블, 폼 필드, 주석, 첨부 파일 목록에는 큰 실제 문서에 맞춘 넉넉한 고정 제한이 있습니다.

거부된 호출은 문서를 변경하기 전에 예외를 발생시키며, 부분 결과를 반환하지 않습니다.

### 오류 코드 { #error-codes }

`LimitError`는 `PdfError`의 하위 클래스입니다. `code` 속성(`error.args[0]`과 같음)은 도달한 제한을 나타내며 릴리스 간에 안정적이므로 프로그램에서 이 값으로 분기할 수 있습니다. 메시지는 사람을 위한 것이며 바뀔 수 있습니다.

| 코드 | 발생하는 경우 |
|---|---|
| `file_size`, `page_count`, `object_count`, `object_depth` | 문서가 `DocumentLimits`의 구조 제한을 넘음 |
| `decompressed_size`, `page_content_size`, `total_decompressed_size`, `decompression_unverifiable`, `stream_filter_count` | 스트림이 허용량보다 크게 디코딩되거나, 출력 크기를 제한할 수 없는 필터를 사용함(16개를 넘는 연쇄 필터 포함) |
| `text_size`, `text_glyph_count`, `interpretation_size` | 추출이나 렌더링이 정책이 허용하는 것보다 많이 읽으려 함 |
| `content_element_count` | 페이지가 그리는 글리프, 세그먼트, 대시 길이, 항목이 `max_elements`보다 많음 |
| `recovery_scan_size` | 손상된 파일을 복구하는 데 지나치게 많은 작업이 필요함 |
| `content_nesting_depth`, `resource_nesting_depth` | 페이지가 너무 깊게 중첩되어 해석하기에 안전하지 않음. [아래](#built-in-protections) 참고 |
| `pdf_output_size`, `render_output_size`, `markdown_output_size`, `svg_output_size`, `pixmap_output_size`, `replacement_output_size` | 출력이 `max_size`를 넘음 |
| `search_input_size`, `search_hit_count` | 검색어가 4,096바이트를 넘거나 결과가 `max_hits`보다 많음 |
| `text_input_size`, `text_line_count`, `font_input_size`, `path_point_count` | 그리기에 전달한 텍스트, 글꼴, 도형 경로가 너무 큼 |
| `canvas_scope_depth` | Canvas의 범위가 16단계를 넘게 중첩됨 |
| `tag_text_size` | 새 문서에서 태그의 텍스트나 목차 항목 제목이 64 KiB를 넘음 |
| `image_input_size`, `image_pixel_count`, `image_inventory_count` | 이미지가 너무 크거나, 페이지가 4,096개를 넘는 이미지를 사용함 |
| `embedded_file_size`, `embedded_file_input_size` | 첨부 파일이나 그 이름, 설명이 너무 큼 |
| `stream_data_size`, `object_value_depth` | 저수준 스트림이 읽기에 너무 크거나, 쓰는 값의 중첩이 너무 깊음 |
| `password_input_size`, `metadata_input_size`, `annotation_input_size`, `form_field_input_size`, `toc_input_size`, `page_label_input_size`, `replacement_input_size` | 메서드에 전달한 텍스트가 너무 큼 |
| `page_entry_count` | 페이지 목록이 허용보다 김 |
| `toc_entry_count`, `toc_text_size`, `named_destination_count`, `named_destination_text_size`, `structure_tree_count`, `form_content_size` | 목차, 링크 대상 테이블, 태그 PDF 구조, 가져온 페이지가 너무 큼 |
| `xmp_metadata_size` | XMP 메타데이터가 1 MiB보다 큼 |
| `xmp_metadata_input_size` | `Document.xmp`에 대입한 XMP 메타데이터가 1 MiB보다 큼 |
| `ocr_model_size`, `ocr_dictionary_entries` | OCR 모델 파일이 너무 큼 |

## 기본 보호 장치 { #built-in-protections }

다음은 `DocumentLimits` 여부와 관계없이 모든 문서에 적용됩니다.

- **깊은 중첩.** 렌더링 엔진인 hayro는 중첩된 구조를 재귀적으로 읽습니다. pylopdf는 페이지를 렌더링하거나 추출하기 전에 그 콘텐츠, 색 공간, 함수가 얼마나 깊이 중첩되는지 확인하고, 스택이 넘치거나 조용히 잘릴 페이지를 거부합니다(`content_nesting_depth`, `resource_nesting_depth`, `object_depth`). 문서의 다른 페이지는 계속 사용할 수 있습니다.
- **렌더러 충돌.** 잘못된 데이터로 렌더러가 패닉을 일으키면, pylopdf는 `except Exception`으로 잡을 수 없는 예외 대신 `PdfError`를 발생시키며 문서는 계속 사용할 수 있습니다.
- **손상된 파일.** pylopdf는 가능하면 손상된 cross-reference 데이터를 복구하고, `PylopdfWarning`을 내며, `doc.is_repaired`를 설정합니다. 애플리케이션이 복구된 입력을 받아들이면 안 된다면 이런 문서를 거부하세요. 일부 손상된 파일은 열 수 없으며 `MalformedPdfError`를 발생시킵니다.
- **안전한 저장.** `save()`와 `Pixmap.save()`는 대상 옆의 임시 파일에 쓰고, 쓰기가 완료된 뒤에만 대상을 교체하므로 실패해도 반쯤 쓰인 파일이 남지 않습니다. Windows 접근 제어 목록을 포함해 기존 파일 권한을 유지합니다.
- **스크립트 없음.** 포함된 JavaScript는 절대 실행하지 않습니다.

## 권장 사항 { #recommendations }

- **호스트에서 시간 제한을 강제하세요.** 제한은 메모리와 출력 크기를 제한하지만, 정상처럼 보이는 일부 문서는 렌더링에 오랜 시간이 걸립니다. 신뢰할 수 없는 작업은 작업자, 프로세스, 컨테이너에서 CPU 또는 실제 시간 기한을 두고 실행하세요.
- **일괄 처리를 격리하세요.** 가능하면 신뢰할 수 없는 파일은 샌드박스나 컨테이너에서 처리하세요.
- **민감 정보 삭제(redaction)를 페이지 삭제에 의존하지 마세요.** 페이지를 삭제하면 그 링크, 폼 필드, 구조 항목은 제거되지만, 내용의 사본이 첨부 파일이나 메타데이터처럼 파일의 다른 곳에 남을 수 있습니다.
- **병렬 렌더링에는 `doc.render()`를 사용하세요.** 이미 자체 메모리 사용량을 제한하므로, 그 바깥을 제한 없는 병렬 처리로 감싸지 마세요.

pylopdf의 지속적 통합(CI)은 네이티브 Python과 WebAssembly에서 같은 악의적 입력 테스트를 실행하며, 정기 퍼징으로 잘못된 cross-reference 테이블, 참조 순환, 깊게 중첩된 객체, 손상된 스트림, 압축 폭탄을 입력합니다.

## 의존성 감사 { #dependency-auditing }

CI는 푸시할 때마다 Rust 의존성과 RustSec 권고 데이터베이스를 대상으로 `cargo audit`을 실행합니다.

이 정책의 저장소 사본은 [`SECURITY.md`](https://github.com/yhay81/pylopdf/blob/main/SECURITY.md)입니다.
