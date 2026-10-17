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
| rendering／extraction에 전달되는 전체 PDF snapshot | 64 MiB |
| 해석한 페이지 전체의 누적 위치 glyph record | 65,536 |

다른 workload에는`DocumentLimits(...)`를 직접 구성하세요. `None`이 아닌 값은
양의 정수여야 합니다. 기존`max_decompressed_size=`는 stream당 예산의 호환
축약형으로 유지되지만`limits=`와 함께 사용할 수 없습니다.

page count／lookup, complexity, render snapshot, TOC／link, annotation,
Form import와 page 구조 편집을 포함한 모든 공개page indexing은 하나의 반복
page-tree walker를 사용합니다. 재사용된Page／Pages object와 cycle, 32개 참조를
초과한 간접`/Kids` chain, 256 level을 초과한 내부tree, 간접object 수를 초과한edge를
거부합니다. `max_pages`는 상한을 넘는 첫page에서 중단합니다. page policy 없는
open은 호환성을 유지하고 이page-tree 검사를 첫page-indexing 작업까지 지연합니다.

page batch는 한 번 얻은 page 수로 entry를 검증합니다. `pages=None`과`insert_pdf()`의
전체 범위는 page 수와 관계없이 모든 page를 대상으로 합니다. 명시sequence는 같은
page를 반복할 수 있지만 page 수와4,096 중 큰 값을 넘는 첫entry에서
materialization을 멈추고`page_entry_count`를 반환하므로, 반복해도 문서 전체를 한 번
처리하는 비용을 넘지 않습니다. text 추출과 rendering은call 단위hayro cache를
4,096 entry마다 다시 만들고, `max_size`, `max_text_size` 등 출력 예산은call 전체에
적용됩니다. core API를 직접 호출해도entry 검사를 반복하며, core page import는 변경
전에 중복된 원본page를 거부합니다.

`LimitError`는`PdfError`의 subclass입니다. 안정적인`code`는`file_size`,
`page_count`, `object_count`, `object_depth`, `decompressed_size`,
`page_content_size`, `total_decompressed_size`, `text_size`, `text_glyph_count`,
`interpretation_size`, `embedded_file_size`, `embedded_file_input_size`,
`form_field_input_size`, `form_content_size`, `annotation_input_size`, `xmp_metadata_size`,
`metadata_input_size`, `toc_input_size`, `toc_entry_count`, `toc_text_size`,
`named_destination_count`, `named_destination_text_size`,
`page_entry_count`, `page_label_input_size`,
`render_output_size`,
`markdown_output_size`, `svg_output_size`, `replacement_input_size`,
`replacement_output_size`, `pdf_output_size`, `image_input_size`,
`image_pixel_count`, `font_input_size`, `text_input_size`, `text_line_count`,
`search_input_size`, `search_hit_count`, `password_input_size`, `pixmap_output_size`,
`ocr_model_size`, `ocr_dictionary_entries`, `stream_filter_count`,
`decompression_unverifiable`, `recovery_scan_size` 중
하나이며 같은 값은`error.args[0]`에도 있습니다.
안전하게 상한을 계산할 수 없는 filter chain은 낙관적으로 디코딩하지 않고 거부합니다.
pylopdf가 소유한 제한된lopdf 디코딩 경로는 filter 목록을materialize하기 전에
16개 layer를 초과하는`/Filter` chain도 거부합니다.

`doc.complexity`는 stream 디코딩이나 renderer 호출 없이 페이지, object, stream 수,
인코딩된 stream byte, 직접 object 최대 깊이를 보고합니다. 무거운 추출 전에
routing하는 데 사용할 수 있습니다. 구조 및 압축 해제 예산은 열린 source를
검증하므로, 생성물이 다른 trust boundary를 넘을 때는 같은 정책으로 다시 여세요.
직접 깊이 검증과complexity 검사의 반복 순회stack은 fallible하게 확장되므로
allocation refusal은 부분facts가 아닌`PdfError`가 됩니다.

