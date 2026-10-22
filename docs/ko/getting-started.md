---
title: 시작하기
description: pylopdf를 설치하고 편집, 렌더링, 추출, 그리기의 핵심 작업 흐름을 배웁니다.
---

# 시작하기

!!! note "프로젝트 상태"

    pylopdf는 1.0 이전 단계이며, 다음 릴리스에서
    [로드맵](https://github.com/yhay81/pylopdf/blob/main/ROADMAP.md)에 따라 API를 다시
    설계합니다. 무엇이 바뀔 수 있는지는 [안정성 정책](stability.md)에서
    설명합니다. 이 사이트는 `main` 브랜치를
    따르므로 아직 PyPI에 배포되지 않은 변경을 설명할 수도 있습니다. 각 릴리스에
    포함된 내용은 [변경 이력](https://github.com/yhay81/pylopdf/blob/main/CHANGELOG.md)에서
    확인하세요.

## 설치 { #installation }

```bash
pip install pylopdf
```

pylopdf에는 Python 3.11 이상이 필요하며 필수 Python 의존성은 없습니다. 미리 빌드된 wheel은 Linux(x86_64, ARM64), macOS(Apple silicon, Intel), Windows(x86_64)를 지원하며 free-threaded Python 3.14t와 3.15t도 포함합니다. WebAssembly 빌드는 Pyodide와 Cloudflare Python Workers에서 동작합니다. 자세한 내용은 [WebAssembly](wasm.md)를 참고하세요.

선택형 extra로 글꼴과 OCR 모델을 추가할 수 있습니다. 필요한 것만 설치하세요.

| Extra | 추가되는 것 |
|---|---|
| `jp`, `ko`, `zh-cn`, `zh-tw` | 일본어, 한국어, 중국어 간체, 중국어 번체 글꼴. 글꼴을 포함하지 않은 PDF를 표시하거나 텍스트를 쓸 때 사용 |
| `ar`, `he`, `hi`, `th` | 텍스트를 쓸 때 사용하는 아랍어, 히브리어, 힌디어(데바나가리), 태국어 글꼴 |
| `ocr` | 내장 [오프라인 OCR](ocr.md) 엔진용 모델 |
| `migrate` | [pymupdf 이전 도구](migration.md#codemod) |

```bash
pip install "pylopdf[jp,ocr]"
```

중국어, 일본어, 한국어 extra가 나뉘어 있는 것은 지역마다 일부 문자를 다르게 그리기 때문입니다. 문서에 맞는 것을 고르세요.

## 열기, 확인, 저장 { #open-inspect-save }

```python
import pylopdf

with pylopdf.open("input.pdf") as doc:     # pylopdf.open(pdf_bytes)도 가능
    print(len(doc.pages))
    print(doc.metadata["title"])
    doc.set_metadata({"title": "Report", "author": "Alice"})

    doc.save("out.pdf")                    # 압축하고 사용하지 않는 객체는 제외
    data = doc.to_bytes()                  # 같은 PDF를 bytes로
    doc.save("small.pdf", dedupe=True, object_streams=True)
    doc.save(
        "locked.pdf",
        encryption=pylopdf.Encryption(
            user_password="secret",
            owner_password="owner-secret",
            permissions=pylopdf.Permissions.PRINT,
        ),
    )
```

- **암호화된 파일:** 열 때 `password=`를 전달하거나, 연 뒤에 `doc.unlock(password)`를 호출합니다. 그래야 하는지는 `doc.needs_password`로 알 수 있습니다.
- **메타데이터만 읽기:** `pylopdf.probe(path)`는 문서 전체를 불러오지 않고 페이지 수, 버전, 메타데이터를 읽습니다. 대규모 파일 모음을 조사할 때 유용합니다.
- **손상된 파일:** 파일을 열기 위해 손상된 부분을 복구해야 했다면 pylopdf는 `PylopdfWarning`을 내고 `doc.is_repaired`를 설정합니다. 저장하면 정상적인 파일로 기록됩니다.

!!! tip "신뢰할 수 없는 출처의 파일"

    직접 관리하지 않는 파일은 `limits=pylopdf.DocumentLimits.web()`을 지정해
    여세요. 그러면 지나치게 크거나 악의적인 입력에 대해 메모리를 소진하는 대신
    `LimitError`를 발생시킵니다. [보안](security.md#untrusted-pdfs)을 참고하세요.

## 페이지, 텍스트, 검색 { #pages-text-search }

```python
with pylopdf.open("input.pdf") as doc:
    for page in doc.pages:                 # doc.pages[0], doc.pages[-1]도 가능. 인덱스는 0부터 시작
        print(page.index, page.rect)

    page = doc.pages[0]
    text = page.text()                     # 일반 텍스트
    words = page.words()                   # text, bbox, block, line, index를 가진 Word 레코드
    layout = page.layout()                 # blocks → lines → spans(bbox, size, font 포함)
    hits = page.search("total")            # 대소문자 구분 없음. Rect 리스트
    tables = page.tables()                 # bbox, rows, cells를 가진 Table 레코드
```

좌표 단위는 포인트(1/72인치)이며, 원점은 화면에 표시되는 페이지의 왼쪽 위입니다. 검색 결과, 추출한 텍스트, 그리기, 렌더링이 모두 같은 좌표를 사용하며 회전된 페이지에서도 마찬가지입니다.

## 렌더링 { #rendering }

```python
with pylopdf.open("input.pdf") as doc:
    doc.pages[0].pixmap(dpi=144).save("page-1.png")
    png = doc.pages[0].pixmap(dpi=300).to_bytes()  # PNG bytes
    pix = doc.pages[0].pixmap(dpi=144)            # NumPy나 Pillow용 RGB 픽셀
    pngs = doc.render(dpi=144, workers=4)       # 모든 페이지를 병렬로
    svg = doc.pages[0].svg()
```

## 페이지 편집 { #editing }

```python
with pylopdf.open("input.pdf") as doc:
    doc.pages.select([2, 0, 1])            # 유지하고 재정렬. 인덱스를 반복하면 복제
    del doc.pages[1]
    doc.pages.new()                        # 끝에 빈 A4 페이지 추가
    doc.pages.insert(1, doc.pages[0])      # 페이지 0의 복사본을 인덱스 1에
    doc.pages.move(0, -1)                  # 첫 페이지를 끝으로 이동
    doc.pages[0].set_rotation(90)
    doc.set_outline([(1, "Chapter 1", 0), (2, "Section 1.1", 1)])
    doc.save("edited.pdf")

with pylopdf.Document() as merged, pylopdf.open("a.pdf") as a, pylopdf.open("b.pdf") as b:
    merged.pages.extend(a.pages)           # a.pdf 전체
    merged.pages.extend(b.pages[:3])       # b.pdf의 처음 세 페이지
    merged.save("merged.pdf")
```

페이지를 추가, 삭제, 재정렬하면 페이지 인덱스가 바뀝니다. 이런 변경 전에 가져온 `Page` 객체는 `StalePageError`를 발생시키므로 `doc.pages[i]`로 다시 가져오세요.

## 그리기와 주석 { #drawing-annotations }

이미지, 오버레이, 선, 주석:

```python
with pylopdf.open("input.pdf") as doc, pylopdf.open("letterhead.pdf") as letterhead:
    page = doc.pages[0]
    page.insert_image((72, 72, 200, 200), filename="logo.png")
    page.show_pdf_page(page.rect, letterhead)       # 다른 PDF의 페이지 0을 벡터 그대로
    page.draw_line((36, 340), (400, 340), width=0.5, dashes="[3 2] 0")

    hits = page.search("important")
    if hits:
        page.add_highlight_annot(hits)
    for rect in page.search("Example")[:1]:
        page.insert_link({"kind": pylopdf.LINK_URI, "from": rect, "uri": "https://example.com/"})
    doc.save("annotated.pdf")
```

텍스트와 글꼴. 글꼴 인수를 지정하지 않으면 pylopdf는 라틴 문자 텍스트를 Helvetica로 쓰고, 다른 문자 체계는 `pylopdf[jp]` 같은 설치된 extra의 글꼴로 씁니다.

```python
with pylopdf.open("input.pdf") as doc:
    page = doc.pages[0]
    page.insert_text((40, 40), "CONFIDENTIAL", fontsize=18, color=(1, 0, 0))
    page.insert_text((40, 70), "社外秘", fontsize=18)                          # pylopdf[jp] 필요
    page.insert_text((40, 100), "简体中文", fontsize=18, font_language="zh-CN")  # pylopdf[zh-cn] 필요

    bold = pylopdf.Font(fontfile="NotoSansJP-Bold.otf")       # 어떤 OpenType 파일이든 가능
    width = bold.text_length("見出し", fontsize=16)             # 그렸을 때의 폭
    page.insert_text((page.rect.width - 40 - width, 140), "見出し", font=bold, fontsize=16)
    doc.save("text.pdf")
```

도형, 텍스트, 이미지를 순서대로 함께 그리려면 캔버스를 사용합니다. 그러면 각 글꼴과 이미지가 한 번만 저장됩니다.

```python
from pylopdf import Color

with pylopdf.open("input.pdf") as doc:
    with doc.pages[0].draw() as canvas:
        canvas.rect((36, 220, 400, 320), radius=6, fill=Color(0.95, 0.97, 1), stroke=Color(0.2, 0.4, 0.6))
        canvas.text("Report", (48, 250), size=16)
        canvas.textbox((48, 260, 390, 310), "Body paragraph.", size=11)
        with canvas.opacity(0.3):
            canvas.image((300, 230, 390, 300), "logo.png")
    doc.save("canvas.pdf")
```

태그 PDF/UA 파일 같은 새 문서를 쓰려면 [`pylopdf.create()`](api.md#create)를 사용하세요. 새 문서의 페이지도 같은 캔버스로 그립니다.

## 스캔 PDF, 폼, Markdown { #scans-forms-markdown }

```python
with pylopdf.open("scan.pdf") as doc:
    doc.pages[0].apply_ocr()                        # pylopdf[ocr] 필요. 보이지 않는 검색 가능 텍스트 추가
    doc.save("searchable.pdf")

with pylopdf.open("form.pdf") as doc:
    print(doc.get_form_fields())
    doc.set_form_field("customer", "Alice")   # 값과 표시 모양을 함께 기록
    doc.save("filled.pdf")

with pylopdf.open("input.pdf") as doc:
    md = doc.markdown()                       # 제목, 목록, 표
```

OCR은 [오프라인 OCR](ocr.md)에서 자세히 다루며, 다른 OCR 엔진이 인식한 텍스트를 추가하는 방법도 설명합니다.

## 아직 지원하지 않는 기능 { #not-supported }

민감 정보 가림 처리(redaction), 주석 편집이나 삭제, 폼 필드 생성, 증분 저장, 검증된 PDF/A와 PDF/UA 출력은 계획되어 있으며 [로드맵](https://github.com/yhay81/pylopdf/blob/main/ROADMAP.md)에서 확인할 수 있습니다. pylopdf는 PDF 파일만 엽니다.

HTML과 CSS를 쓰는 조판과 디지털 서명은 생태계의 도구에 맡깁니다. pylopdf를 typst나 pyHanko와 함께 쓰는 방법은 [생태계 연동](ecosystem.md)에서 소개합니다.

## 다음 단계 { #next-steps }

- 모든 클래스와 메서드는 [API 레퍼런스](api.md)
- 기존 코드를 옮긴다면 [pymupdf에서 이전](migration.md)
- 버그 보고나 기능 요청은 [기여 및 지원](contributing.md)
