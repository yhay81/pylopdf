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
| `to_markdown(pages=None, table_strategy="lines")` | Markdown转换（标题、CJK连接、强调、列表、多栏及保守的竖排顺序；默认插入边框表，`"text"`增加无边框表，`None`禁用表格转换） |
| `render_page(...)` / `render_pages(..., workers=)` / `render_page_svg(...)` | PNG、保序并行PNG批次或SVG |
| `set_fallback_font(font, kind=, index=)` | 未嵌入字体时的CJK后备字体 |
| `select` / `delete_page(s)` / `insert_pdf` / `new_page` / `copy_page` | 页面管理 |
| `get_toc()` / `set_toc(toc)` | 书签（页码从1开始） |
| `get_page_labels()` / `set_page_labels(labels)` | 页码标签范围 |
| `get_form_fields()` / `set_form_field(name, value, fontfile=, fontbuffer=, fontindex=)` | 列出与填写AcroForm，并生成原生widget外观 |
| `embfile_add / embfile_names / embfile_get / embfile_del` | 文件附件 |
| `get_pdfa_claim()` | 读取XMP中的PDF/A声明（不是验证） |
| `save(...)` / `tobytes(...)` | `garbage=` `deflate=` `object_streams=` `user_pw=` `owner_pw=` `permissions=` |
| `close()` | 也可通过`with`调用 |

## Page { #page }

| 成员 | 用途 |
|---|---|
| `number` / `parent` / `get_label()` | 标识与显示标签 |
| `get_text(option)` / `search_for(needle)` | 提取与不区分大小写的搜索 |
| `get_text_ocr(dpi=, engine=, tile_size=, overlap=, min_confidence=, rotation=, clip=)` | 不编辑页面，通过本地PP-OCRv6返回带位置的单词；`rotation`顺时针校正输入，`clip`使用显示坐标 |
| `apply_ocr(..., rotation=, clip=, skip_existing=True)` | 插入保留方向的不可见可搜索层；默认跳过所选区域的已有文本 |
| `find_tables(strategy="lines", clip=None)` | 完整或保守补全的稀疏矢量边框与合并单元格；`"text"`启用无边框检测，`clip`指定显示坐标区域 |
| `to_markdown(table_strategy="lines")` | 使用相同表格控制的单页Markdown |
| `get_images()` | 已绘制图像（含`bbox`，JPEG直通 / PNG） |
| `get_pixmap(scale=, dpi=, background=, clip=)` / `render(...)` / `render_svg()` | 渲染；`clip`使用显示坐标 |
| `rotation` / `set_rotation(deg)` | 显示旋转 |
| `mediabox` / `cropbox` / `rect` / `set_mediabox` / `set_cropbox` | 页面框 |
| `insert_image(rect, filename= / stream= / pixmap=, keep_proportion=, overlay=)` | 绘制JPEG/PNG，或直接复用已渲染的RGBA `Pixmap` |
| `show_pdf_page(rect, src, pno=, keep_proportion=, overlay=)` | 以矢量叠加其他PDF页面 |
| `insert_text(point, text, fontsize=, fontname=, fontfile=, fontbuffer=, fontindex=, color=, overlay=)` | Standard-14 WinAnsi文本，或子集嵌入OpenType Unicode文本 |
| `insert_textbox(rect, text, fontsize=, fontname=, fontfile=, fontbuffer=, fontindex=, color=, align=, expandtabs=, lineheight=, overlay=)` | 按Core 14或嵌入OpenType的实际字宽进行UAX #14换行；返回剩余高度，溢出时不绘制 |
| `insert_ocr_text_layer(words, rotation=)` | 保留方向的OCR不可见文本层（可搜索PDF） |
| `replace_text(search, replacement, default_char=)` | 替换简单编码的文本 |
| `annots()` / `add_highlight_annot(...)` / `add_link_annot(rect, uri)` | 批注 |

