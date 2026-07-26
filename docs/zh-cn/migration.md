---
title: 从pymupdf迁移
description: 将pymupdf工作流映射到pylopdf，并理解类型、行为与范围上的有意差异。
---

# 从pymupdf迁移

pylopdf的风格接近pymupdf，但并非直接替代品。影响迁移成本的数据形状——`"words"`
元组、`"dict"`结构、`search_for → list[Rect]`、从1开始的TOC页码——与pymupdf一致，
因此多数提取与页面管理代码只需少量修改。本页列出可直接迁移的部分、行为差异，
以及pylopdf有意不实现的功能应由什么替代。

!!! note
    pylopdf**只处理PDF文件**。pymupdf打开XPS、EPUB和图像的能力不在其范围内。

## 快速对照 { #mapping }

| pymupdf | pylopdf | 说明 |
|---|---|---|
| `import pymupdf`（`fitz`为旧名称） | `import pylopdf` | |
| `pymupdf.open(path)` / `open(stream=…)` | `pylopdf.open(path)` / `open(stream=…)` | 形式相同，也支持`password=` |
| `doc[i]`、`len(doc)`、迭代 | 相同 | 从0开始，支持负数索引 |
| `doc.metadata` / `set_metadata` | 相同 | 键名也相同 |
| `page.get_text()` | 相同 | 选项：`text` / `words` / `blocks` / `dict` |
| `page.search_for(t)` | 相同，并有`max_hits=4096` | 返回有界`list[Rect]`；搜索词上限4,096 byte，`max_hits=None`取消，无`quads=` |
| `page.get_pixmap(matrix=pymupdf.Matrix(2, 2))` | `page.get_pixmap(scale=2)` | 也可用`dpi=144`；无Matrix类 |
| `pix.samples / width / height / stride / save()` | 相同 | 始终为straight-alpha RGBA8；pylopdf有上限的`tobytes(max_size=64 MiB)`与流式`save(path)`生成PNG，且`save`要求`.png`扩展名 |
| `page.get_images()` / 提取 | `page.get_images()` | 返回带bbox的已绘制图像；JPEG直通 |
| `page.get_drawings()` | 相同 | 类型化path字典；line/cubic和常用paint/stroke属性；不支持`extended=`的clip/group层级 |
| `doc.rewrite_images(dpi_target=, quality=)` | `doc.compress_images(dpi=, quality=)` | 将无mask的安全DeviceGray/DeviceRGB DCT/Flate raster转换为JPEG；`dpi`直接限制最大放置尺寸，不支持lossless转换 |
| `doc.select`、`delete_page(s)`、`copy_page`、`new_page` | 相同 | `select`重复页码即复制页面 |
| `doc.insert_pdf(src, from_page=, to_page=, start_at=)` | 相同 | |
| `doc.get_toc()` / `set_toc()` | 相同 | 两者页码均从1开始 |
| `doc.save(garbage=4, deflate=True)` | `doc.save(garbage=True, deflate=True, object_streams=True)` | `garbage`为bool |
| `doc.save(encryption=…, user_pw=…)` | `doc.save(user_pw=…, owner_pw=…, permissions=…)` | 仅AES-256 |
| `doc.needs_pass` / `authenticate()` | 相同 | 返回值语义相同（0/1/2/4/6） |
| `page.rect / rotation / set_rotation` | 相同 | |
| `page.insert_image(rect, filename= / stream= / pixmap=, rotate=)` | 相同 | JPEG直通、PNG透明、RGBA `Pixmap`直接复用及顺时针直角旋转；其他编码格式可用Pillow转换 |
| `page.show_pdf_page(rect, src, pno)` | 相同 | 同一文档会使用原生编辑前快照，无需serialize/open复制 |
| `page.insert_text(point, text, fontsize=, fontname=, fontfile=)` | 相同，另有`fontbuffer=` / `fontindex=` | 无source时为Standard-14 / WinAnsi，或由`pylopdf[cjk]`为日文／汉字自动选JP subset；中文本地字形等应显式传font |
| `page.insert_textbox(rect, text, align=, lineheight=)` | 相同，并支持任意`fontfile=` / `fontbuffer=` | 同样的可选JP font选择与UAX #14 CJK换行；返回负值时不绘制 |
| `page.add_highlight_annot(...)` | 相同 | 始终生成appearance stream |
| `doc.embfile_add / names / get / del` | 相同 | |
| `doc.get_page_labels / set_page_labels`、`page.get_label` | 相同 | |
| `page.widgets()` / widget对象 | `doc.get_form_fields()` / `doc.set_form_field(name, value, fontfile=)` | Document级；文本／选择／复选框／单选按钮的原生外观 |
| `page.get_textpage_ocr(...)` | `page.get_text_ocr(...)` / `page.apply_ocr(...)` | 通过`pylopdf[ocr]`使用离线纯Rust PP-OCR；任意带坐标结果仍可传给`insert_ocr_text_layer` |
| `pymupdf4llm.to_markdown(doc)` | `doc.to_markdown()` | 内置，MIT |

