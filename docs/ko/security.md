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
`embedded_file_size`, `xmp_metadata_size`, `render_output_size`,
`markdown_output_size`, `svg_output_size`, `decompression_unverifiable` 중
하나이며 같은 값은`error.args[0]`에도 있습니다.
안전하게 상한을 계산할 수 없는 filter chain은 낙관적으로 디코딩하지 않고 거부합니다.

`doc.complexity`는 stream 디코딩이나 renderer 호출 없이 페이지, object, stream 수,
인코딩된 stream byte, 직접 object 최대 깊이를 보고합니다. 무거운 추출 전에
routing하는 데 사용할 수 있습니다. 구조 및 압축 해제 예산은 열린 source를
검증하므로, 생성물이 다른 trust boundary를 넘을 때는 같은 정책으로 다시 여세요.

관대한 열기는 한 가지 제한된 복구만 수행합니다. 같은 마지막 revision에 온전한
classic xref table이 있고 원래 제한 아래에서 전체 parse가 성공할 때만 잘못된 마지막
`startxref`를 바꿉니다. object header 검색, xref stream 복구, 이전 revision으로의
rollback은 하지 않습니다. 복구 시`PylopdfWarning`이 발생하고
`doc.is_repaired`（metadata probe의`repaired`）가`True`가 되며, 저장하면 xref data를
정규화합니다.

- 렌더링은 페이지당 6,400만 픽셀로 제한됩니다.
- `render_page_svg()`와`Page.render_svg()`의UTF-8 출력 기본 상한은64 MiB이며
  PyO3가Python string을 만들기 전에 초과 결과를 거부합니다. `max_size=None`으로
  명시적으로 해제할 수 있습니다. hayro-svg 0.7은 완성된`String`만 반환하므로
  pylopdf가 경계를 적용하기 전의 내부Rust string 하나는 이 제한의 대상이 아닙니다.
- 그리기 삽입은cache 무효화, 입력decode 또는dependent object 생성 전에page
  `/Contents`의raw array와 참조chain을 검사합니다. raw array는4,096 entry,
  chain은 깊이32, 최종array는 한 번만 추가되는`q`/`Q` isolation pair를 포함해
  4,096 stream 참조로 제한됩니다. 실패하면document를 변경하지 않습니다.
- `delete_pages()`, `select()`, `insert_pdf()`는Python과Rust 모두에서call당
  4,096 page entry를 허용합니다. iterable은graph 변경 전4,097번째item에서
  중단됩니다. 빈delete는cache, generation 및기존`Page` view를 유지합니다.
- `Page.get_images()`는 페이지당4,096 placement, 누적64,000,000 source pixel 또는
  64 MiB 반환payload를 넘는 부분 결과를 거부합니다. Flate-wrapped JPEG passthrough도
  남은byte 예산까지만 압축을 풉니다.
- `Document.embfile_get()`은 각filter layer의 디코딩 출력을 기본64 MiB로 제한합니다.
  크기를 알고 있는 대용량 첨부 파일은`max_size=`를 늘릴 수 있고, `max_size=None`은
  무제한materialization을 명시적으로 허용합니다. 첨부name tree도4,096 entry/node,
  깊이32, encoded/decoded 이름 합계1 MiB를 넘으면 거부합니다. 추가하는key/
  filename/description 입력 합계는1 MiB로 제한합니다. 편집은inline FileSpec
  clone 전에direct object 4,096개, 깊이32, direct string/name/stream data 1 MiB
  상한과Catalog 쓰기 대상을 검증하며 rollback을 위해 문서 전체를clone하지 않습니다.
- `Document.get_pdfa_claim()`은 각filter layer의XMP 디코딩 출력을 기본1 MiB로
  제한합니다. 크기를 알고 있는 대용량packet은`max_size=`를 늘릴 수 있고,
  `max_size=None`은 무제한materialization을 명시적으로 허용합니다.
- `Page.insert_ocr_text_layer()`는 비어 있지 않은word 4,096개 또는UTF-8 text 합계
  1 MiB를 넘는 시점에iterable materialization을 중지합니다. core 직접 호출도 같은
  상한을 적용하고65,535번째 고유CID 할당 전에 중지하며 입력 기반buffer를PDF 변경
  전에 준비합니다.
- 페이지 레이블number tree는4,096 entry/node, 깊이32, encoded/decoded
  style·prefix text 합계1 MiB를 넘는 부분 결과를 거부합니다. 참조cycle은 한 번만
  방문하며 쓰기도 같은entry/text 상한을 적용합니다.
- AcroForm field tree는4,096 entry/node, 8,192 edge, 깊이64, encoded/
  decoded/returned name·value 1 MiB 또는choice value 4,096 item을 넘는 부분 결과를
  거부합니다. 참조cycle은 한 번만 방문하고 상속값은 반환leaf마다 예산에 포함합니다.
  입력도 같은tree 상한과1 MiB 입력값 상한을 원자적으로 적용합니다.
- AcroForm button field는4,096 widget, 8,192 normal appearance state entry,
  4,096 unique returned state name 또는encoded/returned state-name text 1 MiB를
  넘으면 거부합니다. 입력은 누락된`Off`/on state key를 변경 전에 예산에 포함합니다.
- 주석과link 읽기는4,096 `/Annots` entry 또는call당aggregate encoded/returned
  metadata text 1 MiB를 넘는 부분 결과를 거부합니다. 추가는dependent object 생성과
  cache 무효화 전에 같은page 수, 생성subtype과Contents/URI 입력 합계1 MiB,
  highlight 4,096 rect를 검사합니다.
- named destination lookup은 참조cycle을 한 번만 방문하고4,096 entry/node,
  8,192 edge, 깊이32 또는key byte 1 MiB를 넘는tree를 단순 미해결로 조용히
  처리하지 않고 거부합니다. `Page.get_links()`는named link마다tree를 다시
  순회하지 않고call당 하나의borrowed index를 만듭니다.
- TOC 읽기는 반복outline walk로 참조cycle을 한 번만 방문하고GIL을 해제합니다.
  4,096 node/entry, 8,192 edge, 깊이64, destination 간접 참조32단계 또는
  source/returned text 1 MiB를 넘는 부분 결과를 거부합니다. 쓰기도 변경 전에
  entry, 깊이, title text 상한을 검사합니다.
- `Document.metadata`는 표준Info 8개 필드만decode하고 aggregate source/returned
  text 1 MiB를 넘으면 거부합니다. custom entry는Python 출력으로materialize하지
  않습니다. `peek_metadata()`도returned 표준text를 제한하고, 쓰기는 변경 전에
  source/encoded text 1 MiB를 검사해 원자적으로 적용합니다.
- 임베드된 JavaScript는 설계상 지원하지 않으며 실행하지 않습니다.
- `render_pages()`는 최대4,096 page entry를 허용하고 누적encoded PNG 기본 상한은
  512 MiB입니다. 병렬 결과는 하나의atomic budget을 공유하며 실패 시 부분list를
  반환하지 않습니다. `max_size=None`으로 명시적으로 해제할 수 있습니다. worker
  admission은live raster/conversion buffer를 별도로 제한하므로 application
  계층에서 무제한 병렬 호출을 덧붙이지 마세요.
- `Document.to_markdown()`는 최대4,096 page entry를 허용하고 누적UTF-8 출력 기본
  상한은64 MiB입니다. heading size 집계pass와render pass 모두에서 한 번에 한
  page의 interpreted layout, table, word만 유지합니다. 상한 초과 시 부분string을
  반환하지 않으며 `max_size=None`으로 명시적으로 해제할 수 있습니다.
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
