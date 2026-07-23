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
| `import fitz` / `import pymupdf` | `import pylopdf` | |
| `fitz.open(path)` / `open(stream=…)` | `pylopdf.open(path)` / `open(stream=…)` | 形式相同，也支持`password=` |
| `doc[i]`、`len(doc)`、迭代 | 相同 | 从0开始，支持负数索引 |
| `doc.metadata` / `set_metadata` | 相同 | 键名也相同 |
| `page.get_text()` | 相同 | 选项：`text` / `words` / `blocks` / `dict` |
| `page.search_for(t)` | 相同 | 返回`list[Rect]`；无`quads=` |
| `page.get_pixmap(matrix=fitz.Matrix(2, 2))` | `page.get_pixmap(scale=2)` | 也可用`dpi=144`；无Matrix类 |
| `pix.samples / width / height / stride` | 相同 | 始终为straight-alpha RGBA8；`tobytes()` → PNG |
| `page.get_images()` / 提取 | `page.get_images()` | 返回带bbox的已绘制图像；JPEG直通 |
| `doc.select`、`delete_page(s)`、`copy_page`、`new_page` | 相同 | `select`重复页码即复制页面 |
| `doc.insert_pdf(src, from_page=, to_page=, start_at=)` | 相同 | |
| `doc.get_toc()` / `set_toc()` | 相同 | 两者页码均从1开始 |
| `doc.save(garbage=4, deflate=True)` | `doc.save(garbage=True, deflate=True, object_streams=True)` | `garbage`为bool |
| `doc.save(encryption=…, user_pw=…)` | `doc.save(user_pw=…, owner_pw=…, permissions=…)` | 仅AES-256 |
| `doc.needs_pass` / `authenticate()` | 相同 | 返回值语义相同（0/1/2/4/6） |
| `page.rect / rotation / set_rotation` | 相同 | |
| `page.insert_image(rect, filename=)` | 相同 | 仅JPEG/PNG；无`pixmap=`，可先用Pillow转换 |
| `page.show_pdf_page(rect, src, pno)` | 相同 | 不支持叠加同一Document，需先复制 |
| `page.insert_text(point, text, fontsize=, fontname=)` | 相同 | Standard-14缩写（`helv`等）；仅WinAnsi |
| `page.add_highlight_annot(...)` | 相同 | 始终生成appearance stream |
| `doc.embfile_add / names / get / del` | 相同 | |
| `doc.get_page_labels / set_page_labels`、`page.get_label` | 相同 | |
| `page.widgets()` / widget对象 | `doc.get_form_fields()` / `doc.set_form_field(name, value)` | Document级；NeedAppearances |
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
- **表单填写**会设置值与`NeedAppearances`，外观由查看器绘制。pylopdf的渲染器不会
  重新生成widget appearance。
- **竖排文字**的阅读顺序尚未重建。

## 有意不实现的功能 — 使用生态系统 { #deliberate-scope }

| pymupdf功能 | pylopdf方案 |
|---|---|
| Story API / `insert_htmlbox`（排版） | 通过typst-py使用typst — [方案](ecosystem.md) |
| OCR（`get_textpage_ocr`，需安装Tesseract） | 任意OCR引擎 + `insert_ocr_text_layer` |
| 数字签名 | pyHanko（MIT）— [方案](ecosystem.md) |
| 增量保存 | 不计划支持（采用qpdf/pikepdf式重写思路）；签名场景由pyHanko处理 |
| 打开XPS / EPUB / CBZ / 图像 | 超出范围，只处理PDF |

## 迁移示例 { #worked-example }

```python
# pymupdf
import fitz
doc = fitz.open("in.pdf")
page = doc[0]
for rect in page.search_for("合计"):
    page.add_highlight_annot(rect)
pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
pix.save("page.png")
doc.save("out.pdf", garbage=4, deflate=True)
```

```python
# pylopdf
import pylopdf
doc = pylopdf.open("in.pdf")
page = doc[0]
page.add_highlight_annot(page.search_for("合计"))   # 可直接传入整个列表
with open("page.png", "wb") as f:
    f.write(page.get_pixmap(scale=2).tobytes())
doc.save("out.pdf", garbage=True, deflate=True)
```