## 行为差异 { #behavioral-differences }

- **坐标**：两者均使用左上角原点的显示空间。pylopdf在提取、搜索、绘制和渲染中，
  对旋转页面也始终保持同一坐标系。
- **类型**：`Rect`是不可变`NamedTuple`（`x0, y0, x1, y1`以及`width` / `height`）。
  没有`Point` / `Matrix` / `Quad`类；API使用普通元组和`scale=` / `dpi=`关键字。
- **过期Page**：删除、插入或重排等结构变更后，先前获取的`Page`会抛出
  `StalePageError`，而不是悄悄指向其他页面。请使用`doc[i]`重新获取。
- **异常**：基类为`PdfError`（`ValueError`的子类）；`PasswordError`、
  `DocumentClosedError`、`EncryptedDocumentError`和`StalePageError`进一步细分。
  `except ValueError`仍然有效。
- **`get_text`选项**仅有`text` / `words` / `blocks` / `dict`，没有`html` /
  `rawdict` / `xml`。对嵌入字体，span字典包含`font`和兼容pymupdf的`flags`
  （bold/italic/serif/mono）。
- **多栏文本**通过确定性的栏间空白检测排序：先在每栏内从上到下，再按栏从左到右。
- **`Page.find_tables()`**可从描边线或细长填充矩形重建轴对齐边框网格，
  并支持矩形合并单元格。指定`strategy="text"`可启用高置信度无边框表格检测；
  与对齐的多栏正文之间仍存在几何歧义。使用`clip=`可只保留完整位于已知显示坐标
  区域内的表格；排序无边框结果时可检查`Table.confidence` /
  `Table.diagnostics`。
- **`to_markdown()`**按阅读顺序插入完整边框表，并从周围正文中移除单元格文本。
  指定`table_strategy="text"`可加入保守的无边框候选；设为`None`可禁用表格转换。
- **表单填写**会写入值和原生外观，可在pylopdf及外部查看器中渲染。WinAnsi使用
  Helvetica自动缩小；Unicode需传入OpenType字体，或安装`pylopdf[cjk]`尝试JP
  subset。中文本地字形与Hangul应传入匹配font。comb文本字段遵循继承的`MaxLen`与
  对齐方式。富文本、pushbutton和签名仍
  不在API范围内。
- **CJK竖排文字**采用保守检测：列内从上到下，列间从右到左。
  尚不解释注音、夹注及横竖混排等复杂排版。

## 有意不实现的功能 — 使用生态系统 { #deliberate-scope }

| pymupdf功能 | pylopdf方案 |
|---|---|
| Story API / `insert_htmlbox`（排版） | 通过typst-py使用typst — [方案](ecosystem.md) |
| 数字签名 | pyHanko（MIT）— [方案](ecosystem.md) |
| 增量保存 | 当前不支持；pylopdf会重写整个文件，并将其保留在观察列表中；签名由pyHanko处理 |
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
page.get_pixmap(scale=2).save("page.png")
doc.save("out.pdf", garbage=True, deflate=True)
```