使用嵌入字体的`insert_text`需要一个包含所有所需字形的字体。它会对每一行进行塑形，
但不提供字体回退、双向段落布局或自动换行。RTL塑形可以正确渲染；当前文本提取采用
视觉顺序而非逻辑顺序。

`insert_textbox`并非富文本引擎；它保留显式换行、展开制表符、按Unicode机会换行CJK，
并对过长单词执行grapheme安全的紧急换行。对齐常量为`TEXT_ALIGN_LEFT`、
`TEXT_ALIGN_CENTER`、`TEXT_ALIGN_RIGHT`和`TEXT_ALIGN_JUSTIFY`。返回负值表示
垂直空间不足，此时不会添加页面内容或字体resource。

`set_form_field`会为文本、组合框／列表选择、复选框和单选按钮生成外观。WinAnsi文本
使用Helvetica自动缩小；传入OpenType `fontfile`或`fontbuffer`即可对子集嵌入Unicode。
安装`pylopdf[cjk]`后，非WinAnsi值会自动使用其中的sans字体。已有且非空的按钮外观
会保留，仅为缺失状态生成矢量标记。其他WinAnsi字段缺失的外观也会同时补齐；仅当
所有可填写widget都自包含时才清除`NeedAppearances`。comb文本字段遵循继承的
`MaxLen`与对齐方式，将每个Unicode grapheme置于相应位置中央，并在不修改文档的
情况下拒绝超长值。富文本、pushbutton动作和签名不在生成范围内。

`Table.confidence`是0–1的确定性排序heuristic，并非经过校准的概率。
`Table.diagnostics`是`TableDiagnostics` tuple；对无边框文本表格，它包含以em归一化的
对齐误差、最小列间距和行间距变化。完整矢量网格得分为1.0，补全稀疏边框的
hybrid grid为0.95；两者的文本专用指标均为`None`。
`TableFinder.strategy`和`TableFinder.clip`保留本次使用的设置。

## 模块级 { #module-level }

| 名称 | 用途 |
|---|---|
| `peek_metadata(path_or_stream, password=)` | 无需完整解析即可快速读取元数据与页数 |
| `Permissions` | 加密权限标志（IntFlag） |
| `Rect` | 带`width` / `height`的矩形NamedTuple |
| `TextPage` / `TextBlock` / `TextLine` / `TextSpan` | `get_text("dict")`的TypedDict层级 |
| `ImageInfo` / `AnnotationInfo` / `LinkInfo` / `FormFieldInfo` | 页面与表单字典结果的TypedDict契约 |
| `PageLabelInfo` / `PageLabelSpec` | 规范化页码标签输出／setter输入契约 |
| `DocumentMetadata` / `MetadataUpdate` / `MetadataProbe` | 元数据输出／部分更新／快速探测契约 |
| `OcrEngine` / `OcrWord` | 可复用的纯Rust PP-OCR引擎与带位置结果契约 |
| `OcrRotation` / `WordEntry` / `BlockEntry` / `FormFieldType` | 可在runtime导入的OCR旋转、tuple和literal类型别名 |
| `TableFinder` / `Table` / `TableDiagnostics` | 自包含的表格几何、单元格文本（合并延续位置为`None`）、策略与置信依据 |
| `PdfError` / `PasswordError` / `OcrError` / `DocumentClosedError` / `EncryptedDocumentError` / `StalePageError` | 异常层级（基类兼容ValueError） |
| `Pixmap` | 不可变RGBA8像素：`samples` / `width` / `height` / `stride` / `n` / `tobytes()`；cp314t还支持只读、零复制的`memoryview()` |
| `PylopdfWarning` | 解释器警告（字体解析、图像解码） |

`TypedDict`契约仅影响静态类型；运行时值仍是普通的pymupdf风格字典。
`LinkInfo`要求`kind`和`from`，而各类目标专用键为可选。
`PageLabelSpec`要求`startpage`；`style`、`prefix`和`firstpagenum`的运行时默认值不变。
