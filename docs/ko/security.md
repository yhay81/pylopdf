---
title: 보안
description: 지원 버전, 비공개 취약점 보고, 신뢰할 수 없는 PDF를 처리할 때의 지침입니다.
---

# 보안

PyPI의 최신 릴리스만 보안 수정 지원을 받습니다.

## 취약점 보고 { #report-a-vulnerability }

[GitHub Security Advisories](https://github.com/yhay81/pylopdf/security/advisories/new)를
통해 비공개로 보고하세요. 공개 Issue를 만들지 마세요. 첫 답변은 일주일 이내를 목표로 합니다.

## 신뢰할 수 없는 PDF 처리 { #untrusted-pdfs }

pylopdf는 Rust로 작성되었고 필수 Python 의존성이 없지만, 악의적인 PDF 입력을
파싱하는 작업에는 본질적인 위험이 있습니다.

!!! warning "전체 리소스 정책을 사용하세요"
    `pylopdf.open()`에`limits=pylopdf.DocumentLimits.web()`을 전달하세요.
    메모리가 제한된 Web 또는 queue worker에서 사용자 업로드를 처리하기 위한
    보수적인 시작점입니다.

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

Web profile은 현재 다음 상한을 독립적으로 적용합니다.

| 리소스 | 상한 |
|---|---:|
| 입력 파일 | 10 MiB |
| 페이지 | 200 |
| 간접 object | 100,000 |
| 이미지 RGBA 추정치를 포함한 개별 디코딩 stream | 64 MiB |
| 개별 page-content stream | 10 MiB |
| 누적 디코딩 또는 추정 stream byte | 128 MiB |
| 직접 array/dictionary 중첩 | 64 |
| 해석한 페이지 전체의 누적 UTF-8 glyph payload | 1 MiB |

다른 workload에는`DocumentLimits(...)`를 직접 구성하세요. `None`이 아닌 값은
양의 정수여야 합니다. 기존`max_decompressed_size=`는 stream당 예산의 호환
축약형으로 유지되지만`limits=`와 함께 사용할 수 없습니다.

`LimitError`는`PdfError`의 subclass입니다. 안정적인`code`는`file_size`,
`page_count`, `object_count`, `object_depth`, `decompressed_size`,
`page_content_size`, `total_decompressed_size`, `text_size`,
`decompression_unverifiable` 중 하나이며 같은 값은`error.args[0]`에도 있습니다.
안전하게 상한을 계산할 수 없는 filter chain은 낙관적으로 디코딩하지 않고 거부합니다.

`doc.complexity`는 stream 디코딩이나 renderer 호출 없이 페이지, object, stream 수,
인코딩된 stream byte, 직접 object 최대 깊이를 보고합니다. 무거운 추출 전에
routing하는 데 사용할 수 있습니다. 구조 및 압축 해제 예산은 열린 source를
검증하므로, 생성물이 다른 trust boundary를 넘을 때는 같은 정책으로 다시 여세요.

- 렌더링은 페이지당 6,400만 픽셀로 제한됩니다.
- 임베드된 JavaScript는 설계상 지원하지 않으며 실행하지 않습니다.
- `render_pages()`에는 정상적인 메모리 제한 admission이 있으므로 application
  계층에서 무제한 병렬 호출을 덧붙이지 마세요.
- CPU deadline은 Worker, process 또는 container host에서 적용하세요. 리소스
  예산은 문서화된 allocation과 출력 증가를 제한하지만 실행 중인 parser나
  interpreter를 wall-clock 시간으로 중단하지 않습니다.
- 신뢰할 수 없는 파일을 일괄 처리할 때는 가능하면 sandbox나 container에서 실행하세요.
  native와Pyodide CI는 같은 hostile-input 회귀 계약을 공유하며, 정기 Atheris
  fuzzing은 손상된 xref, cycle, 깊은 object, broken stream, 압축 bomb을 seed로 사용합니다.

## 의존성 감사 { #dependency-auditing }

CI는 push할 때마다 RustSec 취약점 데이터베이스를 기준으로 Rust 의존성 트리에
`cargo audit`를 실행합니다.

저장소의 정책 원본은
[`SECURITY.md`](https://github.com/yhay81/pylopdf/blob/main/SECURITY.md)입니다.