`max_interpretation_size`는 hayro가 보관된 input을 처음 읽을 때와 pylopdf가 편집,
복호화 또는 AcroForm state 선택 후 현재 상태를 serialize할 때 적용됩니다. 상한이 있는
writer는 경계를 넘는 write를 거부하며 불완전한 renderer／extractor cache를 설치하지
않습니다. 호환성을 위한 기본값은`None`이고`DocumentLimits.web()`은64 MiB입니다.

`max_text_size`를 설정하면plain-text 추출은 조립된 정확한UTF-8 size를 사전 검사하고
한 번의 추출call을glyph payload budget의2배로 제한합니다. 비어 있지 않은glyph는payload를
최소1 byte 제공하며 추론gap과 줄바꿈 합계는glyph 수를 넘지 않습니다. 이 출력
상한은 내부4,096 entry chunk가 아니라call 전체에 적용되고, 명시sequence는 page 수와
4,096 중 큰 값까지의entry만 허용하므로 반복page 번호로policy를 우회할 수 없습니다.
거부code는`text_size`를 유지하고 호환성을 위한 기본값은`None`입니다.

`max_text_glyphs`는line 조립 전에 유지하는record 수를 제한하므로 구조화text가
materialize할 수 있는block, line, span, word 수도 제한합니다. 같은page의text 해석과
table 해석은 하나의 누적admission을 공유하며 거부된page는budget을 소비하지 않습니다.
호환성을 위한 기본값은`None`이고`DocumentLimits.web()`은65,536입니다.

관대한 열기는 lopdf의 cross-reference 복구를 사용합니다. `startxref`, `/Prev`,
`/XRefStm` offset이 64 byte 이내로 어긋나면 근처의`xref` keyword로 옮깁니다. 표준
해석이 여전히 실패하면 lopdf는 줄 시작의 object header와`/Root`가 존재하는 가장
최근의 classic `trailer`로 table을 재구성합니다. 이후 revision의 object가 우선하지만
손상된 영역의 object는 누락될 수 있습니다. classic trailer가 없는 손상된 xref stream
파일은 거부됩니다. 어느 복구든`PylopdfWarning`이 발생하고
`doc.is_repaired`（metadata probe의`repaired`）가`True`가 되며, 렌더링과 추출은 저장
시 기록되는 복구된 object graph를 사용하고 저장하면 xref data를 정규화합니다. 복구된
입력을 받아들일 수 없는 호출자는`is_repaired`인 문서를 거부할 수 있습니다. 19 byte
xref entry나 모호하지 않은 object 경계에서 보정한 stream `/Length`처럼 선언된 구조를
유지하는 관대한 처리는 보고하지 않습니다. 복구된 문서에도`DocumentLimits`가 그대로
적용됩니다.

lopdf의 재구성은 뒤에`endstream`이 없는`stream` keyword마다 입력의 나머지를 다시
검색합니다. pylopdf는 관대한 열기와 metadata probe 전에 이 작업량을 선형 시간으로
추정하고, 입력 1 byte당 64 byte에 64 MiB를 더한 값을 넘는 입력을
`recovery_scan_size`로 거부하여 악의적인 입력이 복구를 이차 시간으로 만들지 못하게
합니다. 실제 손상 파일에는 그 위치에 잘린 stream이 많아야 하나 있습니다.

- 렌더링은 페이지당 6,400만 픽셀로 제한됩니다.
- `Document.render_page()`, `Page.render()`, `Pixmap.tobytes()`의 encoded PNG
  출력 기본 상한은64 MiB입니다. writer는Python `bytes`를 반환하기 전에 경계를
  넘는write를 거부하며`max_size=None`으로 명시적으로 해제할 수 있습니다.
  rendering은`render_output_size`, Pixmap 직접encode는`pixmap_output_size`를
  사용합니다.
- `Document.tobytes()`는 일반, object/xref stream, 암호화 출력 모두에512 MiB 기본
  serialization 상한을 적용합니다. Rust writer는Python `bytes` 변환 전에 경계를
  넘는write를 거부하며`max_size=None`으로 명시적으로 해제할 수 있습니다.
  `save()`는target과 같은directory에 안전하게 만든sibling으로stream한 뒤 완전한
  write가 끝난 경우에만 요청path를 원자적으로 교체하므로serialization/교체 실패 시
  기존file을 보존합니다. 이in-memory 예산의 대상은 아닙니다. `garbage`, `deflate`,
  `object_streams` 같은save option은 이후I/O가 실패해도 문서화된mutation semantics를
  유지합니다.
