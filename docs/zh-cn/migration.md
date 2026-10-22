---
title: 从pymupdf迁移
description: 借助迁移工具、逐个名称的对照表以及需要注意的行为差异，将pymupdf代码移植到pylopdf。
---

# 从pymupdf迁移

pylopdf的目标是用自己的API完成pymupdf对PDF文件能做的一切。但它不能直接替代pymupdf：它**只能打开PDF文件**（不支持XPS、EPUB或图像），有意改变了少数行为，而且下一个版本将重新设计API，迁移工具会随着各部分的落地而跟进。文档及其页面、保存、文本读取、表格和OCR已经采用新API：`pylopdf.open(source)`、`doc.pages`、`doc.save()`、`page.text()`、`page.layout()`、`page.tables()`和`page.ocr()`。本页列出可以直接沿用的部分、存在差异的部分，以及pylopdf尚未实现的功能应改用什么。

## 自动迁移 { #codemod }

`python -m pylopdf.migrate`会就地改写pymupdf程序。通过`migrate` extra安装：

```bash
pip install "pylopdf[migrate]"
python -m pylopdf.migrate src/
```

它会替换`import fitz`和`import pymupdf`，改写可以机械替换的部分（例如打开文档、由`doc.pages`取代的页面操作、带页码换算的`get_toc`/`set_toc`、`embfile_*`、`xref_*`流读取方法、文本与表格读取、矢量绘图与图像位置，以及`Rect`/`Point`/`Matrix`的就地修改方法），并在每条仍需人工处理的语句上方添加`# TODO(pylopdf): …`注释，最后以`path:line: message`的形式列出这些位置。同一命令也能升级为pylopdf 0.15编写的程序。

- `--diff`只打印变更，不写入文件。
- `--check`在有文件会被修改时以状态1退出，适合在CI中使用。

该工具无法得知变量的类型，因此请审阅diff。只有当对某个变量的每次赋值都是打开文档（例如`doc = fitz.open(path)`）时，它才会把对该变量的索引、取长度和迭代改为经由`.pages`进行；对函数参数的`doc[0]`则保持不变。运行时使用pylopdf没有的pymupdf名称会抛出`AttributeError`，其消息会给出替代API，或说明该功能尚不支持；而`doc[0]`或`for page in doc`会抛出指向`doc.pages`的`TypeError`。

## 名称对照 { #mapping }

“相同”表示调用无需修改即可使用；说明列出不同之处。

### 文档与页面 { #mapping-documents }

