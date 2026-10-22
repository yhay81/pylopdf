---
title: 快速开始
description: 安装pylopdf，并了解编辑、渲染、提取和绘制PDF的核心用法。
---

# 快速开始

!!! note "项目状态"

    pylopdf尚处于1.0之前的阶段，下一个版本将按照[路线图](https://github.com/yhay81/pylopdf/blob/main/ROADMAP.md)重新设计API；哪些内容可能变化，请参阅[稳定性政策](stability.md)。本站跟随`main`分支更新，因此可能介绍尚未发布到PyPI的变更。各版本包含的内容请查看[更新日志](https://github.com/yhay81/pylopdf/blob/main/CHANGELOG.md)。

## 安装 { #installation }

```bash
pip install pylopdf
```

pylopdf需要Python 3.11或更高版本，且没有必需的Python依赖。预构建wheel覆盖Linux（x86_64、ARM64）、macOS（Apple silicon、Intel）和Windows（x86_64），包括自由线程的Python 3.14t和3.15t。WebAssembly版本可在Pyodide和Cloudflare Python Workers中运行，详见[WebAssembly](wasm.md)。

可选的extra提供字体和OCR模型，只需安装用得到的那些：

| Extra | 提供的内容 |
|---|---|
| `jp`、`ko`、`zh-cn`、`zh-tw` | 日语、韩语、简体中文和繁体中文字体，用于显示未嵌入字体的PDF以及写入文本 |
| `ar`、`he`、`hi`、`th` | 阿拉伯语、希伯来语、印地语（天城文）和泰语字体，用于写入文本 |
| `ocr` | 内置[离线OCR](ocr.md)引擎所用的模型 |
| `migrate` | [pymupdf迁移工具](migration.md#codemod) |

```bash
pip install "pylopdf[jp,ocr]"
```

中文、日语和韩语的extra彼此独立，因为各地区对部分字符的写法不同，请选择与文档相符的那一个。

## 打开、查看与保存 { #open-inspect-save }

```python
import pylopdf

with pylopdf.open("input.pdf") as doc:     # 也可使用 pylopdf.open(pdf_bytes)
    print(len(doc.pages))
    print(doc.metadata["title"])
    doc.set_metadata({"title": "Report", "author": "Alice"})

    doc.save("out.pdf")                    # 压缩，且不含未使用的对象
    data = doc.to_bytes()                  # 以bytes形式得到同一份PDF
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

- **加密文件**：打开时传入`password=`，或在打开后调用`doc.unlock(password)`；`doc.needs_password`表明是否需要这样做。
- **只读元数据**：`pylopdf.probe(path)`无需加载整个文档即可读取页数、版本和元数据，适合扫描大量文件。
- **损坏的文件**：如果pylopdf需要修复损坏的文件才能打开，它会发出`PylopdfWarning`并设置`doc.is_repaired`。保存时会写出一份完好的文件。

!!! tip "来源不可信的文件"

    打开不受你控制的文件时，请传入`limits=pylopdf.DocumentLimits.web()`。这样遇到过大或恶意的输入时，pylopdf会抛出`LimitError`，而不会耗尽内存。详见[安全](security.md#untrusted-pdfs)。

## 页面、文本与搜索 { #pages-text-search }

```python
with pylopdf.open("input.pdf") as doc:
    for page in doc.pages:                 # 也可使用 doc.pages[0]、doc.pages[-1]；索引从0开始
        print(page.index, page.rect)

    page = doc.pages[0]
    text = page.get_text()                 # 纯文本
    words = page.get_text("words")         # (x0, y0, x1, y1, word, block, line, word_no)
    layout = page.get_text("dict")         # blocks → lines → spans，含bbox、size、font
    hits = page.search_for("total")        # 不区分大小写；返回Rect列表
    tables = page.find_tables()
```

坐标以点（1/72英寸）为单位，原点位于页面显示时的左上角。搜索结果、提取的文本、绘制和渲染都使用同一套坐标，旋转页面也不例外。

## 渲染 { #rendering }

```python
with pylopdf.open("input.pdf") as doc:
    doc.pages[0].get_pixmap(dpi=144).save("page-1.png")
    png = doc.pages[0].get_pixmap(dpi=300).tobytes()  # PNG bytes
    pix = doc.pages[0].get_pixmap(dpi=144)            # 供NumPy或Pillow使用的RGB像素
    pngs = doc.render_pages(dpi=144, workers=4)       # 并行渲染所有页面
    svg = doc.pages[0].get_svg_image()
```

## 编辑页面 { #editing }

```python
with pylopdf.open("input.pdf") as doc:
    doc.pages.select([2, 0, 1])            # 保留并重排；重复某个索引即复制该页
    del doc.pages[1]
    doc.pages.new()                        # 在末尾添加空白A4页面
    doc.pages.insert(1, doc.pages[0])      # 在索引1处插入第0页的副本
    doc.pages.move(0, -1)                  # 将第一页移到末尾
    doc.pages[0].set_rotation(90)
    doc.set_outline([(1, "Chapter 1", 0), (2, "Section 1.1", 1)])
    doc.save("edited.pdf")

with pylopdf.Document() as merged, pylopdf.open("a.pdf") as a, pylopdf.open("b.pdf") as b:
    merged.pages.extend(a.pages)           # a.pdf的全部页面
    merged.pages.extend(b.pages[:3])       # b.pdf的前三页
    merged.save("merged.pdf")
```

添加、删除或重排页面后，页面索引会随之变化。在此之前获取的`Page`对象会抛出`StalePageError`，请用`doc.pages[i]`重新获取。

## 绘制与批注 { #drawing-annotations }

图像、叠加、线条和批注：

```python
with pylopdf.open("input.pdf") as doc, pylopdf.open("letterhead.pdf") as letterhead:
    page = doc.pages[0]
    page.insert_image((72, 72, 200, 200), filename="logo.png")
    page.show_pdf_page(page.rect, letterhead)       # 另一个PDF的第0页，保持矢量
    page.draw_line((36, 340), (400, 340), width=0.5, dashes="[3 2] 0")

    hits = page.search_for("important")
    if hits:
        page.add_highlight_annot(hits)
    for rect in page.search_for("Example")[:1]:
        page.insert_link({"kind": pylopdf.LINK_URI, "from": rect, "uri": "https://example.com/"})
    doc.save("annotated.pdf")
```

文本与字体。不指定字体参数时，pylopdf用Helvetica写入拉丁文字，其他文字则使用已安装extra（如`pylopdf[jp]`）中的字体：

```python
with pylopdf.open("input.pdf") as doc:
    page = doc.pages[0]
    page.insert_text((40, 40), "CONFIDENTIAL", fontsize=18, color=(1, 0, 0))
    page.insert_text((40, 70), "社外秘", fontsize=18)                          # 需要 pylopdf[jp]
    page.insert_text((40, 100), "简体中文", fontsize=18, font_language="zh-CN")  # 需要 pylopdf[zh-cn]

    bold = pylopdf.Font(fontfile="NotoSansJP-Bold.otf")       # 任意OpenType文件
    width = bold.text_length("見出し", fontsize=16)             # 实际绘制的宽度
    page.insert_text((page.rect.width - 40 - width, 140), "見出し", font=bold, fontsize=16)
    doc.save("text.pdf")
```

要按顺序一起绘制图形、文本和图像，请使用画布，这样每种字体和每幅图像只存储一次：

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

要写出新文档（例如带标签的PDF/UA文件），请使用[`pylopdf.create()`](api.md#create)：其页面使用同一个画布绘制。

## 扫描件、表单与Markdown { #scans-forms-markdown }

```python
with pylopdf.open("scan.pdf") as doc:
    doc.pages[0].apply_ocr()                        # 需要 pylopdf[ocr]；添加不可见、可搜索的文本
    doc.save("searchable.pdf")

with pylopdf.open("form.pdf") as doc:
    print(doc.get_form_fields())
    doc.set_form_field("customer", "Alice")   # 写入值及其显示外观
    doc.save("filled.pdf")

with pylopdf.open("input.pdf") as doc:
    md = doc.to_markdown()                    # 标题、列表和表格
```

OCR的详细说明（包括如何添加其他OCR引擎识别的文本）请参阅[离线OCR](ocr.md)。

## 尚不支持的功能 { #not-supported }

涂黑（redaction）、编辑或删除批注、创建表单字段、增量保存以及经过验证的PDF/A和PDF/UA输出都在计划中，详见[路线图](https://github.com/yhay81/pylopdf/blob/main/ROADMAP.md)。pylopdf只打开PDF文件。

基于HTML和CSS的排版以及数字签名交由生态系统中的工具完成。[生态系统方案](ecosystem.md)介绍了如何将pylopdf与typst和pyHanko配合使用。

## 下一步 { #next-steps }

- [API参考](api.md)：所有类和方法
- [从pymupdf迁移](migration.md)：移植现有代码时阅读
- [贡献与支持](contributing.md)：报告bug或提出功能需求
