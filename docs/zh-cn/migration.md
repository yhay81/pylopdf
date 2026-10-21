---
title: 从pymupdf迁移
description: 将pymupdf工作流映射到pylopdf，并理解类型、行为与范围上的有意差异。
---

# 从pymupdf迁移

pylopdf的风格接近pymupdf，但并非直接替代品。影响迁移成本的数据形状——`"words"`元组、`"dict"`结构、`search_for → list[Rect]`——与pymupdf一致，因此多数提取与页面管理代码只需少量修改。本页列出可直接迁移的部分、行为差异，以及pylopdf有意不实现的功能应由什么替代。

!!! note
    pylopdf**只处理PDF文件**。pymupdf打开XPS、EPUB和图像的能力不在其范围内。

## 自动迁移 { #codemod }

`python -m pylopdf.migrate`会就地改写pymupdf程序。它需要`migrate` extra安装的LibCST：

```bash
pip install "pylopdf[migrate]"
python -m pylopdf.migrate src/
```

它会重命名`fitz`和`pymupdf`的import，改写可机械替换的部分——带页码换算的`get_toc`/`set_toc`、`embfile_*`、`xref_*`流读取、`copy_page`，以及就地修改的`Rect`/`Point`/`Matrix`方法——并在仍需人工处理的语句上方添加`# TODO(pylopdf): …`注释，最后以`path:line: message`形式列出这些步骤。`--diff`只打印变更而不写入，`--check`在有文件会被修改时以状态1退出。由于无法得知接收者的类型，请审阅diff。运行时查找pylopdf没有的pymupdf名称会引发`AttributeError`，其消息会给出替代API，或说明该功能尚不支持。

## 快速对照 { #mapping }

