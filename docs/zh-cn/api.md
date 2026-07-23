---
title: API概览
description: pylopdf的Document、Page、Pixmap、Rect、权限、警告与异常的紧凑索引。
---

# API概览

完整docstring包含在包内，可运行`help(pylopdf.Document)`查看。本页提供API地图。
除`get_toc` / `set_toc`为兼容pymupdf而从1开始外，所有页码均从0开始。
所有坐标均为左上角原点的显示空间。

## Document { #document }

`pylopdf.Document(filename=None, stream=None, password=None, max_decompressed_size=None)` —
`pylopdf.open()`是别名构造函数，并支持上下文管理器。

| 成员 | 用途 |
|---|---|
| `doc[i]` / `load_page(pno)` / 迭代 | `Page`视图（支持负数；结构变更后需重新获取） |
| `page_count` / `len(doc)` | 页数 |
| `needs_pass` / `is_encrypted` / `authenticate(pw)` | 加密状态与解锁（兼容pymupdf语义） |
| `metadata` / `set_metadata(dict)` | Info字典（支持UTF-16BE） |
| `get_page_text(pno, option)` | `"text"` / `"words"` / `"blocks"` / `"dict"` |
| `to_markdown(pages=None)` | Markdown转换（标题、CJK连接、强调、列表） |
| `render_page(pno, scale=, dpi=, background=)` / `render_page_svg(pno)` | PNG字节 / SVG字符串 |
| `set_fallback_font(font, kind=, index=)` | 未嵌入字体时的CJK后备字体 |
| `select` / `delete_page(s)` / `insert_pdf` / `new_page` / `copy_page` | 页面管理 |
| `get_toc()` / `set_toc(toc)` | 书签（页码从1开始） |
| `get_page_labels()` / `set_page_labels(labels)` | 页码标签范围 |
| `get_form_fields()` / `set_form_field(name, value)` | AcroForm列出与填写（NeedAppearances） |
| `embfile_add / embfile_names / embfile_get / embfile_del` | 文件附件 |
| `get_pdfa_claim()` | 读取XMP中的PDF/A声明（不是验证） |
| `save(...)` / `tobytes(...)` | `garbage=` `deflate=` `object_streams=` `user_pw=` `owner_pw=` `permissions=` |
| `close()` | 也可通过`with`调用 |

## Page { #page }

| 成员 | 用途 |
|---|---|
| `number` / `parent` / `get_label()` | 标识与显示标签 |
| `get_text(option)` / `search_for(needle)` | 提取与不区分大小写的搜索 |
| `to_markdown()` | 单页Markdown |
| `get_images()` | 已绘制图像（含`bbox`，JPEG直通 / PNG） |
| `get_pixmap(scale=, dpi=, background=)` / `render(...)` / `render_svg()` | 渲染 |
| `rotation` / `set_rotation(deg)` | 显示旋转 |
| `mediabox` / `cropbox` / `rect` / `set_mediabox` / `set_cropbox` | 页面框 |
| `insert_image(rect, filename= / stream=, keep_proportion=, overlay=)` | 绘制JPEG/PNG |
| `show_pdf_page(rect, src, pno=, keep_proportion=, overlay=)` | 以矢量叠加其他PDF页面 |
| `insert_text(point, text, fontsize=, fontname=, color=)` | Standard-14文本（WinAnsi） |
| `insert_ocr_text_layer(words)` | OCR不可见文本层（可搜索PDF） |
| `replace_text(search, replacement, default_char=)` | 替换简单编码的文本 |
| `annots()` / `add_highlight_annot(...)` / `add_link_annot(rect, uri)` | 批注 |

## 模块级 { #module-level }

| 名称 | 用途 |
|---|---|
| `peek_metadata(path_or_stream, password=)` | 无需完整解析即可快速读取元数据与页数 |
| `Permissions` | 加密权限标志（IntFlag） |
| `Rect` | 带`width` / `height`的矩形NamedTuple |
| `PdfError` / `PasswordError` / `DocumentClosedError` / `EncryptedDocumentError` / `StalePageError` | 异常层级（基类兼容ValueError） |
| `Pixmap` | RGBA8像素：`samples` / `width` / `height` / `stride` / `n` / `tobytes()` |
| `PylopdfWarning` | 解释器警告（字体解析、图像解码） |
