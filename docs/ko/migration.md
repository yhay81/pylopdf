---
title: pymupdf에서 이전
description: 이전 도구, 이름별 대응표, 주의할 동작 차이를 바탕으로 pymupdf 코드를 pylopdf로 옮깁니다.
---

# pymupdf에서 이전

pylopdf는 pymupdf가 PDF 파일로 할 수 있는 모든 일을 자체 API로 하는 것을 목표로 합니다. 다만 그대로 바꿔 끼울 수 있는 대체품은 아닙니다. **PDF 파일만** 열며(XPS, EPUB, 이미지는 열지 않음), 일부 동작을 의도적으로 바꿨고, 다음 릴리스에서 API를 다시 설계하며 이전 도구도 각 부분이 반영될 때마다 이를 따릅니다. 문서와 그 페이지, 저장, 텍스트 읽기, 표, OCR은 이미 새 API인 `pylopdf.open(source)`, `doc.pages`, `doc.save()`, `page.text()`, `page.layout()`, `page.tables()`, `page.ocr()`을 사용합니다. 이 페이지는 그대로 쓸 수 있는 것, 다른 것, pylopdf가 아직 구현하지 않은 부분 대신 쓸 것을 정리합니다.

## 자동 이전 { #codemod }

`python -m pylopdf.migrate`는 pymupdf 프로그램을 제자리에서 다시 씁니다. `migrate` extra로 설치합니다.

```bash
pip install "pylopdf[migrate]"
python -m pylopdf.migrate src/
```

`import fitz`와 `import pymupdf`를 바꾸고, 기계적으로 처리할 수 있는 대체(문서 열기, `doc.pages`가 대신하는 페이지 작업, 페이지 번호를 포함한 `get_toc`/`set_toc`, `embfile_*`, `xref_*` 스트림 읽기, 텍스트와 표 읽기, 벡터 그림과 이미지 배치, `Rect`/`Point`/`Matrix`의 제자리 변경 메서드 등)를 다시 쓰며, 여전히 사람이 손봐야 하는 모든 문 위에 `# TODO(pylopdf): …` 주석을 추가합니다. 그런 다음 해당 위치를 `path:line: message` 형식으로 나열합니다. 같은 명령으로 pylopdf 0.15용으로 작성한 프로그램도 업그레이드할 수 있습니다.

- `--diff`는 변경 내용을 쓰지 않고 출력합니다.
- `--check`는 변경될 파일이 있으면 상태 코드 1로 종료합니다. CI용입니다.

이 도구는 변수의 타입을 알 수 없으므로 diff를 검토하세요. 변수에 대한 모든 대입이 `doc = fitz.open(path)`처럼 문서를 여는 경우에만 그 변수의 인덱싱, 길이 측정, 반복을 `.pages`를 거치도록 바꿉니다. 함수 매개변수에 대한 `doc[0]`은 그대로 둡니다. 실행 시 pylopdf에 없는 pymupdf 이름을 사용하면 `AttributeError`가 발생하며, 그 메시지는 대체할 이름을 알려 주거나 해당 기능이 아직 지원되지 않는다고 알려 줍니다. 또한 `doc[0]`이나 `for page in doc`은 `doc.pages`를 안내하는 `TypeError`를 발생시킵니다.

## 이름 대응표 { #mapping }

"같음"은 호출이 변경 없이 동작한다는 뜻이며, 비고에 다른 점을 적었습니다.

### 문서와 페이지 { #mapping-documents }