| pymupdf | pylopdf | 说明 |
|---|---|---|
| `import pymupdf`（`fitz`为旧名称） | `import pylopdf` | |
| `pymupdf.open(path)` / `open(stream=…)` / `open("pdf", data)` | 相同 | `open`就是`Document`；`filetype=`仅接受`"pdf"`；`password=`为仅限关键字参数，上限127 UTF-8 byte |
| `doc[i]`、`len(doc)`、迭代 | 相同 | 从0开始，支持负数索引 |
| `doc.metadata` / `set_metadata` | 相同 | 键名也相同 |
| `page.get_text()` | 相同 | 选项：`text` / `words` / `blocks` / `dict` / `rawdict` |
| `page.search_for(t)` | 相同，并有`max_hits=4096` | 返回有界`list[Rect]`；搜索词上限4,096 byte，`max_hits=None`取消，无`quads=` |
| `page.get_pixmap(matrix=pymupdf.Matrix(2, 2))` / `doc.get_page_pixmap(pno, ...)` | 相同 | 默认不透明RGB，`alpha=True`时为RGBA；`dpi=`优先；仅支持缩放matrix，尚无`colorspace=`与`annots=` |
| `page.get_svg_image()` | 相同 | `matrix`仅限恒等；`text_as_path=False`会抛出异常 |
| `Point(...)`, `Matrix(...)`, `p * m`, `m1 * m2`, `~m` | 相同 | 不可变NamedTuple；坐标必须为有限值 |
| `p.transform(m)` / `m.invert()` | `p = p * m` / `m = ~m` | 没有就地修改的方法；除以奇异矩阵会抛出异常 |
| `r.include_point(p)` / `r.include_rect(o)` / `r.intersect(o)` / `r.normalize()` / `r.transform(m)` | `r = r \| p` / `r = r \| o` / `r = r & o` / `r = r.normalized()` / `r = r * m` | 矩形与quad不可变；`q.transform(m)`改为`q = q * m` |
| `m.prerotate(deg)` / `m.prescale(sx, sy)` / `m.preshear(sx, sy)` / `m.pretranslate(tx, ty)` | `m = Matrix(deg) * m` / `m = Matrix(sx, sy) * m` / `m = Matrix(sx, sy, 1) * m` / `m = Matrix(1, 0, 0, 1, tx, ty) * m` | 需赋值结果；组合时先应用左侧矩阵 |
| `pix.samples / width / height / n / alpha / stride / colorspace / save()` | 相同 | RGB，`alpha=True`时为straight-alpha RGBA；pylopdf有上限的`tobytes(max_size=64 MiB)`与流式`save(filename)`生成PNG，`save`需要`.png` |
| `page.get_images(full=)` / `page.get_image_info(xrefs=)` / `doc.extract_image(xref)` | 相同 | `extract_image`以JPEG直通DCT，其他filter解码为PNG；`get_image_info(hashes=True)`在支持digest前会抛出异常 |
| `page.annots()` / `annot.type` / `annot.rect` / `annot.info` / `annot.colors` / `annot.flags` | 相同 | 目前为只读；`rect`使用显示坐标 |
| `page.add_highlight_annot(quads)` / `page.insert_link(link)` | 相同 | `Quad`按外接矩形处理；`insert_link`仅支持`LINK_URI` |
| `page.get_drawings()` | 相同 | 类型化path字典；line/cubic和常用paint/stroke属性；不支持`extended=`的clip/group层级 |
| `doc.rewrite_images(dpi_threshold=, dpi_target=, quality=)` | 相同 | 仅将无mask的安全DeviceGray/DeviceRGB DCT/Flate raster转换为JPEG，因此缩小需要`quality` 1–100；`set_to_gray`和`options`会引发异常；跳过bitonal图像；返回计数而非`None` |
| `doc.select`、`delete_page(s)`、`fullcopy_page`、`new_page` | 相同 | `delete_pages`接受pymupdf的形式（一个页码、两个页码、`from_page=`/`to_page=`、iterable）；`select`重复页码即复制页面；select／delete会移除被删除页面的表单widget、字段与标签结构内容 |
| `doc.copy_page(pno, to)` | `doc.fullcopy_page(pno, to)` | pylopdf不共享页面对象 |
| `doc.insert_pdf(docsrc, from_page=, to_page=, start_at=)` | 相同 | 采用pymupdf的范围规则（负的`from_page`表示第一页，负的`to_page`表示最后一页，两者都会限制在范围内）；注册导入页面的表单字段，冲突的顶层名称追加`+N`；没有`widgets=` / `join_duplicates=` |
| `doc.get_toc()` / `set_toc(toc)` | `doc.get_outline()` / `set_outline(entries)` | `OutlineEntry(level, title, page)`的`page`从0开始（pymupdf从1开始） |
| `doc.save(garbage=4, deflate=True)` | 相同 | 与pymupdf相同的0–4级；页面、注释、表单字段、结构元素和图层不会合并 |
| `doc.save(encryption=…, user_pw=…)` | 相同 | 仅支持`PDF_ENCRYPT_NONE`（默认）与`PDF_ENCRYPT_AES_256`，其他方式抛出异常；`PDF_ENCRYPT_NONE`下传入password会抛出异常而不是被忽略；每个password上限127 UTF-8 byte |
| `doc.needs_pass` / `authenticate()` | 相同 | 返回值语义相同（0/1/2/4/6） |
| `page.rect / rotation / set_rotation` | 相同 | |
| `page.insert_image(rect, filename= / stream= / pixmap=, rotate=)` | 相同 | JPEG直通、PNG透明、RGBA `Pixmap`直接复用及顺时针直角旋转；其他编码格式可用Pillow转换；相同输入共享同一图像XObject，无需`xref=`复用 |
| `page.show_pdf_page(rect, docsrc, pno)` | 相同 | 同一文档会使用原生编辑前快照，无需serialize/open复制；再次放置未编辑的源页面时共享同一Form XObject |
| `page.insert_text(point, text, fontsize=, lineheight=, fontname=, fontfile=)` | 相同，另有`fontbuffer=` / `fontindex=` / `font_language=` | 行的位置与计数与pymupdf相同；见[文本插入](#behavioral-differences) |
| `page.insert_textbox(rect, text, align=, lineheight=)` | 相同，并支持任意`fontfile=` / `fontbuffer=` / `font_language=` | 同样的provider选择与UAX #14换行；行的位置与是否放得下的判断与pymupdf相同；返回负值时不绘制 |
| `pymupdf.Font(fontname=, fontfile=, fontbuffer=)` / `font.text_length(text, fontsize)` / `font.has_glyph(chr)` | 相同，另有`language=`、`family=`、`weight=` | Standard 14名称、OpenType文件以及已安装的字体extra；见[字体](#behavioral-differences) |
| `page.add_highlight_annot(...)` | 相同 | 始终生成appearance stream |
| `doc.embfile_add / names / get / del / upd / info / count` | `doc.attachments.add(name, data, description=…)`、`list(doc.attachments)`、`doc.attachments[name].read()`、`del doc.attachments[name]`、`.write(data)`、handle属性、`len(doc.attachments)` | 不存在的名称引发`KeyError`；`desc=`改为`description=` |
| `doc.get_page_labels / set_page_labels`、`page.get_label` | 相同 | |
| `page.widgets()` / widget对象 | `doc.get_form_fields()` / `doc.set_form_field(name, value, fontfile=)` | Document级；文本／选择／复选框／单选按钮的原生外观 |
| `page.get_textpage_ocr(...)` | `page.get_text_ocr(...)` / `page.apply_ocr(...)` | 通过`pylopdf[ocr]`使用离线纯Rust PP-OCR；任意带坐标结果仍可传给`insert_ocr_text_layer` |
| `page.new_shape()` / `shape.draw_*` / `shape.finish()` / `shape.insert_text()` / `shape.commit()` | 相同 | 每次提交一个Form XObject，每种字体只嵌入一次；`with page.new_shape() as shape:`在正常结束时提交；未经`finish()`完成的路径执行`commit()`，以及不绘制任何内容的`finish()`都会抛出异常；`morph=`与`oc=`不可用 |
| `page.draw_line()` / `draw_rect()` / `draw_circle()` / `draw_polyline()`等`draw_*` | 相同 | 样式选项仅限关键字参数，返回终点`Point`；`morph=`与`oc=`不可用 |
| `pymupdf4llm.to_markdown(doc)` | `doc.to_markdown()` | 内置，MIT |

## 行为差异 { #behavioral-differences }

- **文本插入**：未指定字体来源时，`insert_text`使用WinAnsi编码的Standard 14字体，或已安装的script／locale字体provider；其他来源会shape并subset嵌入。分行、行的位置以及省略超出页面底边的行均与pymupdf相同，并返回绘制的行数。页面底边为显示坐标中`page.rect`的底边，pymupdf则在未旋转的坐标中测量。
- **字体**：`Font`接受Standard 14名称与别名、OpenType文件，以及通过`language=`或`family=`选择的已安装字体extra；pymupdf的其他内置字体（`"cjk"`、`"figo"`等）与`script=`、`ordering=`、`embed=`不可用。`is_bold=`、`is_italic=`与`is_serif=`精确选择已安装extra的字体，与pymupdf会忽略的`fontname`或字体文件一起使用时抛出异常；仅限关键字的`weight=`还可从可变字体文件的字重轴选择实例。将字体以`font=`传给`insert_text` / `insert_textbox`；`text_length`包含字距调整，`ascender`／`descender`返回与pymupdf相同的值，`insert_text`与`insert_textbox`用它们排列行。

- **坐标**：两者均使用左上角原点的显示空间。pylopdf在提取、搜索、绘制和渲染中，对旋转页面也始终保持同一坐标系。
- **类型**：`Point`、`Matrix`、`Rect`、`IRect`和`Quad`都是不可变的NamedTuple。`Point`和`Matrix`支持几何运算、矩阵乘法和除法，同时保留元组的索引、解包、相等比较和哈希。特别是，`Point(0, 0)`现在为假，`p + other`将坐标相加，`p * scalar`进行缩放而不是重复元组。没有就地修改的几何方法，请赋值运算符的结果。`Rect`、`IRect`和`Quad`同样遵循pymupdf：`r | p`包含一个点，`r & other`求交集，`r * m`进行变换，`p in r`测试包含关系，`width`和`height`从不为负。`r.normalize()`应写作`r = r.normalized()`。与元组不同，`r + (1, 2, 3, 4)`将坐标相加，`Rect(0, 0, 0, 0)`为假。`get_pixmap(matrix=)`接受缩放矩阵；旋转和剪切会抛出异常。
- **过期Page**：删除、插入或重排等结构变更后，先前获取的`Page`会抛出`StalePageError`，而不是悄悄指向其他页面。请使用`doc[i]`重新获取。
- **异常**：基类为`PdfError`（`ValueError`的子类）；`PasswordError`、`DocumentClosedError`、`EncryptedDocumentError`和`StalePageError`进一步细分。`except ValueError`仍然有效。pymupdf的`FileDataError`与`EmptyFileError`名称相同，并同时继承`RuntimeError`；文件不存在时抛出Python内置的`FileNotFoundError`，因此请将`except pymupdf.FileNotFoundError`改为`except FileNotFoundError`。
- **资源策略**：`DocumentLimits.web()`将传给rendering／extraction的完整PDF
  snapshot限制为64 MiB，并将累计带位置文本限制为65,536个glyph record。自定义策略可设置`max_interpretation_size`和`max_text_glyphs`；`None`保留兼容的无上限行为。
- **`get_text`选项**仅有`text` / `words` / `blocks` / `dict` / `rawdict`，没有`html` / `xml`。对嵌入字体，span字典包含`font`和兼容pymupdf的`flags`（bold/italic/serif/mono）。
- **多栏文本**通过确定性的栏间空白检测排序：先在每栏内从上到下，再按栏从左到右。小于1 em的窄栏间距只有在两侧都有充分正文且多行密集重复时才会采用；对齐标签、点引导线以及包含多个分隔位置的密集行仍保持按行顺序。确认后的分栏边界会跟随扫描件或OCR文本层中的小幅水平漂移。
- **`Page.find_tables()`**可从描边线或细长填充矩形重建轴对齐边框网格，并支持矩形合并单元格。指定`strategy="text"`可启用高置信度无边框表格检测；与对齐的多栏正文之间仍存在几何歧义。使用`clip=`可只保留完整位于已知显示坐标区域内的表格；排序无边框结果时可检查`Table.confidence` /
  `Table.diagnostics`。
- **`to_markdown()`**按阅读顺序插入完整边框表，并从周围正文中移除单元格文本。指定`table_strategy="text"`可加入保守的无边框候选；设为`None`可禁用表格转换。
- **表单填写**会写入值和原生外观，可在pylopdf及外部查看器中渲染。WinAnsi使用Helvetica自动缩小；Unicode需传入OpenType字体，或安装对应script／locale font
  extra。地区汉字可用`font_language=`指定。comb文本字段遵循继承的`MaxLen`与对齐方式。富文本、pushbutton和签名仍不在API范围内。
- **合并表单**时，`insert_pdf()`与pymupdf默认的`widgets=True`一样始终注册导入页面的字段。接收文档已使用的顶层名称会像qpdf那样变为未使用的最小`name+N`，因此同一表单合并后的各副本保持独立的值；没有把它们合并为一个字段的`join_duplicates=`。指定页面范围时，不导入其他页面的widget以及因此不再有widget的字段。
- **CJK竖排文字**采用保守检测：列内从上到下，列间从右到左。尚不解释注音、夹注及横竖混排等复杂排版。

## 有意不实现的功能 — 使用生态系统 { #deliberate-scope }

| pymupdf功能 | pylopdf方案 |
|---|---|
| Story API / `insert_htmlbox`（排版） | 通过typst-py使用typst — [方案](ecosystem.md) |
| 数字签名 | pyHanko（MIT）— [方案](ecosystem.md) |
| 增量保存 | 当前不支持；pylopdf会重写整个文件；增量保存已在计划中；签名由pyHanko处理 |
| 打开XPS / EPUB / CBZ / 图像 | 超出范围，只处理PDF |

## 迁移示例 { #worked-example }

```python
# pymupdf
import pymupdf
doc = pymupdf.open("in.pdf")
page = doc[0]
for rect in page.search_for("合计"):
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
page.add_highlight_annot(page.search_for("合计"))   # 可直接传入整个列表
page.get_pixmap(matrix=pylopdf.Matrix(2, 2)).save("page.png")
doc.save("out.pdf", garbage=4, deflate=True)
```
