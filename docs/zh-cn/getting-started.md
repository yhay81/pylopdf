---
title: 快速开始
description: 安装pylopdf，并学习编辑、渲染、提取和写入PDF的核心工作流。
---

# 快速开始

## 安装 { #installation }

```bash
pip install pylopdf
```

如需渲染未嵌入字体的中日韩PDF，请安装附带Noto CJK字体的额外依赖。
渲染时会自动检测字体：

```bash
pip install pylopdf[cjk]
```

## 打开、检查、保存 { #open-inspect-save }

```python
import pylopdf

doc = pylopdf.open("input.pdf")           # 也可使用 pylopdf.open(stream=pdf_bytes)
print(doc.page_count)                     # 也支持 len(doc)
print(doc.metadata["title"])
doc.set_metadata({"title": "报告", "author": "Alice"})

doc.save("out.pdf")
data = doc.tobytes()
doc.save("small.pdf", garbage=True, deflate=True, object_streams=True)
doc.save("locked.pdf", user_pw="secret", permissions=pylopdf.Permissions.PRINT)
```

使用`password=`打开加密PDF，也可稍后调用`doc.authenticate()`。
`pylopdf.peek_metadata(path)`无需解析整个文件即可读取元数据和页数，适合扫描大型文件集。
处理不受信任的文件时，请传入`max_decompressed_size=`以防解压炸弹。该限制会在打开时
逐个检查流，包括页面内容和图像解码后的大小；启用限制后，无法安全计算输出上限的
过滤器链会被拒绝。

## 页面、文本与搜索 { #pages-text-search }

```python
page = doc[0]                             # 从0开始；负数从末尾计数
for page in doc:
    print(page.number, page.rect)

text = page.get_text()                    # 纯文本
words = page.get_text("words")            # (x0, y0, x1, y1, word, block, line, word_no)
layout = page.get_text("dict")            # blocks → lines → spans（bbox、size、font、flags）
hits = page.search_for("合计")             # 不区分大小写，返回 list[Rect]
```

所有坐标均为左上角原点的**显示空间**。即使页面旋转，搜索结果、版面信息、绘制和渲染
也使用同一坐标系。

## 渲染 { #rendering }

```python
png = doc.render_page(0, dpi=300)                    # bytes（PNG）
pix = page.get_pixmap(scale=2)                       # 供NumPy/PIL使用的RGBA8像素
svg = doc.render_page_svg(0)
```

## 编辑 { #editing }

```python
doc.delete_pages([1, 2])
doc.select([2, 0])                                   # 保留/重排（重复即复制）
doc.new_page(); doc.copy_page(0, to=1)

merged = pylopdf.Document()
merged.insert_pdf(pylopdf.open("a.pdf"))
merged.insert_pdf(pylopdf.open("b.pdf"), from_page=0, to_page=2, start_at=0)

doc.set_toc([[1, "第1章", 1], [2, "1.1节", 2]])
page.set_rotation(90)
```

## 绘制与批注 { #drawing-annotations }

```python
page.insert_image((72, 72, 200, 200), filename="logo.png")   # JPEG直通/PNG透明
page.insert_image(page.search_for("已批准")[0], stream=stamp_png)
page.show_pdf_page(page.rect, letterhead)                    # 以矢量叠加其他PDF
page.insert_text((40, 40), "CONFIDENTIAL", fontsize=18, color=(1, 0, 0))
page.add_highlight_annot(page.search_for("重要"))            # 搜索并高亮
page.add_link_annot(page.search_for("Example")[0], "https://example.com/")
```

## 扫描PDF、表单与Markdown { #scans-forms-markdown }

```python
page.insert_ocr_text_layer(ocr_words)     # 将任意OCR结果写入可搜索PDF
doc.set_form_field("customer", "Alice")   # 填写AcroForm（NeedAppearances）
md = doc.to_markdown()                    # 适合RAG的Markdown
```

排版、PDF/A与数字签名请继续阅读[生态系统方案](ecosystem.md)；
从pymupdf迁移的用户请查看[迁移指南](migration.md)。