- `Pixmap.save()`는target directory 안에 예측할 수 없고 배타적으로 생성한sibling에
  PNG encode를 직접stream하며, 완전한write가 성공한 뒤에만 요청path를 원자적으로
  교체합니다. 완성된PNG를 메모리에 하나 더 보관하지 않습니다. 교체 실패 시 기존
  output을 보존하고 임시file을 삭제합니다.
- `Page.insert_image()`는encoded JPEG/PNG input을 기본64 MiB, decoded PNG input을
  기본64,000,000픽셀로 제한합니다. filename은GIL을 해제한Rust 경계에서 상한을 두고
  읽으며 PNG dimension은decoded storage 할당 전에 검사합니다. 신뢰 가능한workload는
  `max_size=None`／`max_pixels=None`으로 명시적으로 해제할 수 있습니다.
- `insert_text`, `insert_textbox`, `set_form_field`, `set_fallback_font`의 명시／자동
  OpenType input은 기본64 MiB입니다. buffer는PyO3 copy 전에 거부하고 filename은GIL을
  해제한 상한 적용Rust path에서 읽습니다. 신뢰 가능한workload는
  `max_font_size=None`으로 명시적으로 해제할 수 있습니다.
- `insert_text()`와`insert_textbox()`의 생성text input은 기본UTF-8 1 MiB이며
  물리줄과 줄바꿈 후layout은4,096줄로 제한됩니다. Python은PyO3 copy 전에 물리줄을
  검사하고Rust 경계도 다시 검사하여mutation 전에 줄바꿈layout을 중단합니다.
  textbox는확장된string을 할당하기 전에tab 확장량을 미리 검사합니다. 신뢰 가능한
  삽입input은`max_text_size=None`으로 명시적으로 해제할 수 있고 거부code는
  `text_input_size` 또는`text_line_count`입니다. AcroForm text／choice appearance는
  고정4,096줄layout 상한을 유지합니다. UAX #14／grapheme index, 줄collection,
  유지하는 줄text는fallible하게 확장되므로 allocation refusal은 부분layout이나
  document 편집을 노출하지 않습니다. pylopdf가 소유한 정규화, grapheme 참조,
  shaped line／glyph collection, WinAnsi 출력, 입력 기반text operator 확장도
  fallible합니다. Standard 14 너비 검사는encoded text copy를 할당하지 않습니다.
  allocation refusal은`PdfError`를 반환하며 삽입은non-mutating이고 form fill은
  원자적으로rollback합니다.
- `search_for()`의 검색어는UTF-8 4,096 byte, 반환geometry는 기본4,096건으로
  제한됩니다. Python은PyO3 copy 전에 초과 검색어를 거부하고Rust 경계도 두 제한을
  다시 검사합니다. 신뢰 가능한 결과 집합은`max_hits=None`으로 명시적으로 해제할
  수 있고 거부code는`search_input_size`／`search_hit_count`입니다. partial list는
  반환하지 않습니다.
- open, authenticate, 빠른metadata probe 및AES-256 출력의password는PyO3 copy나
  password KDF 전에UTF-8 127 byte로 제한됩니다. Rust 직접 호출도 경계를 반복하며
  거부code는`password_input_size`입니다. 저장 거부는document mutation 또는output
  생성 전에 발생합니다.
- `render_page_svg()`와`Page.render_svg()`의UTF-8 출력 기본 상한은64 MiB이며
  PyO3가Python string을 만들기 전에 초과 결과를 거부합니다. `max_size=None`으로
  명시적으로 해제할 수 있습니다. hayro-svg 0.7은 완성된`String`만 반환하므로
  pylopdf가 경계를 적용하기 전의 내부Rust string 하나는 이 제한의 대상이 아닙니다.
