---
title: 快速开始
description: 安装pylopdf，并学习编辑、渲染、提取和绘制PDF的核心工作流。
---

# 快速开始

## 安装 { #installation }

```bash
pip install pylopdf
```

只需安装工作负载所需的语言或文字font：

```bash
pip install "pylopdf[jp]"     # 日语fallback与生成
pip install "pylopdf[ko]"     # 韩语
pip install "pylopdf[zh-cn]"  # 简体中文
pip install "pylopdf[zh-tw]"  # 繁体中文
pip install "pylopdf[ar]"     # 阿拉伯语生成（另有he、hi、th）
```

四个CJK地区extra同时支持未嵌入Adobe CJK font的渲染与subset嵌入生成。阿拉伯语、希伯来语、天城文和泰语的包提供生成font。请根据所需地区字形明确选择`jp`、`ko`、`zh-cn`或`zh-tw`；不提供通用CJK extra。

预构建wheel覆盖Linux（x86_64、ARM64）、macOS（Apple silicon、Intel）和Windows（x86_64）上的CPython 3.11–3.15，以及free-threaded 3.14t和3.15t。free-threaded用法请参阅[并发](concurrency.md)，Pyodide与Cloudflare Python Workers请参阅[WebAssembly](wasm.md)。

## 打开、检查、保存 { #open-inspect-save }

```python
import pylopdf

with pylopdf.open("input.pdf") as doc:     # 也可使用 pylopdf.open(stream=pdf_bytes)
    print(doc.page_count)                  # 也支持 len(doc)
    print(doc.metadata["title"])
    doc.set_metadata({"title": "Report", "author": "Alice"})

    doc.save("out.pdf")
    data = doc.tobytes()                   # 同一PDF的bytes形式
    doc.save("small.pdf", garbage=True, deflate=True, use_objstms=True)
    doc.save(
        "locked.pdf",
        encryption=pylopdf.PDF_ENCRYPT_AES_256,
        user_pw="secret",
        permissions=pylopdf.PDF_PERM_PRINT,
    )
```

- **加密文件**：使用`password=`打开，也可稍后调用`doc.authenticate()`。
- **仅读取元数据**：`pylopdf.peek_metadata(path)`无需解析整个文档即可读取元数据和页数，适合扫描大型文件集。`max_file_size=`可在解析前拒绝过大的输入。
- **损坏的文件**：pylopdf恢复损坏的cross-reference数据时，会发出`PylopdfWarning`并设置`doc.is_repaired`（`peek_metadata`中为`repaired`）。保存会写出干净的cross-reference数据。

