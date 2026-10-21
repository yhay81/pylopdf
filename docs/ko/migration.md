---
title: pymupdf에서 이전
description: 이전 도구, 이름별 대응표, 주의할 동작 차이를 바탕으로 pymupdf 코드를 pylopdf로 옮깁니다.
---

# pymupdf에서 이전

pylopdf는 pymupdf의 API를 따릅니다. 같은 이름은 같은 의미이며, `"words"` 튜플, `"dict"` 구조, 사각형을 반환하는 `search_for()`처럼 대부분의 코드가 의존하는 데이터 형태가 일치합니다. 다만 아직 그대로 바꿔 끼울 수 있는 대체품은 아닙니다. pymupdf API의 일부만 지원하고, **PDF 파일만** 열며(XPS, EPUB, 이미지는 열지 않음), 일부 동작을 의도적으로 바꿨습니다. 이 페이지는 그대로 쓸 수 있는 것, 다른 것, pylopdf가 구현하지 않은 부분 대신 쓸 것을 정리합니다.

## 자동 이전 { #codemod }

`python -m pylopdf.migrate`는 pymupdf 프로그램을 제자리에서 다시 씁니다. `migrate` extra로 설치합니다.

```bash
pip install "pylopdf[migrate]"
python -m pylopdf.migrate src/
```

`import fitz`와 `import pymupdf`를 바꾸고, 기계적으로 처리할 수 있는 대체(페이지 번호를 포함한 `get_toc`/`set_toc`, `embfile_*`, `xref_*` 스트림 읽기, `copy_page`, `Rect`/`Point`/`Matrix`의 제자리 변경 메서드 등)를 다시 쓰며, 여전히 사람이 손봐야 하는 모든 문 위에 `# TODO(pylopdf): …` 주석을 추가합니다. 그런 다음 해당 위치를 `path:line: message` 형식으로 나열합니다.

- `--diff`는 변경 내용을 쓰지 않고 출력합니다.
- `--check`는 변경될 파일이 있으면 상태 코드 1로 종료합니다. CI용입니다.

이 도구는 변수의 타입을 알 수 없으므로 diff를 검토하세요. 실행 시 pylopdf에 없는 pymupdf 이름을 사용하면 `AttributeError`가 발생하며, 그 메시지는 대체할 이름을 알려 주거나 해당 기능이 아직 지원되지 않는다고 알려 줍니다.

## 이름 대응표 { #mapping }

"같음"은 호출이 변경 없이 동작한다는 뜻이며, 비고에 다른 점을 적었습니다.

### 문서와 페이지 { #mapping-documents }