- 그리기 삽입은cache 무효화, 입력decode 또는dependent object 생성 전에page
  `/Contents`의raw array와 참조chain을 검사합니다. raw array는4,096 entry,
  chain은 깊이32, 최종array는 한 번만 추가되는`q`/`Q` isolation pair를 포함해
  4,096 stream 참조로 제한됩니다. 실패하면document를 변경하지 않습니다.
- `Page.replace_text()`는search, replacement, fallback의 합계를4,096 UTF-8
  byte로 제한하고 디코딩한page content, font encoding data, 교체 증가분, 최종
  stream에64 MiB 기본 상한을 적용합니다. commit 전에page 전용stream을 준비하므로
  복사한page의 공유content를 변경하지 않으며 no-match/error에서document와cache를
  보존합니다. caller text는PyO3 copy 전에 완전한encoded copy를 만들지 않고
  순차적으로 계산합니다. 신뢰할 수 있는 입력은`max_size=None`으로 명시적으로
  해제할 수 있습니다.
- `delete_pages()`와`select()`는Python과Rust 모두에서call당 page 수와4,096 중
  큰 값까지의entry를 허용하므로, 같은page를 반복해도 선택 결과가 문서보다 커지지
  않습니다. iterable은graph 변경 전 상한을 넘는 첫entry에서 중단하고
  `page_entry_count`를 반환합니다. `insert_pdf()`는 원본의 전체 범위를 허용합니다.
  빈delete는cache, generation 및기존`Page` view를 유지합니다.
- `Page.get_images()`는 페이지당4,096 placement, 누적64,000,000 source pixel 또는
  64 MiB 반환payload를 넘는 부분 결과를 거부합니다. Flate-wrapped JPEG passthrough도
  남은byte 예산까지만 압축을 풉니다.
- `Document.embfile_add()`는PyO3 copy 전에64 MiB를 넘는 입력을 거부하고,
  `embfile_get()`은 각 디코딩filter layer에 같은 기본 상한을 적용합니다. 크기를 알고
  있는 대용량 첨부 파일은`max_size=`를 늘릴 수 있고, `max_size=None`은 무제한 입력
  또는materialization을 명시적으로 허용합니다. 첨부name tree도4,096 entry/node,
  깊이32, encoded/decoded 이름 합계1 MiB를 넘으면 거부합니다. caller 조회／삭제 이름과
  추가key/filename/description 입력은tree 순회 또는data copy 전에1 MiB에서 중지하며
  `embedded_file_input_size`를 사용합니다. 편집은inline FileSpec
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
  방문하며 쓰기는PyO3 copy 전에`page_label_input_size`로 같은entry/text 상한을
  적용합니다.
- AcroForm field tree는4,096 entry/node, 8,192 edge, 깊이64, encoded/
  decoded/returned name·value 1 MiB 또는choice value 4,096 item을 넘는 부분 결과를
  거부합니다. 참조cycle은 한 번만 방문하고 상속값은 반환leaf마다 예산에 포함합니다.
  입력도 같은tree 상한과1 MiB caller 이름／값 상한을 원자적으로 적용하며 font 탐색,
  button lookup 또는file 읽기 전 거부에는`form_field_input_size`를 사용합니다.
- AcroForm button field는4,096 widget, 8,192 normal appearance state entry,
  4,096 unique returned state name 또는encoded/returned state-name text 1 MiB를
  넘으면 거부합니다. 입력은 누락된`Off`/on state key를 변경 전에 예산에 포함합니다.
- 주석과link 읽기는4,096 `/Annots` entry 또는call당aggregate encoded/returned
  metadata text 1 MiB를 넘는 부분 결과를 거부합니다. 추가는dependent object 생성과
  cache 무효화 전에 같은page 수, 생성subtype과Contents/URI 입력 합계1 MiB,
  highlight 4,096 rect를 검사합니다. caller text는PyO3 copy 또는rectangle
  iteration 전에`annotation_input_size`로 거부하며 highlight iteration은4,097번째
  item에서 중단됩니다.