!!! tip "不受信任的输入"

    打开不受自己控制的文件时，请传入`limits=pylopdf.DocumentLimits.web()`。它会限制文件大小、结构、解压和已解释文本，并以抛出`LimitError`代替耗尽内存。重型处理前可检查`doc.complexity`。各项预算请参阅[安全](security.md#untrusted-pdfs)。

## 页面、文本与搜索 { #pages-text-search }

```python
with pylopdf.open("input.pdf") as doc:
    for page in doc:                       # 也可用 doc[0]、doc[-1]；从0开始
        print(page.number, page.rect)

    page = doc[0]
    text = page.get_text()                 # 纯文本
    words = page.get_text("words")         # (x0, y0, x1, y1, word, block, line, word_no)
    layout = page.get_text("dict")         # blocks → lines → spans（含bbox、size、font）
    hits = page.search_for("total")        # 不区分大小写；返回 list[Rect]
```

所有坐标均为左上角原点的**显示空间**。即使页面旋转，搜索结果、版面信息、绘制和渲染也共享同一坐标系。

## 渲染 { #rendering }

```python
with pylopdf.open("input.pdf") as doc:
    doc[0].get_pixmap(dpi=144).save("page-1.png")
    png = doc[0].get_pixmap(dpi=300).tobytes()    # PNG bytes
    pix = doc[0].get_pixmap(dpi=144)               # 供NumPy或Pillow使用的RGB像素
    pngs = doc.render_pages(dpi=144, workers=4)    # 所有页面，并行渲染
    svg = doc[0].get_svg_image()
```

## 编辑 { #editing }

```python
with pylopdf.open("input.pdf") as doc:
    doc.select([2, 0, 1])                  # 保留并重排；重复索引即复制
    doc.delete_pages([1])                  # 每次编辑后页面会重新编号
    doc.new_page()                         # 在末尾添加空白A4页
    doc.fullcopy_page(0, to=1)             # 在索引1处插入第0页的独立副本
    doc[0].set_rotation(90)
    doc.set_outline([(1, "Chapter 1", 0), (2, "Section 1.1", 1)])
    doc.save("edited.pdf")

with pylopdf.Document() as merged, pylopdf.open("a.pdf") as a, pylopdf.open("b.pdf") as b:
    merged.insert_pdf(a)                   # a.pdf的全部页面
    merged.insert_pdf(b, from_page=0, to_page=2)  # b.pdf的第1–3页
    merged.save("merged.pdf")
```

## 绘制与批注 { #drawing-annotations }

图像、叠加和批注：

```python
with pylopdf.open("input.pdf") as doc, pylopdf.open("letterhead.pdf") as letterhead:
    page = doc[0]
    page.insert_image((72, 72, 200, 200), filename="logo.png")  # JPEG按原样直通
    page.show_pdf_page(page.rect, letterhead)                    # 将另一PDF的第0页作为矢量叠加
    page.draw_line((36, 340), (400, 340), width=0.5, dashes="[3 2] 0")

    hits = page.search_for("important")
    if hits:
        page.add_highlight_annot(hits)
    for rect in page.search_for("Example")[:1]:
        page.insert_link({"kind": pylopdf.LINK_URI, "from": rect, "uri": "https://example.com/"})
    doc.save("annotated.pdf")
```

文本与字体。未指定字体参数时，pylopdf对拉丁文字使用Helvetica，对其他文字使用已安装的地区extra（例如`pylopdf[jp]`）：

```python
with pylopdf.open("input.pdf") as doc:
    page = doc[0]
    page.insert_text((40, 40), "CONFIDENTIAL", fontsize=18, color=(1, 0, 0))
    page.insert_text((40, 70), "社外秘", fontsize=18)                        # 需要 pylopdf[jp]
    page.insert_text((40, 100), "简体中文", fontsize=18, font_language="zh-CN")  # 需要 pylopdf[zh-cn]

    bold = pylopdf.Font(fontfile="NotoSansJP-Bold.otf")    # 任意OpenType文件
    width = bold.text_length("見出し", fontsize=16)          # 与实际绘制宽度一致
    page.insert_text((page.rect.width - 40 - width, 140), "見出し", font=bold, fontsize=16)

    semibold = pylopdf.Font(fontfile="NotoSansCJKjp-VF.otf", weight=600)  # 可变字体可选择其字重轴上的任意字重
    page.insert_text((40, 170), "見出し", font=semibold, fontsize=16)
    doc.save("text.pdf")
```

将多个图形和文本作为一次绘制，每种字体只嵌入一次：

```python
with pylopdf.open("input.pdf") as doc:
    with doc[0].new_shape() as shape:
        shape.draw_rect((36, 220, 400, 320), radius=0.1)
        shape.finish(color=(0.2, 0.4, 0.6), fill=(0.95, 0.97, 1), width=1)
        shape.insert_text((48, 250), "Report", fontsize=16)
        shape.insert_textbox((48, 260, 390, 310), "Body paragraph.", fontsize=11)
    doc.save("shapes.pdf")
```

## 扫描PDF、表单与Markdown { #scans-forms-markdown }

```python
with pylopdf.open("scan.pdf") as doc:
    ocr_words = [(72, 90, 140, 104, "Invoice"), (146, 90, 190, 104, "#1024")]
    doc[0].insert_ocr_text_layer(ocr_words)   # 任意OCR输出：(x0, y0, x1, y1, text, ...)

with pylopdf.open("form.pdf") as doc:
    doc.set_form_field("customer", "Alice")   # 写入值及其外观
    doc.save("filled.pdf")

with pylopdf.open("input.pdf") as doc:
    md = doc.to_markdown()                    # 适合RAG的标题、列表和表格
```

如需在pylopdf内部识别文本，而不是自行提供OCR结果，请参阅[离线OCR](ocr.md)。

排版、PDF/A与数字签名请继续阅读[生态系统方案](ecosystem.md)；从pymupdf迁移的用户请查看[迁移指南](migration.md)。
