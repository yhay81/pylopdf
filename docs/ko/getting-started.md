---
title: 시작하기
description: pylopdf를 설치하고 편집, 렌더링, 추출, 그리기의 핵심 작업 흐름을 배웁니다.
---

# 시작하기

## 설치 { #installation }

```bash
pip install pylopdf
```

작업에 필요한 지역 또는 문자 체계의 글꼴만 설치합니다.

```bash
pip install "pylopdf[jp]"     # 일본어 fallback과 생성
pip install "pylopdf[ko]"     # 한국어
pip install "pylopdf[zh-cn]"  # 중국어 간체
pip install "pylopdf[zh-tw]"  # 중국어 번체
pip install "pylopdf[ar]"     # 아랍어 생성(he, hi, th도 제공)
```

네 CJK 지역 extra는 임베드되지 않은 Adobe CJK 글꼴의 렌더링과 subset 임베드
생성을 모두 지원합니다. 아랍어, 히브리어, 데바나가리, 태국어 패키지는 생성
provider입니다. 필요한 지역 글리프 규칙에 맞춰 `jp`, `ko`, `zh-cn`, `zh-tw`를
명시적으로 선택하세요. 범용 CJK extra는 없습니다.

미리 빌드된 wheel은 Linux(x86_64, ARM64), macOS(Apple silicon, Intel),
Windows(x86_64)의 CPython 3.11–3.15와 free-threaded 3.14t, 3.15t를 지원합니다.
free-threaded 사용법은 [동시성](concurrency.md)을, Pyodide와 Cloudflare Python
Workers는 [WebAssembly](wasm.md)를 참고하세요.

## 열기, 확인, 저장 { #open-inspect-save }

```python
import pylopdf

with pylopdf.open("input.pdf") as doc:     # 또는 pylopdf.open(stream=pdf_bytes)
    print(doc.page_count)                  # len(doc)도 사용 가능
    print(doc.metadata["title"])
    doc.set_metadata({"title": "Report", "author": "Alice"})

    doc.save("out.pdf")
    data = doc.tobytes()                   # 같은 PDF를 bytes로
    doc.save("small.pdf", garbage=True, deflate=True, use_objstms=True)
    doc.save(
        "locked.pdf",
        encryption=pylopdf.PDF_ENCRYPT_AES_256,
        user_pw="secret",
        permissions=pylopdf.PDF_PERM_PRINT,
    )
```

- **암호화된 파일**은 `password=`로 열거나, 연 뒤에 `doc.authenticate()`를
  호출합니다.
- **메타데이터만 읽기:** `pylopdf.peek_metadata(path)`는 문서 전체를 파싱하지
  않고 메타데이터와 페이지 수를 읽으므로 대규모 파일 모음을 조사할 때
  적합합니다. `max_file_size=`를 지정하면 파싱 전에 지나치게 큰 입력을
  거부합니다.
- **손상된 파일:** 깨진 cross-reference 데이터를 복구하면 pylopdf는
  `PylopdfWarning`을 발생시키고 `doc.is_repaired`(`peek_metadata`에서는
  `repaired`)를 설정합니다. 저장하면 깨끗한 cross-reference 데이터가 기록됩니다.