| pymupdf | pylopdf | 비고 |
|---|---|---|
| `import pymupdf`(또는 `fitz`) | `import pylopdf` | |
| `pymupdf.open(path)`, `open(stream=…)`, `open("pdf", data)` | 같음 | PDF만 지원. `password=`는 키워드 전용 |
| `doc[i]`, `len(doc)`, 반복 | 같음 | |
| `doc.metadata`, `set_metadata()` | 같음 | |
| `doc.needs_pass`, `authenticate()` | 같음 | 같은 반환 코드 |
| `doc.save(garbage=4, deflate=True)` | 같음 | 페이지, 주석, 폼 필드, 레이어는 `garbage`로 병합되지 않음 |
| `doc.save(encryption=…, user_pw=…)` | 같음 | `PDF_ENCRYPT_NONE`과 `PDF_ENCRYPT_AES_256`만 지원. 암호화 없이 비밀번호를 주면 무시하지 않고 예외 발생 |
| `doc.select()`, `delete_page()`, `delete_pages()`, `fullcopy_page()`, `new_page()` | 같음 | 삭제한 페이지의 폼 필드와 태그 PDF 구조도 제거 |
| `doc.copy_page(pno, to)` | `doc.fullcopy_page(pno, to)` | pylopdf는 페이지 객체 하나를 두 페이지가 공유하지 않음 |
| `doc.insert_pdf(docsrc, from_page=, to_page=, start_at=)` | 같음 | 가져온 폼 필드의 이름이 겹치면 `name+N`으로 이름을 바꿈. `widgets=`, `join_duplicates=`는 없음 |
| `doc.get_toc()`, `set_toc(toc)` | `doc.get_outline()`, `set_outline(entries)` | 페이지 번호가 1이 아니라 0부터 시작 |
| `doc.get_page_labels()`, `set_page_labels()`, `page.get_label()` | 같음 | |
| `doc.embfile_add/names/get/del/upd/info/count` | `doc.attachments` | 매핑: `add(name, data, description=…)`, `list(…)`, `[name].read()`, `del …[name]`, `[name].write(data)`, `len(…)`. `desc=`는 `description=` |
| `doc.xref_*`, `pdf_catalog()`, `pdf_trailer()` | `doc.objects`, `doc.catalog`, `doc.trailer` | PDF 구문 대신 타입이 있는 Python 값. [저수준 객체](api.md#low-level-objects) 참고 |

### 텍스트, 렌더링, 이미지 { #mapping-text }

| pymupdf | pylopdf | 비고 |
|---|---|---|
| `page.get_text()` | 같음 | 옵션 `text`, `words`, `blocks`, `dict`, `rawdict`. `html`, `xml`은 없음 |
| `page.search_for(text)` | 같음 | 사각형을 반환. `quads=`는 없음. `max_hits=`를 늘리지 않으면 결과는 최대 4,096개 |
| `page.get_textpage_ocr()` | `page.get_text_ocr()`, `page.apply_ocr()` | 내장 오프라인 OCR. [오프라인 OCR](ocr.md) 참고 |
| `pymupdf4llm.to_markdown(doc)` | `doc.to_markdown()` | 내장 |
| `page.get_pixmap(matrix=…, dpi=…)`, `doc.get_page_pixmap()` | 같음 | 배율 행렬만 지원. `colorspace=`, `annots=`는 아직 없음 |
| `pix.samples`, `width`, `height`, `n`, `alpha`, `stride`, `colorspace`, `save()` | 같음 | `save()`와 `tobytes()`는 PNG만 기록 |
| `page.get_svg_image()` | 같음 | 텍스트는 항상 경로로 그림 |
| `page.get_images()`, `get_image_info()`, `doc.extract_image()` | 같음 | `get_image_info(hashes=True)`는 아직 지원하지 않음 |
| `page.get_drawings()` | 같음 | `extended=`의 클리핑과 그룹 정보는 없음 |
| `doc.rewrite_images(dpi_threshold=, dpi_target=, quality=)` | 같음 | JPEG로만 다시 인코딩하므로 리샘플링에는 `quality`가 필요. `None` 대신 통계를 반환 |

### 그리기와 주석 { #mapping-drawing }

| pymupdf | pylopdf | 비고 |
|---|---|---|
| `page.insert_text()`, `insert_textbox()` | 같음 | `font=`, `fontbuffer=`, `font_language=`도 받음. [텍스트와 글꼴](api.md#fonts) 참고 |
| `pymupdf.Font(...)`, `font.text_length()`, `has_glyph()` | 같음 | Standard 14 글꼴, OpenType 파일, 설치된 글꼴 extra. pymupdf의 다른 내장 글꼴과 `script=`, `ordering=`, `embed=`는 사용할 수 없음 |
| `page.insert_image(rect, filename=/stream=/pixmap=)` | 같음 | JPEG, PNG, `Pixmap`. 다른 형식은 Pillow로 변환. 같은 이미지를 두 번 삽입해도 한 번만 저장되므로 `xref=`가 필요 없음 |
| `page.show_pdf_page(rect, docsrc, pno)` | 같음 | 원본이 같은 문서여도 됨 |
| `page.new_shape()`, `shape.draw_*`, `finish()`, `insert_text()`, `commit()` | 같음 | `with page.new_shape() as shape:`는 자동으로 커밋. `morph=`와 `oc=`는 아직 지원하지 않음 |
| `page.draw_line()`, `draw_rect()` 및 다른 `draw_*` | 같음 | 스타일 옵션은 키워드 전용 |
| `page.annots()`, `annot.type`, `rect`, `info`, `colors`, `flags` | 같음 | 현재는 읽기 전용 |
| `page.add_highlight_annot()`, `page.insert_link()` | 같음 | `insert_link`는 웹 링크(`LINK_URI`)만 생성 |
| `page.widgets()` | `doc.get_form_fields()`, `doc.set_form_field(name, value)` | 폼은 문서 단위로 다룸 |

### 기하 { #mapping-geometry }

| pymupdf | pylopdf | 비고 |
|---|---|---|
| `Point`, `Matrix`, `Rect`, `IRect`, `Quad`와 그 연산자 | 같음 | 변경 불가 |
| `p.transform(m)`, `m.invert()` | `p = p * m`, `m = ~m` | |
| `r.include_point(p)`, `include_rect(o)`, `intersect(o)`, `normalize()`, `transform(m)` | `r = r | p`, `r | o`, `r & o`, `r.normalized()`, `r * m` | |
| `m.prerotate(deg)`, `prescale(sx, sy)`, `preshear(sx, sy)`, `pretranslate(tx, ty)` | `m = Matrix(deg) * m`, `Matrix(sx, sy) * m`, `Matrix(sx, sy, 1) * m`, `Matrix(1, 0, 0, 1, tx, ty) * m` | |

## 동작 차이 { #behavioral-differences }

- **기하 값은 변경할 수 없습니다.** `Point`, `Matrix`, `Rect`, `IRect`, `Quad`는 변경할 수 없는 네임드 튜플입니다. 제자리 변경 메서드를 호출하는 대신 연산자의 결과를 대입하세요. pymupdf와 마찬가지로, 일반 튜플과 달리 `p + q`는 좌표를 더하고 `Rect(0, 0, 0, 0)`은 거짓입니다.
- **오래된 페이지는 예외를 발생시킵니다.** 페이지를 추가, 삭제, 재정렬한 뒤에는 그 전에 가져온 `Page`가 조용히 다른 페이지를 가리키는 대신 `StalePageError`를 발생시킵니다. `doc[i]`로 다시 가져오세요.
- **예외.** `ValueError`의 하위 클래스인 `PdfError`가 기본 클래스이므로 `except ValueError`는 계속 동작합니다. `FileDataError`와 `EmptyFileError`는 pymupdf의 이름을 유지합니다. 없는 파일은 Python 내장 `FileNotFoundError`를 발생시키므로 `except pymupdf.FileNotFoundError`를 `except FileNotFoundError`로 바꾸세요.
- **회전된 페이지.** 추출, 검색, 그리기, 렌더링은 모두 화면에 표시되는 페이지의 좌표를 사용하며 회전된 페이지에서도 마찬가지입니다.
- **페이지 아래쪽 근처의 `insert_text`.** pylopdf는 보이는 페이지 영역(`page.rect`)을 기준으로 어떤 줄이 들어가는지 판단하고, pymupdf는 회전 전 페이지의 아래쪽 가장자리까지 잽니다. 일반적인 회전하지 않은 페이지에서는 둘이 일치합니다.
- **글꼴은 엄격합니다.** pylopdf는 조용히 다른 글꼴을 쓰는 대신 예외를 발생시킵니다. 예를 들어 글꼴에 문자가 없을 때나, `is_bold=True`로 설치되지 않은 볼드 글꼴을 요청할 때입니다.
- **폼 병합.** `insert_pdf()`는 pymupdf의 기본값 `widgets=True`처럼 항상 폼 필드를 복사합니다. 받는 문서에 이미 있는 이름의 필드는 `name+1`, `name+2` 등으로 이름을 바꾸므로, 같은 폼의 사본들이 각자 값을 유지합니다.
- **표와 Markdown.** `find_tables()`는 기본적으로 선으로 그린 표를 감지합니다. 테두리 없는 표에는 `strategy="text"`를 전달하세요. `to_markdown()`은 감지한 표를 읽기 순서에 맞게 배치하고 주변 단락에서 그 텍스트를 제거합니다.

## 구현하지 않은 기능의 대안 { #deliberate-scope }

| pymupdf 기능 | 대안 |
|---|---|
| Story API, `insert_htmlbox`(조판) | [typst](ecosystem.md#typesetting) |
| 디지털 서명 | [pyHanko](ecosystem.md#signatures) |
| 증분 저장 | 계획됨. 그때까지 pylopdf는 파일 전체를 다시 쓰며, 서명은 pyHanko가 처리 |
| XPS, EPUB, CBZ, 이미지 열기 | 계획 없음. pylopdf는 PDF 전용 |

## 예제 { #worked-example }

```python
# pymupdf
import pymupdf
doc = pymupdf.open("in.pdf")
page = doc[0]
for rect in page.search_for("total"):
    page.add_highlight_annot(rect)
pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
pix.save("page.png")
doc.save("out.pdf", garbage=4, deflate=True)
```

```python
# pylopdf
import pylopdf
doc = pylopdf.open("in.pdf")
page = doc[0]
page.add_highlight_annot(page.search_for("total"))   # 리스트 전체를 받음
page.get_pixmap(matrix=pylopdf.Matrix(2, 2)).save("page.png")
doc.save("out.pdf", garbage=4, deflate=True)
```