- named destination lookup은 참조cycle을 한 번만 방문하고65,536 entry/node 또는
  131,072 edge를 넘는tree는`named_destination_count`로, key byte 16 MiB를 넘는
  tree는`named_destination_text_size`로 거부하며 깊이32도 넘지 않게 합니다.
  중간에 잘린tree를 단순 미해결로 조용히 처리하지 않습니다. `get_toc()`,
  `Page.get_links()`, `insert_pdf()`의link 가져오기, page 삭제와 선택 시name
  tree와 구형식`/Dests`의 재작성은 이 상한을 공유하므로 해석되는 올바른 형식의
  tree는 정리와 가져오기도 가능합니다. 재작성은 유지하는destination 값을direct
  object 1,048,576개(entry당16개)와text 16 MiB 범위에서copy합니다.
  조회한destination 이름은 순회 중인 구조의 예산에 계상되며 outline은TOC text,
  name tree는key text, link는page당1 MiB link metadata를 사용합니다.
  `Page.get_links()`는 첫named lookup에서tree를 한 번만index화하고 다음 편집
  전까지 그index와page index를 재사용하므로 모든page의link를 읽어도 처리
  시간이 선형으로 유지됩니다. 유지하는index는 같은entry·key text 상한 안에서
  서로 다른key를 한 번씩만 보관하며, 거부된tree는cache하지 않습니다.
- TOC 읽기는 반복outline walk로 참조cycle을 한 번만 방문하고GIL을 해제합니다.
  65,536 node/entry 또는131,072 edge를 넘는 부분 결과는`toc_entry_count`로,
  source/returned text 16 MiB를 넘는 결과는`toc_text_size`로 거부하며 깊이64와
  destination 간접 참조32단계도 넘지 않게 합니다. 쓰기도 변경 전에
  `toc_entry_count`와`toc_input_size`로 같은entry, 깊이, title text 상한을 검사합니다.
  page 삭제와 선택은outline 상한을 공유하므로 쓴outline은 계속 편집할 수 있습니다.
- `Document.metadata`는 표준Info 8개 필드만decode하고 aggregate source/returned
  text 1 MiB를 넘으면 거부합니다. custom entry는Python 출력으로materialize하지
  않습니다. `peek_metadata(max_file_size=)`는 parsing 전에 path 또는 byte input을
  거부할 수 있고 returned 표준text도 제한합니다. 입력 기본값은 무제한입니다.
  쓰기는PyO3 copy 전에`metadata_input_size`로 source/encoded text 1 MiB를 검사해
  원자적으로 적용합니다.
- 임베드된 JavaScript는 설계상 지원하지 않으며 실행하지 않습니다.
- `render_pages()`는 기본적으로 전체page를 렌더링하고 명시entry는 page 수와4,096
  중 큰 값까지 허용하며 누적encoded PNG 기본 상한은512 MiB입니다. 병렬 결과는
  하나의atomic budget을 공유하며 실패 시 부분list를 반환하지 않습니다.
  `max_size=None`으로 명시적으로 해제할 수 있습니다. worker admission은live
  raster/conversion buffer를 별도로 제한하고 각worker는4,096 page마다hayro cache를
  다시 만들므로 application 계층에서 무제한 병렬 호출을 덧붙이지 마세요.
- `Document.to_markdown()`는 기본적으로 전체page를 변환하고 명시entry는 page 수와
  4,096 중 큰 값까지 허용하며 누적UTF-8 출력 기본 상한은64 MiB입니다. heading size 집계pass와render pass 모두에서 한 번에 한
  page의 interpreted layout, table, word만 유지합니다. page 출력을 조립하기 전에
  각table에 남은 누적 예산을 전달합니다. `Table.to_markdown()`도 같은 기본 상한을
  사용하며 merged-cell 확장을 포함한 escape 후 정확한UTF-8 크기를 사전 검사합니다.
  제목, paragraph, list, table은entry를 보관할 때 예산에 반영되며, 전체size가
  허용된 뒤page를 선형 조립합니다. 상한 초과 시 부분string을 반환하지 않으며
  `max_size=None`으로 명시적으로 해제할 수 있습니다.
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