| pymupdf | pylopdf | 비고 |
|---|---|---|
| `import pymupdf`(또는 `fitz`) | `import pylopdf` | |
| `pymupdf.open(path)`, `open(stream=…)`, `open("pdf", data)` | `pylopdf.open(path)`, `pylopdf.open(data)` | PDF만 지원. `password=`는 키워드 전용. 파일 객체는 먼저 읽어서 전달 |
| `pymupdf.open()`, `pymupdf.Document()` | `pylopdf.Document()` | 빈 문서 |
| `doc[i]`, `doc.load_page(i)`, `len(doc)`, `doc.page_count`, 반복 | `doc.pages[i]`, `len(doc.pages)`, `doc.pages` 반복 | `doc.pages`는 pymupdf의 `doc.pages(start, stop)`처럼 슬라이스도 지원 |
| `page.number`, `page.parent` | `page.index`, `page.document` | |
| `doc.metadata`, `set_metadata(m)` | `doc.metadata`, `doc.metadata.update(m)` | 존재하는 항목의 실시간 매핑. 없는 항목은 `.get(key, "")`로 읽고 `del doc.metadata[key]`로 삭제. `created`와 `modified`가 `creationDate`와 `modDate`를, `doc.version`과 `doc.encryption_method`가 `format`과 `encryption`을 대신함 |
| `doc.get_xml_metadata()`, `set_xml_metadata(xml)`, `del_xml_metadata()` | `doc.xmp`, `doc.xmp = xml`, `del doc.xmp` | 패킷이 없으면 `None`. 읽기와 쓰기는 1 MiB에서 멈춤 |
| `doc.language`, `set_language(tag)` | `doc.language`, `doc.language = tag` | 완전한 태그를 다룸. pymupdf는 최대 세 글자만 유지하므로 `zh-TW`와 `zh-HK`가 모두 `zh-Hant`가 됨. `del doc.language`로 삭제 |
| `doc.pagemode`, `pagelayout`, `markinfo`와 그 `set_*` 메서드 | `doc.page_mode`, `page_layout`, `mark_info`에 대입 | `PageMode`, `PageLayout` 열거형과 `MarkInfo` 레코드. pymupdf는 대소문자를 무시했지만 이름은 정확히 일치해야 함 |
| `doc.has_links()` | `any(page.links for page in doc.pages)` | |
| `doc.has_annots()` | `any(page.annotations for page in doc.pages)` | |
| `doc.is_closed` | 같음 | |
| `doc.name` | `doc.path` | `pathlib.Path`. bytes에서 열었거나 새로 만든 빈 문서는 `None`. 모든 문서가 PDF이므로 `is_pdf`는 없어짐 |
| `doc.needs_pass`, `doc.is_encrypted` | `doc.needs_password` | 문서 잠금을 해제할 때까지 True |
| `doc.authenticate(password)` | `doc.unlock(password)` | `Access.USER` 또는 `Access.OWNER`를 반환하고, 비밀번호가 틀리면 0을 반환하는 대신 `PasswordError` 발생 |
| `doc.permissions`, `is_dirty` | 같음 | `permissions`는 `Permissions` 플래그 값. `-4` 대신 `Permissions.ALL` |
| `doc.save(garbage=4, deflate=True)` | `doc.save(dedupe=True, renumber=True)` | 저장은 기본적으로 스트림을 압축하고 사용하지 않는 객체를 제거. `garbage=0`은 `prune=False`, `deflate=False`는 `compress=False`가 됨. 페이지, 주석, 폼 필드, 레이어는 병합되지 않음 |
| `doc.save(encryption=PDF_ENCRYPT_AES_256, user_pw=…, owner_pw=…, permissions=…)` | `doc.save(encryption=Encryption(user_password=…, owner_password=…, permissions=…))` | AES-256만 지원 |
| `doc.tobytes(...)`, `doc.write(...)` | `doc.to_bytes(...)` | `save()`와 같은 옵션 |
| `doc.save(use_objstms=True)` | `doc.save(object_streams=True)` | |
| `doc.select(indexes)` | `doc.pages.select(indexes)` | 남기지 않은 페이지의 폼 필드와 태그 PDF 구조도 제거 |
| `doc.delete_page(i)`, `delete_pages(a, b)` | `del doc.pages[i]`, `del doc.pages[a:b + 1]` | 삭제할 페이지를 리스트로 지정했다면 `doc.pages.select(...)`로 나머지 페이지를 남김 |
| `doc.new_page(pno, width=, height=)` | `doc.pages.new((width, height), index=pno)` | `index`를 지정하지 않으면 끝에 추가 |
| `doc.copy_page(pno, to)`, `fullcopy_page(pno, to)` | `doc.pages.insert(to, doc.pages[pno])` | pylopdf는 페이지 객체 하나를 두 페이지가 공유하지 않음 |
| `doc.move_page(pno, to)` | `doc.pages.move(pno, index)` | `index`는 페이지가 옮겨진 뒤의 위치. pymupdf는 페이지 `to` 앞에 삽입했음 |
| `doc.insert_pdf(docsrc, from_page=a, to_page=b, start_at=i)` | `doc.pages.insert(i, docsrc.pages[a:b + 1])`, `doc.pages.extend(docsrc.pages)` | 가져온 폼 필드의 이름이 겹치면 `name+N`으로 이름을 바꿈. `widgets=`, `join_duplicates=`는 없음 |
| `doc.get_toc()`, `set_toc(toc)` | `doc.outline.entries()`, `doc.outline = entries` | `[level, title, page]` 리스트 대신, `destination`이 0부터 시작하는 페이지 번호의 `Destination`인 `OutlineEntry` 레코드 |
| `doc.outline`, `item.next`, `item.down` | `doc.outline`, `item.children`, `doc.outline.walk()` | `doc.outline`은 첫 항목이 아니라 최상위 `OutlineItem` 핸들의 시퀀스 |
| `doc.set_toc_item(i, ...)`, `del_toc_item(i)` | `OutlineItem`의 `title`, `destination`, `color`, `bold`, `italic`, `is_open`에 대입 | `del_toc_item`은 항목을 회색으로 바꾸고 대상을 없앴습니다 |
| `doc.get_page_labels()`, `set_page_labels(rules)`, `page.get_label()` | `doc.page_labels`, `page.label` | 딕셔너리 대신 `PageLabelRange(start, style, prefix, first)` 레코드 |
| `doc.get_page_numbers(label)` | `doc.pages.labeled(label)` | |
| `page.set_rotation(deg)` | `page.rotation = deg` | |
| `page.mediabox`, `set_mediabox(r)` | `page.boxes.media`, `page.boxes.media = r` | |
| `page.cropbox`, `artbox`, `bleedbox`, `trimbox`, `set_cropbox(r)`, `set_artbox(r)` 등 | `page.boxes.crop`, `.art`, `.bleed`, `.trim`, 각각 대입 가능 | 저장된 그대로의 PDF 사용자 공간. pymupdf는 MediaBox 위쪽을 기준으로 y를 뒤집었습니다 |
| `page.transformation_matrix`, `rotation_matrix`, `derotation_matrix` | `page.user_to_display` | 회전을 포함해 PDF 사용자 공간을 표시 좌표로 옮깁니다 |
| `doc.embfile_add/names/get/del/upd/info/count` | `doc.attachments` | 매핑: `add(name, data, description=…)`, `list(…)`, `[name].read()`, `del …[name]`, `[name].write(data)`, `len(…)`. `desc=`는 `description=` |
| `doc.xref_*`, `pdf_catalog()`, `pdf_trailer()` | `doc.objects`, `doc.catalog`, `doc.trailer` | PDF 구문 대신 타입이 있는 Python 값. [저수준 객체](api.md#low-level-objects) 참고 |

### 텍스트, 렌더링, 이미지 { #mapping-text }

| pymupdf | pylopdf | 비고 |
|---|---|---|
| `page.get_text()`, `get_text("text")` | `page.text()` | |
| `page.get_text("words")` | `page.words()` | 튜플 대신 `text`, `bbox`, `block`, `line`, `index`를 가진 `Word` 레코드 |
| `page.get_text("blocks")` | `page.layout().blocks` | 튜플 대신 `text`, `bbox`, `lines`를 가진 `Block` 레코드 |
| `page.get_text("dict")`, `get_text("rawdict")` | `page.layout().to_dict()`, `page.layout(chars=True).to_dict()` | 같은 딕셔너리를 반환. `page.layout()` 자체는 레코드를 반환. `html`, `xml`은 없음 |
| `page.search_for(text)` | `page.search(text)` | 사각형을 반환. `quads=`는 없음. `max_hits=`를 늘리지 않으면 결과는 최대 4,096개 |
| `page.find_tables()` | `page.tables()` | `Table` 레코드 리스트. `table.rows`가 `extract()`를, `len(table.rows)`가 `row_count`를 대신하고, `table.cells`는 각 셀의 상자를 행별로 담음. 조정용 키워드, `header`, `to_pandas()`는 아직 없음 |
| `page.get_textpage_ocr()` | `page.ocr()`, `page.apply_ocr()` | 내장 오프라인 OCR. [오프라인 OCR](ocr.md) 참고 |
| `pymupdf4llm.to_markdown(doc)` | `doc.markdown()` | 내장 |
| `page.get_pixmap(matrix=…, dpi=…, annots=…)` | `page.pixmap(dpi=…, annotations=…)` | `dpi`와 `matrix` 중 하나만 전달. 평행 이동은 픽셀을 움직이지 않으며 pixmap에는 원점이 없음. `colorspace=`는 `Colorspace.GRAY` 또는 `Colorspace.RGB`이며 CMYK는 없음. `doc.get_page_pixmap(i)`는 `doc.pages[i].pixmap()`으로 바뀜 |
| `pix.samples`, `pix.samples_mv` | `bytes(pix)`, `memoryview(pix)` | `numpy.asarray(pix)`는 복사 없이 `(height, width, channels)` 모양으로 픽셀을 읽음 |
| `pix.n` | `pix.channels` | |
| `pix.tobytes(output, jpg_quality=…)`, `pix.save(path, output, jpg_quality=…)` | `pix.to_bytes(format, quality=…)`, `pix.save(path, format=…, quality=…)` | PNG와 JPEG. 다른 형식은 Pillow에 맡김 |
| `pix.width`, `height`, `alpha`, `stride`, `colorspace` | 같음 | `colorspace`는 `Colorspace.GRAY` 또는 `Colorspace.RGB`. `csRGB`와 `csGRAY`는 `Colorspace.RGB`와 `Colorspace.GRAY`로 바뀜 |
| `page.get_svg_image()` | `page.svg()` | 텍스트는 항상 경로로, 72 dpi로 그림 |
| `page.get_images()` | `page.resources.images` | 튜플 대신 `xref`, `width`, `height`, `bits`, `colorspace`, `filters`, 마스크가 있는 `ImageObject` 핸들 |
| `doc.extract_image(xref)` | `doc.images[xref].extract()` | `format`, `data`, `resolution`이 있는 `EncodedImage`. 크기, 색 공간, 마스크는 `doc.images[xref]`의 속성 |
| `pymupdf.Pixmap(doc, xref)` | `doc.images[xref].to_pixmap()` | `alpha=True`는 이미지의 마스크를 적용 |
| `page.get_image_info()` | `page.content(text_runs=False, paths=False).images` | `xref`, `bbox`, `matrix`(pymupdf의 `transform`), 크기, 색 공간, 마스크, 해상도가 있는 `Image` 레코드. `hashes=`는 아직 지원하지 않음 |
| `page.get_drawings()`, `get_cdrawings()` | `page.content(text_runs=False, images=False).paths` | `segments`, `fill`, `fill_rule`, `stroke`, `bbox`가 있는 `Path` 레코드. `extended=True`가 별도 항목으로 반환하던 클립, 그룹, 마크된 콘텐츠는 각 경로의 `context`에 있음 |
| `page.get_texttrace()`, `get_bboxlog()`, `get_displaylist()`, `run()`, `get_image_rects()` | `page.content()` | 글리프가 있는 텍스트 런, 경로, 이미지를 불변 값으로 반환. [페이지 내용](api.md#content) 참고 |
| `doc.rewrite_images(dpi_threshold=, dpi_target=, quality=)` | 같음 | JPEG로만 다시 인코딩하므로 리샘플링에는 `quality`가 필요. `None` 대신 통계를 반환 |

### 그리기와 주석 { #mapping-drawing }

| pymupdf | pylopdf | 비고 |
|---|---|---|
| `page.insert_text(point, text)`, `insert_textbox(rect, text)` | `canvas.text(text, at)`, `canvas.textbox(rect, text)` | `with page.draw() as canvas:` 안에서 호출함. `fontsize=`는 `size=`, `color=`는 `fill=Color(...)`, `lineheight=`는 `line_height=`가 되고, `fontname=`, `fontfile=`, `fontbuffer=`는 `font=`로 전달하는 `Font`가 됨. `align=`은 `"left"`, `"center"`, `"right"`, `"justify"`를 받음. `textbox`는 `TextFit`을 반환하며, 아무것도 그리지 않았으면 `spare_height`가 음수임. [텍스트 쓰기](api.md#writing-text) 참고 |
| `pymupdf.Font(...)` | `Font.standard(name)`, `Font.from_file(path)`, `Font.from_bytes(data)`, `Font.installed(language=...)` | PostScript 이름으로 지정하는 Standard 14 글꼴(`"helv"`는 `"Helvetica"`), OpenType 파일, 설치된 글꼴 extra. pymupdf의 다른 내장 글꼴과 `script=`, `ordering=`, `embed=`는 사용할 수 없음 |
| `font.text_length(text, fontsize)`, `pymupdf.get_text_length(text, fontname, fontsize)` | `font.text_width(text, size)` | `size`에는 기본값이 없음. `font.text_height(text, size, width=...)`는 줄바꿈한 텍스트를 `canvas.textbox`가 그리는 대로 잼 |
| `font.has_glyph(ord(c))` | `font.has_glyph(c)` | 문자 자체를 받고, 글리프 번호가 아니라 불리언을 반환 |
| `pymupdf.Base14_fontnames`, `Base14_fontdict` | `typing.get_args(pylopdf.StandardFontName)` | `Font.standard()`가 받는 14개의 PostScript 이름 |
| `page.insert_image(rect, filename=/stream=/pixmap=)` | `canvas.image(rect, source)` | `with page.draw() as canvas:` 안에서 호출함. source는 경로, JPEG나 PNG bytes, 또는 `Pixmap`이고, `keep_proportion=`은 `keep_aspect=`가 됨. `rotate=`는 시계 방향이며 pymupdf는 반시계 방향임. 같은 이미지를 두 번 그려도 한 번만 저장되므로 `xref=`가 필요 없고, 각 배치의 `xref`는 `page.content().images`로 알 수 있음 |
| `page.show_pdf_page(rect, docsrc, pno)` | `canvas.page(rect, docsrc.pages[pno])` | 같은 문서의 페이지여도 됨. `clip=`과 `rotate=`는 아직 없음 |
| `page.new_shape()`, `shape.draw_*`, `finish()`, `insert_text()`, `commit()` | `with page.draw() as canvas:` | 캔버스는 경로와 텍스트를 호출한 순서대로 그리고 블록이 끝날 때 기록함. `finish()`의 스타일은 각 호출의 `fill=`과 `stroke=`가 됨. [캔버스](api.md#canvas) 참고 |
| `page.draw_line()`, `draw_rect()`, `draw_circle()`, `draw_oval()`, `draw_polyline()`, `draw_bezier()` | `canvas.line()`, `rect()`, `circle()`, `ellipse()`, `polygon(close=False)`, `bezier()` | `color=`, `width=`, `dashes=`, `lineCap=`, `lineJoin=`는 `stroke=Stroke(paint=Color(...), ...)`가, `fill=`은 `Color`가, 불투명도는 `Color`의 알파가 됨. `rect(radius=)`는 포인트 단위이며 pymupdf에서는 짧은 변에 대한 비율임 |
| `page.draw_quad()`, `draw_sector()`, `draw_curve()`, `draw_squiggle()`, `draw_zigzag()` | `canvas.polygon()`, `canvas.path(PathBuilder().sector(...))`, `canvas.bezier()`, `PathBuilder().wave()`, `PathBuilder().zigzag()` | `PathBuilder`로 형상을 만듦. 부채꼴 각도는 x축에서 시계 방향으로 잰 도(degree) |
| `page.annots()`, `page.first_annot`, `annot.type`, `rect`, `info`, `colors`, `flags`, `parent` | `page.annotations`, `annotation.type`, `rect`, `contents`를 비롯한 텍스트, `color`, `interior_color`, `flags`, `page` | 타입이 있는 프로퍼티를 가진 `Annotation` 핸들입니다. `type`은 `AnnotationType` 멤버이고 `info`와 `colors` 딕셔너리는 프로퍼티가 됩니다. 링크와 위젯은 목록에 없고 팝업은 그 주석에 속하며, 프로퍼티는 현재 읽기 전용입니다 |
| `page.add_highlight_annot(quads)`, `page.delete_annot(annot)` | `page.annotations.add(Highlight(quads))`, `del page.annotations[i]` | `color`는 `Color`이고 `content`는 `contents`가 됩니다. 주석을 제거하면 팝업도 함께 제거됩니다 |
| `page.get_links()`, `page.links()`, `page.first_link`, `page.load_links()` | `page.links` | 딕셔너리와 `Link` 객체의 연결 대신 `rect`와 `target`을 가진 `Link` 핸들의 시퀀스 |
| `page.insert_link(link)`, `page.update_link(link)`, `page.delete_link(link)` | `page.links.add(rect, target)`, `link.rect`와 `link.target`에 대입, `del page.links[i]` | 대상은 목차와 같아서 URI 문자열, `Destination(page, point, zoom)`, `RemoteDestination(file, page)`, `Launch(file)`, `NamedAction(name)` 중 하나이며, `LINK_*` 종류 대신 대상에 `isinstance`를 씁니다 |
| `page.widgets()`, `widget.field_name`, `field_type`, `field_value`, `field_flags`, `choice_values`, `widget.update()` | `doc.form`, `field.name`, `type`, `value`, `flags`, `choices`, `doc.form[name].value`에 대입 | 필드는 문서 단위의 `Field` 핸들로 다룸. `value`에 대입하면 즉시 표시 모양을 다시 생성하고, `doc.form.font`로 글꼴을 고름. 위젯 핸들은 Interaction 마일스톤에서 추가됨 |

### 기하 { #mapping-geometry }

| pymupdf | pylopdf | 비고 |
|---|---|---|
| `Point`, `Matrix`, `Rect`, `IRect`, `Quad`와 그 연산자 | 같음 | 변경 불가 |
| `p.transform(m)`, `m.invert()` | `p = p * m`, `m = ~m` | |
| `r.include_point(p)`, `include_rect(o)`, `intersect(o)`, `normalize()`, `transform(m)` | `r = r | p`, `r | o`, `r & o`, `r.normalized()`, `r * m` | |
| `m.prerotate(deg)`, `prescale(sx, sy)`, `preshear(sx, sy)`, `pretranslate(tx, ty)` | `m = Matrix(deg) * m`, `Matrix(sx, sy) * m`, `Matrix(sx, sy, 1) * m`, `Matrix(1, 0, 0, 1, tx, ty) * m` | |

## 동작 차이 { #behavioral-differences }

- **기하 값은 변경할 수 없습니다.** `Point`, `Matrix`, `Rect`, `IRect`, `Quad`는 변경할 수 없는 네임드 튜플입니다. 제자리 변경 메서드를 호출하는 대신 연산자의 결과를 대입하세요. pymupdf와 마찬가지로, 일반 튜플과 달리 `p + q`는 좌표를 더하고 `Rect(0, 0, 0, 0)`은 거짓입니다.
- **오래된 페이지는 예외를 발생시킵니다.** 페이지를 추가, 삭제, 재정렬한 뒤에는 그 전에 가져온 `Page`가 조용히 다른 페이지를 가리키는 대신 `StalePageError`를 발생시킵니다. `doc.pages[i]`로 다시 가져오세요.
- **예외.** `ValueError`의 하위 클래스인 `PdfError`가 기본 클래스이므로 `except ValueError`는 계속 동작합니다. 읽을 수 있는 PDF가 아닌 입력은 빈 입력을 포함해, pymupdf가 `FileDataError`나 `EmptyFileError`를 발생시키는 곳에서 `MalformedPdfError`를 발생시킵니다. 없는 파일은 Python 내장 `FileNotFoundError`를 발생시키므로 `except pymupdf.FileNotFoundError`를 `except FileNotFoundError`로 바꾸세요.
- **회전된 페이지.** 추출, 검색, 그리기, 렌더링은 모두 화면에 표시되는 페이지의 좌표를 사용하며 회전된 페이지에서도 마찬가지입니다.
- **텍스트 배치.** `canvas.textbox`는 `canvas.text`와 마찬가지로 줄을 `line_height × size` 간격으로 배치하며, `Font.text_height`가 재는 높이, 즉 첫 줄의 ascender부터 마지막 줄의 descender까지를 정확히 필요로 합니다. pymupdf의 `insert_textbox`는 둘째 줄부터 줄 높이 × ascender 간격으로 배치하고 descender를 하나 더 세므로, 줄 위치가 다르고 pymupdf에서는 들어가지 않는 텍스트가 들어갈 수 있습니다. `canvas.text`는 모든 줄을 그리지만, pymupdf의 `insert_text`는 페이지 아래로 벗어나는 줄을 그리지 않습니다.
- **글꼴은 엄격합니다.** pylopdf는 조용히 다른 글꼴을 쓰는 대신 예외를 발생시킵니다. 예를 들어 글꼴에 문자가 없을 때나, `is_bold=True`로 설치되지 않은 볼드 글꼴을 요청할 때입니다.
- **폼 병합.** `doc.pages.insert()`와 `extend()`는 pymupdf의 기본값 `widgets=True`처럼 항상 폼 필드를 복사합니다. 받는 문서에 이미 있는 이름의 필드는 `name+1`, `name+2` 등으로 이름을 바꾸므로, 같은 폼의 사본들이 각자 값을 유지합니다.
- **표와 Markdown.** `tables()`는 기본적으로 선으로 그린 표를 감지합니다. 테두리 없는 표에는 `strategy="text"`를 전달하세요. `markdown()`은 감지한 표를 읽기 순서에 맞게 배치하고 주변 단락에서 그 텍스트를 제거합니다.

## 구현하지 않은 기능의 대안 { #deliberate-scope }

| pymupdf 기능 | 대안 |
|---|---|
| Story API, `insert_htmlbox`(조판) | [typst](ecosystem.md#typesetting) |
| 디지털 서명 | [pyHanko](ecosystem.md#signatures) |
| 증분 저장 | 계획됨. 그때까지 pylopdf는 파일 전체를 다시 쓰며, 서명은 pyHanko가 처리 |
| 이미지나 CBZ 아카이브를 PDF로 변환 | 계획됨. 그전까지는 Pillow나 img2pdf로 변환하세요 |
| XPS나 EPUB 열기 | 계획 없음. pylopdf는 PDF 라이브러리 |

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
page = doc.pages[0]
page.annotations.add(pylopdf.Highlight(page.search("total")))  # 리스트 전체를 받음
page.pixmap(matrix=pylopdf.Matrix(2, 2)).save("page.png")
doc.save("out.pdf", dedupe=True, renumber=True)
```