!!! tip "신뢰할 수 없는 입력"

    직접 관리하지 않는 파일을 열 때는 `limits=pylopdf.DocumentLimits.web()`을
    전달하세요. 파일 크기, 구조, 압축 해제, 해석된 텍스트를 제한하며, 메모리를
    소진하는 대신 `LimitError`를 발생시킵니다. 무거운 작업 전에는
    `doc.complexity`를 확인하세요. 모든 예산은
    [보안](security.md#untrusted-pdfs)을 참고하세요.

## 페이지, 텍스트, 검색 { #pages-text-search }

```python
with pylopdf.open("input.pdf") as doc:
    for page in doc:                       # 또는 doc[0], doc[-1]; 0부터 시작
        print(page.number, page.rect)

    page = doc[0]
    text = page.get_text()                 # 일반 텍스트
    words = page.get_text("words")         # (x0, y0, x1, y1, word, block, line, word_no)
    layout = page.get_text("dict")         # blocks → lines → spans (bbox, size, font 포함)
    hits = page.search_for("total")        # 대소문자 구분 없음; list[Rect]
```

모든 좌표는 왼쪽 위를 원점으로 하는 **표시 공간**을 사용합니다. 검색 결과,
레이아웃, 그리기, 렌더링은 회전된 페이지를 포함해 하나의 좌표계를 공유합니다.

## 렌더링 { #rendering }

```python
with pylopdf.open("input.pdf") as doc:
    doc[0].get_pixmap(dpi=144).save("page-1.png")
    png = doc[0].get_pixmap(dpi=300).tobytes()    # PNG bytes
    pix = doc[0].get_pixmap(dpi=144)               # NumPy나 Pillow용 RGB 픽셀
    pngs = doc.render_pages(dpi=144, workers=4)    # 모든 페이지를 병렬로
    svg = doc[0].get_svg_image()
```

## 편집 { #editing }

```python
with pylopdf.open("input.pdf") as doc:
    doc.select([2, 0, 1])                  # 유지하고 재정렬; 인덱스를 반복하면 복제
    doc.delete_pages([1])                  # 편집할 때마다 페이지 번호가 다시 매겨짐
    doc.new_page()                         # 끝에 빈 A4 페이지
    doc.fullcopy_page(0, to=1)             # 페이지 0의 독립 사본을 인덱스 1에
    doc[0].set_rotation(90)
    doc.set_outline([(1, "Chapter 1", 0), (2, "Section 1.1", 1)])
    doc.save("edited.pdf")

with pylopdf.Document() as merged, pylopdf.open("a.pdf") as a, pylopdf.open("b.pdf") as b:
    merged.insert_pdf(a)                   # a.pdf 전체
    merged.insert_pdf(b, from_page=0, to_page=2)  # b.pdf의 1–3쪽
    merged.save("merged.pdf")
```

## 그리기와 주석 { #drawing-annotations }

이미지, 오버레이, 주석:

```python
with pylopdf.open("input.pdf") as doc, pylopdf.open("letterhead.pdf") as letterhead:
    page = doc[0]
    page.insert_image((72, 72, 200, 200), filename="logo.png")  # JPEG는 그대로 통과
    page.show_pdf_page(page.rect, letterhead)                    # 다른 PDF의 페이지 0을 벡터 오버레이로
    page.draw_line((36, 340), (400, 340), width=0.5, dashes="[3 2] 0")

    hits = page.search_for("important")
    if hits:
        page.add_highlight_annot(hits)
    for rect in page.search_for("Example")[:1]:
        page.insert_link({"kind": pylopdf.LINK_URI, "from": rect, "uri": "https://example.com/"})
    doc.save("annotated.pdf")
```

텍스트와 글꼴. 글꼴 인수가 없으면 pylopdf는 라틴 문자에는 Helvetica를, 그 밖의
문자 체계에는 `pylopdf[jp]` 같은 설치된 지역 extra를 사용합니다.

```python
with pylopdf.open("input.pdf") as doc:
    page = doc[0]
    page.insert_text((40, 40), "CONFIDENTIAL", fontsize=18, color=(1, 0, 0))
    page.insert_text((40, 70), "社外秘", fontsize=18)                        # pylopdf[jp] 필요
    page.insert_text((40, 100), "简体中文", fontsize=18, font_language="zh-CN")  # pylopdf[zh-cn] 필요

    bold = pylopdf.Font(fontfile="NotoSansJP-Bold.otf")    # 어떤 OpenType 파일이든 가능
    width = bold.text_length("見出し", fontsize=16)          # 실제로 그려지는 폭과 일치
    page.insert_text((page.rect.width - 40 - width, 140), "見出し", font=bold, fontsize=16)

    semibold = pylopdf.Font(fontfile="NotoSansCJKjp-VF.otf", weight=600)  # 가변 글꼴은 축 위의 어떤 굵기든 선택 가능
    page.insert_text((40, 170), "見出し", font=semibold, fontsize=16)
    doc.save("text.pdf")
```

여러 도형과 텍스트를 하나의 그리기로 묶으며, 각 글꼴은 한 번만 임베드됩니다.

```python
with pylopdf.open("input.pdf") as doc:
    with doc[0].new_shape() as shape:
        shape.draw_rect((36, 220, 400, 320), radius=0.1)
        shape.finish(color=(0.2, 0.4, 0.6), fill=(0.95, 0.97, 1), width=1)
        shape.insert_text((48, 250), "Report", fontsize=16)
        shape.insert_textbox((48, 260, 390, 310), "Body paragraph.", fontsize=11)
    doc.save("shapes.pdf")
```

## 스캔 PDF, 폼, Markdown { #scans-forms-markdown }

```python
with pylopdf.open("scan.pdf") as doc:
    ocr_words = [(72, 90, 140, 104, "Invoice"), (146, 90, 190, 104, "#1024")]
    doc[0].insert_ocr_text_layer(ocr_words)   # 모든 OCR 결과 사용 가능: (x0, y0, x1, y1, text, ...)

with pylopdf.open("form.pdf") as doc:
    doc.set_form_field("customer", "Alice")   # 값과 그 appearance를 기록
    doc.save("filled.pdf")

with pylopdf.open("input.pdf") as doc:
    md = doc.to_markdown()                    # RAG용 제목, 목록, 표
```

직접 준비한 OCR 단어 대신 pylopdf 안에서 텍스트를 인식하려면
[오프라인 OCR](ocr.md)을 참고하세요.

조판, PDF/A, 디지털 서명은 [생태계 연동](ecosystem.md)을, pymupdf에서
이전한다면 [이전 가이드](migration.md)를 이어서 읽으세요.