| pymupdf | pylopdf | 说明 |
|---|---|---|
| `import pymupdf`（或`fitz`） | `import pylopdf` | |
| `pymupdf.open(path)`、`open(stream=…)`、`open("pdf", data)` | `pylopdf.open(path)`、`pylopdf.open(data)` | 仅限PDF；`password=`仅限关键字参数；文件对象请先读出内容 |
| `pymupdf.open()`、`pymupdf.Document()` | `pylopdf.Document()` | 空文档 |
| `doc[i]`、`doc.load_page(i)`、`len(doc)`、`doc.page_count`、迭代 | `doc.pages[i]`、`len(doc.pages)`、迭代`doc.pages` | `doc.pages`也支持切片，作用与pymupdf的`doc.pages(start, stop)`相同 |
| `page.number`、`page.parent` | `page.index`、`page.document` | |
| `doc.metadata`、`set_metadata()` | 相同 | |
| `doc.get_xml_metadata()`、`set_xml_metadata()`、`del_xml_metadata()` | 相同 | 读写默认以1 MiB为上限；`max_size=None`可取消上限 |
| `doc.language`、`set_language(tag)` | `doc.language_tag`、`set_language_tag(tag)` | 使用完整的标签：pymupdf最多只保留三个字母，因此`zh-TW`和`zh-HK`都会变成`zh-Hant`；迁移工具会改写`set_language` |
| `doc.pagemode`、`pagelayout`、`markinfo`及其`set_*`方法、`has_links()`、`has_annots()`、`is_closed` | 相同 | |
| `doc.name` | `doc.path` | 一个`pathlib.Path`；从bytes打开或新建的空文档为`None`。由于所有文档都是PDF，`is_pdf`已被移除 |
| `doc.needs_pass`、`doc.is_encrypted` | `doc.needs_password` | 在文档解锁之前为True |
| `doc.authenticate(password)` | `doc.unlock(password)` | 返回`Access.USER`或`Access.OWNER`；密码错误时抛出`PasswordError`，而不是返回0 |
| `doc.permissions`、`is_dirty` | 相同 | `permissions`是`Permissions`标志值：以`Permissions.ALL`代替`-4` |
| `doc.save(garbage=4, deflate=True)` | `doc.save(dedupe=True, renumber=True)` | 保存时默认压缩流并删除未使用的对象；`garbage=0`变为`prune=False`，`deflate=False`变为`compress=False`。页面、批注、表单字段和图层从不合并 |
| `doc.save(encryption=PDF_ENCRYPT_AES_256, user_pw=…, owner_pw=…, permissions=…)` | `doc.save(encryption=Encryption(user_password=…, owner_password=…, permissions=…))` | 仅支持AES-256 |
| `doc.tobytes(...)`、`doc.write(...)` | `doc.to_bytes(...)` | 选项与`save()`相同 |
| `doc.save(use_objstms=True)` | `doc.save(object_streams=True)` | |
| `doc.select(indexes)` | `doc.pages.select(indexes)` | 还会删除未保留页面的表单字段和标签PDF结构 |
| `doc.delete_page(i)`、`delete_pages(a, b)` | `del doc.pages[i]`、`del doc.pages[a:b + 1]` | 若要删除一组列出的页面，请用`doc.pages.select(...)`保留其余页面 |
| `doc.new_page(pno, width=, height=)` | `doc.pages.new((width, height), index=pno)` | 未指定`index`时追加到末尾 |
| `doc.copy_page(pno, to)`、`fullcopy_page(pno, to)` | `doc.pages.insert(to, doc.pages[pno])` | pylopdf从不让两个页面共享同一个页面对象 |
| `doc.move_page(pno, to)` | `doc.pages.move(pno, index)` | `index`是页面最终所在的位置；pymupdf则是插入到第`to`页之前 |
| `doc.insert_pdf(docsrc, from_page=a, to_page=b, start_at=i)` | `doc.pages.insert(i, docsrc.pages[a:b + 1])`、`doc.pages.extend(docsrc.pages)` | 名称冲突的导入表单字段会被重命名为`name+N`；没有`widgets=`和`join_duplicates=` |
| `doc.get_toc()`、`set_toc(toc)` | `doc.get_outline()`、`set_outline(entries)` | 页码从0开始，而不是从1开始 |
| `doc.get_page_labels()`、`set_page_labels()`、`page.get_label()` | 相同 | |
| `doc.get_page_numbers(label)` | `doc.pages.labeled(label)` | |
| `doc.embfile_add/names/get/del/upd/info/count` | `doc.attachments` | 一个映射：`add(name, data, description=…)`、`list(…)`、`[name].read()`、`del …[name]`、`[name].write(data)`、`len(…)`；`desc=`改为`description=` |
| `doc.xref_*`、`pdf_catalog()`、`pdf_trailer()` | `doc.objects`、`doc.catalog`、`doc.trailer` | 使用带类型的Python值而非PDF语法；参见[底层对象](api.md#low-level-objects) |

### 文本、渲染与图像 { #mapping-text }

| pymupdf | pylopdf | 说明 |
|---|---|---|
| `page.get_text()`、`get_text("text")` | `page.text()` | |
| `page.get_text("words")` | `page.words()` | 返回带有`text`、`bbox`、`block`、`line`和`index`的`Word`记录，而非元组 |
| `page.get_text("blocks")` | `page.layout().blocks` | 返回带有`text`、`bbox`和`lines`的`Block`记录，而非元组 |
| `page.get_text("dict")`、`get_text("rawdict")` | `page.layout().to_dict()`、`page.layout(chars=True).to_dict()` | 返回相同的字典；`page.layout()`本身返回记录。没有`html`和`xml` |
| `page.search_for(text)` | `page.search(text)` | 返回矩形；没有`quads=`；除非调大`max_hits=`，最多返回4,096个结果 |
| `page.find_tables()` | `page.tables()` | 返回`Table`记录列表：`table.rows`取代`extract()`，`len(table.rows)`取代`row_count`，`table.cells`逐行保存各单元格的框。尚无调优用的关键字参数、`header`和`to_pandas()` |
| `page.get_textpage_ocr()` | `page.ocr()`、`page.apply_ocr()` | 内置离线OCR；参见[离线OCR](ocr.md) |
| `pymupdf4llm.to_markdown(doc)` | `doc.markdown()` | 内置 |
| `page.get_pixmap(matrix=…, dpi=…)` | 相同 | 仅支持缩放矩阵；尚无`colorspace=`和`annots=`；`doc.get_page_pixmap(i)`改为`doc.pages[i].get_pixmap()` |
| `pix.samples`、`width`、`height`、`n`、`alpha`、`stride`、`colorspace`、`save()` | 相同 | `save()`和`tobytes()`只输出PNG |
| `page.get_svg_image()` | 相同 | 文本始终以路径绘制 |
| `page.get_images()` | `page.resources.images` | 以带有`xref`、`width`、`height`、`bits`、`colorspace`、`filters`和蒙版的`ImageObject`句柄代替元组 |
| `doc.extract_image(xref)` | `doc.images[xref].extract()` | 带有`format`、`data`和`resolution`的`EncodedImage`；尺寸、色彩空间和蒙版是`doc.images[xref]`的属性 |
| `pymupdf.Pixmap(doc, xref)` | `doc.images[xref].to_pixmap()` | `alpha=True`会应用图像的蒙版 |
| `page.get_image_info()` | `page.content(text_runs=False, paths=False).images` | 带有`xref`、`bbox`、`matrix`（pymupdf的`transform`）、尺寸、颜色空间、蒙版和分辨率的`Image`记录；尚不支持`hashes=` |
| `page.get_drawings()`、`get_cdrawings()` | `page.content(text_runs=False, images=False).paths` | 带有`segments`、`fill`、`fill_rule`、`stroke`和`bbox`的`Path`记录；`extended=True`作为单独条目返回的裁剪、分组和标记内容由每条路径的`context`持有 |
| `page.get_texttrace()`, `get_bboxlog()`, `get_displaylist()`, `run()`, `get_image_rects()` | `page.content()` | 以不可变的值返回带字形的文本段、路径和图像；参见[页面内容](api.md#content) |
| `doc.rewrite_images(dpi_threshold=, dpi_target=, quality=)` | 相同 | 只重新编码为JPEG，因此重新采样需要指定`quality`；返回统计信息而不是`None` |

### 绘制与批注 { #mapping-drawing }

| pymupdf | pylopdf | 说明 |
|---|---|---|
| `page.insert_text()`、`insert_textbox()` | 相同 | 还接受`font=`、`fontbuffer=`和`font_language=`；参见[文本与字体](api.md#fonts) |
| `pymupdf.Font(...)`、`font.text_length()`、`has_glyph()` | 相同 | Standard 14字体、OpenType文件和已安装的字体extra；pymupdf的其他内置字体以及`script=`、`ordering=`和`embed=`不可用 |
| `page.insert_image(rect, filename=/stream=/pixmap=)` | 相同 | JPEG、PNG或`Pixmap`；其他格式请用Pillow转换。同一图像插入两次只存储一份，因此不需要`xref=` |
| `page.show_pdf_page(rect, docsrc, pno)` | 相同 | 源文档可以是同一个文档 |
| `page.new_shape()`、`shape.draw_*`、`finish()`、`insert_text()`、`commit()` | 相同 | `with page.new_shape() as shape:`会自动提交；尚不支持`morph=`和`oc=` |
| `page.draw_line()`、`draw_rect()`及其他`draw_*` | 相同 | 样式选项仅限关键字参数 |
| `page.annots()`、`annot.type`、`rect`、`info`、`colors`、`flags` | 相同 | 目前为只读 |
| `page.add_highlight_annot()`、`page.insert_link()` | 相同 | `insert_link`只创建网页链接（`LINK_URI`） |
| `page.widgets()` | `doc.get_form_fields()`、`doc.set_form_field(name, value)` | 表单按文档处理 |

### 几何 { #mapping-geometry }

| pymupdf | pylopdf | 说明 |
|---|---|---|
| `Point`、`Matrix`、`Rect`、`IRect`、`Quad`及其运算符 | 相同 | 不可变 |
| `p.transform(m)`、`m.invert()` | `p = p * m`、`m = ~m` | |
| `r.include_point(p)`、`include_rect(o)`、`intersect(o)`、`normalize()`、`transform(m)` | `r = r | p`、`r | o`、`r & o`、`r.normalized()`、`r * m` | |
| `m.prerotate(deg)`、`prescale(sx, sy)`、`preshear(sx, sy)`、`pretranslate(tx, ty)` | `m = Matrix(deg) * m`、`Matrix(sx, sy) * m`、`Matrix(sx, sy, 1) * m`、`Matrix(1, 0, 0, 1, tx, ty) * m` | |

## 行为差异 { #behavioral-differences }

- **几何值不可修改。**`Point`、`Matrix`、`Rect`、`IRect`和`Quad`是不可变的具名元组。请赋值运算符的结果，而不是调用就地修改的方法。与pymupdf相同，`p + q`对坐标相加，`Rect(0, 0, 0, 0)`为假，这一点与普通元组不同。
- **过期的页面会抛出异常。**添加、删除或重排页面后，先前获取的`Page`会抛出`StalePageError`，而不是悄悄指向另一页。请用`doc.pages[i]`重新获取。
- **异常。**基类`PdfError`是`ValueError`的子类，因此`except ValueError`仍然有效。不是可读取PDF的输入（包括空输入）会抛出`MalformedPdfError`，而pymupdf在这些情况下抛出`FileDataError`或`EmptyFileError`。文件不存在时抛出Python内置的`FileNotFoundError`，因此请将`except pymupdf.FileNotFoundError`改为`except FileNotFoundError`。
- **旋转页面。**提取、搜索、绘制和渲染都使用页面显示时的坐标，旋转页面也是如此。
- **页面底部附近的`insert_text`。**pylopdf依据可见页面区域（`page.rect`）判断哪些行放得下，而pymupdf测量到未旋转页面的底边为止。在普通的未旋转页面上，两者结果一致。
- **字体处理严格。**pylopdf不会悄悄改用其他字体，而是抛出异常：例如字体缺少某个字符时，或`is_bold=True`请求的粗体字体未安装时。
- **合并表单。**`doc.pages.insert()`和`extend()`总是复制表单字段，与pymupdf默认的`widgets=True`相同。接收文档中已存在的同名字段会被重命名为`name+1`、`name+2`等，因此同一表单的各个副本保持各自的值。
- **表格与Markdown。**`tables()`默认检测以线条绘制的表格；无边框表格请传入`strategy="text"`。`markdown()`按阅读顺序放置检测到的表格，并从周围段落中移除表格文本。

## 未实现的功能及替代方案 { #deliberate-scope }

| pymupdf功能 | 替代方案 |
|---|---|
| Story API、`insert_htmlbox`（排版） | [typst](ecosystem.md#typesetting) |
| 数字签名 | [pyHanko](ecosystem.md#signatures) |
| 增量保存 | 已在计划中；在此之前pylopdf会重写整个文件，签名由pyHanko处理 |
| 将图像或CBZ归档转换为PDF | 已在计划中；在此之前请用Pillow或img2pdf转换 |
| 打开XPS或EPUB | 无此计划：pylopdf是PDF库 |

## 迁移示例 { #worked-example }

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
page.add_highlight_annot(page.search("total"))       # 可直接传入整个列表
page.get_pixmap(matrix=pylopdf.Matrix(2, 2)).save("page.png")
doc.save("out.pdf", dedupe=True, renumber=True)
```
