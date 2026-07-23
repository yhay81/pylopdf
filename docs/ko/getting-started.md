---
title: 시작하기
description: pylopdf를 설치하고 편집, 렌더링, 추출, 그리기의 핵심 작업 흐름을 배웁니다.
---

# 시작하기

## 설치 { #installation }

```bash
pip install pylopdf
```

글꼴이 임베드되지 않은 CJK PDF를 렌더링하려면 함께 제공되는 Noto CJK 글꼴을
설치하세요. 렌더링할 때 자동으로 감지됩니다.

```bash
pip install pylopdf[cjk]
```

## 열기, 확인, 저장 { #open-inspect-save }

```python
import pylopdf

doc = pylopdf.open("input.pdf")           # pylopdf.open(stream=pdf_bytes)도 가능
print(doc.page_count)                     # len(doc)도 지원
print(doc.metadata["title"])
doc.set_metadata({"title": "보고서", "author": "Alice"})

doc.save("out.pdf")
data = doc.tobytes()
doc.save("small.pdf", garbage=True, deflate=True, object_streams=True)
doc.save("locked.pdf", user_pw="secret", permissions=pylopdf.Permissions.PRINT)
```

암호화된 PDF는`password=`로 열거나 나중에`doc.authenticate()`를 호출합니다.
`pylopdf.peek_metadata(path)`는 전체 파일을 파싱하지 않고 메타데이터와 페이지 수를
읽으므로 대규모 파일 모음을 조사할 때 유용합니다. 신뢰할 수 없는 파일을 처리할 때는
압축 해제 폭탄을 막기 위해`max_decompressed_size=`를 지정하세요. 페이지 콘텐츠와
디코딩된 이미지 크기를 포함해 열 때 각 스트림을 검사하며, 제한을 안전하게 계산할 수
없는 필터 체인은 거부됩니다.

## 페이지, 텍스트, 검색 { #pages-text-search }

```python
page = doc[0]                             # 0부터 시작, 음수는 끝에서부터
for page in doc:
    print(page.number, page.rect)

text = page.get_text()                    # 일반 텍스트
words = page.get_text("words")            # (x0, y0, x1, y1, word, block, line, word_no)
layout = page.get_text("dict")            # blocks → lines → spans（bbox, size, font, flags）
hits = page.search_for("합계")             # 대소문자 구분 없음, list[Rect]
```

모든 좌표는 왼쪽 위가 원점인**표시 공간**입니다. 회전된 페이지에서도 검색 결과,
레이아웃, 그리기, 렌더링이 같은 좌표계를 공유합니다.

## 렌더링 { #rendering }

```python
png = doc.render_page(0, dpi=300)                    # bytes（PNG）
pix = page.get_pixmap(scale=2)                       # NumPy/PIL용 RGBA8 픽셀
svg = doc.render_page_svg(0)
```

## 편집 { #editing }

```python
doc.delete_pages([1, 2])
doc.select([2, 0])                                   # 유지/재정렬（반복하면 복제）
doc.new_page(); doc.copy_page(0, to=1)

merged = pylopdf.Document()
merged.insert_pdf(pylopdf.open("a.pdf"))
merged.insert_pdf(pylopdf.open("b.pdf"), from_page=0, to_page=2, start_at=0)

doc.set_toc([[1, "1장", 1], [2, "1.1절", 2]])
page.set_rotation(90)
```

## 그리기와 주석 { #drawing-annotations }

```python
page.insert_image((72, 72, 200, 200), filename="logo.png")   # JPEG 패스스루/PNG 알파
page.insert_image(page.search_for("승인")[0], stream=stamp_png)
page.show_pdf_page(page.rect, letterhead)                    # 다른 PDF를 벡터로 겹치기
page.insert_text((40, 40), "CONFIDENTIAL", fontsize=18, color=(1, 0, 0))
page.add_highlight_annot(page.search_for("중요"))             # 검색 후 강조
page.add_link_annot(page.search_for("Example")[0], "https://example.com/")
```

## 스캔 PDF, 폼, Markdown { #scans-forms-markdown }

```python
page.insert_ocr_text_layer(ocr_words)     # 모든 OCR 결과로 검색 가능한 PDF 생성
doc.set_form_field("customer", "Alice")   # AcroForm 입력（NeedAppearances）
md = doc.to_markdown()                    # RAG에 바로 쓰는 Markdown
```

조판, PDF/A, 디지털 서명은[생태계 연동](ecosystem.md)을, pymupdf에서 이전한다면
[이전 가이드](migration.md)를 이어서 읽으세요.
