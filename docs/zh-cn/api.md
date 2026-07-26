---
title: API概览
description: pylopdf的Document、Page、Pixmap、Rect、权限、警告与异常的紧凑索引。
---

# API概览

完整docstring包含在包内，可运行`help(pylopdf.Document)`查看。本页提供API地图。
除`get_toc` / `set_toc`为兼容pymupdf而从1开始外，所有页码均从0开始。
所有坐标均为左上角原点的显示空间。
[API稳定性政策](stability.md)定义了公共边界和弃用流程。

## Document { #document }

`pylopdf.Document(filename=None, stream=None, password=None, max_decompressed_size=None, *, limits=None)` —
`pylopdf.open()`是别名构造函数，并支持上下文管理器。
password input上限为127个UTF-8 byte。

| 成员 | 用途 |
|---|---|
| `doc[i]` / `load_page(pno)` / 迭代 | `Page`视图（支持负数；结构变更后需重新获取） |
| `page_count` / `len(doc)` | 页数 |
| `limits` / `complexity` | 打开时的不可变资源策略 / 无需解码stream的轻量结构指标 |
| `needs_pass` / `is_encrypted` / `authenticate(pw)` | 带127-byte password上限的加密状态与解锁（兼容pymupdf语义） |
| `is_repaired` | 打开时是否修复了最终classic `startxref`；保存会规范化xref数据 |
| `metadata` / `set_metadata(dict)` | 8个标准Info字段（支持UTF-16BE）；aggregate文本上限1 MiB，写入为原子操作 |
| `get_page_text(pno, option)` | `"text"` / `"words"` / `"blocks"` / `"dict"` |
| `to_markdown(pages=None, table_strategy="lines", max_size=64 MiB)` | 使用有界线性entry builder按页两pass转换Markdown；最多4,096页及累计UTF-8输出上限（`None`取消），含标题、CJK、强调、列表、分栏、竖排顺序及表格控制 |
| `render_page(..., max_size=64 MiB)` / `render_pages(..., workers=, max_size=512 MiB)` / `render_page_svg(..., max_size=64 MiB)` | 有上限的PNG、带4,096页及累计encoded output上限的保序并行PNG批次，或有上限的UTF-8 SVG（`None`取消） |
| `compress_images(dpi=150, quality=75)` | 按实际放置DPI对安全DCT/Flate raster XObject进行有损缩小和JPEG重压缩，并返回类型化byte/count统计 |
| `set_fallback_font(font, kind=, index=, max_font_size=64 MiB)` | 未嵌入字体时的有界CJK后备font；可信font input可用`None`取消上限 |
| `select` / `delete_page(s)` / `insert_pdf` / `new_page` / `copy_page` | 页面管理；select/delete/insert batch上限为4,096个entry |
| `get_toc()` / `set_toc(toc)` | 可处理cycle且有上限的书签（页码从1开始；4,096个entry/node、8,192条edge、64层、1 MiB文本） |
| `get_page_labels()` / `set_page_labels(labels)` | 页码标签范围；固定上限为4,096个entry/node、32层、1 MiB标签文本 |
| `get_form_fields()` / `set_form_field(name, value, fontfile=, fontbuffer=, fontindex=, max_font_size=64 MiB)` | 有界地列出与填写AcroForm，并限制原生widget外观和font input |
| `embfile_add(..., max_size=64 MiB) / embfile_names / embfile_get(name, max_size=64 MiB) / embfile_del` | 对输入与解码输出采用对称默认上限，并限制添加metadata及inline FileSpec clone形状；`max_size=None`可显式取消上限 |
| `get_pdfa_claim(max_size=1 MiB)` | 有上限地读取XMP PDF/A声明；`max_size=None`显式取消上限，且这不是验证 |
| `save(...)` / `tobytes(..., max_size=512 MiB)` | 完整写入同directory临时stream后原子替换file／有上限的PDF byte；`garbage=` `deflate=` `object_streams=`及127-byte上限的`user_pw=`／`owner_pw=`；`max_size=None`取消上限 |
| `close()` | 也可通过`with`调用 |

`compress_images()`会解释所有页面，找出每个间接raster object的最大放置尺寸，再原子地
编辑lopdf副本。`dpi=None`时不缩小，仅按quality重压缩。保守边界仅包含无mask或自定义
decode array的直接单filter 8-bit DeviceGray/DeviceRGB DCT/Flate stream。DCT decode
parameter不受支持；Flate可无predictor或使用与字典一致的PNG predictor。已解释但
不支持的间接图像以及不会变小的编码会被跳过；inline图像不计入统计。用相同设置重复
调用是幂等的。

## Page { #page }

| 成员 | 用途 |
|---|---|
| `number` / `parent` / `get_label()` | 标识与显示标签 |
| `get_text(option)` / `search_for(needle, max_hits=4096)` | 提取与有界的不区分大小写搜索；搜索词上限为4,096 UTF-8 byte，可信结果集可用`None`取消 |
| `get_text_ocr(dpi=, engine=, tile_size=, overlap=, min_confidence=, rotation=, clip=)` | 不编辑页面，通过本地PP-OCRv6返回带位置的单词；`rotation`顺时针校正输入，`clip`使用显示坐标 |
| `apply_ocr(..., rotation=, clip=, skip_existing=True)` | 插入保留方向的不可见可搜索层；默认跳过所选区域的已有文本 |
| `find_tables(strategy="lines", clip=None)` | 完整或保守补全的稀疏矢量边框与合并单元格；`"text"`启用无边框检测，`clip`指定显示坐标区域 |
| `to_markdown(table_strategy="lines", max_size=64 MiB)` | 使用相同表格及UTF-8输出控制的单页Markdown |
| `get_images()` | 已绘制图像（含`bbox`，JPEG直通 / PNG）；超过4,096个placement、累计64,000,000像素或64 MiB payload时拒绝部分结果 |
| `get_drawings()` | 页面中已解释的矢量fill/stroke路径；显示坐标中的line/cubic几何与规范化绘制属性 |
| `get_pixmap(scale=, dpi=, background=, clip=)` / `render(max_size=64 MiB)` / `render_svg(max_size=64 MiB)` | 有上限的PNG / UTF-8 SVG渲染；`clip`使用显示坐标 |
| `rotation` / `set_rotation(deg)` | 显示旋转 |
| `mediabox` / `cropbox` / `rect` / `set_mediabox` / `set_cropbox` | 页面框 |
| `insert_image(rect, filename= / stream= / pixmap=, rotate=, keep_proportion=, overlay=, max_size=64 MiB, max_pixels=64,000,000)` | 绘制有上限的JPEG/PNG或复用已有边界的RGBA `Pixmap`；可信encoded input／PNG像素可用`None`取消上限；`rotate`按90度顺时针旋转 |
| `show_pdf_page(rect, src, pno=, keep_proportion=, overlay=)` | 以矢量叠加PDF页面；`src`可为同一文档 |
| `insert_text(point, text, fontsize=, fontname=, fontfile=, fontbuffer=, fontindex=, color=, overlay=, max_font_size=64 MiB, max_text_size=1 MiB)` | 有界UTF-8 Standard-14或shape subset文本；`pylopdf[cjk]`自动选择JP font；对应可信input可用`None`取消上限 |
| `insert_textbox(rect, text, fontsize=, fontname=, fontfile=, fontbuffer=, fontindex=, color=, align=, expandtabs=, lineheight=, overlay=, max_font_size=64 MiB, max_text_size=1 MiB)` | 预检文本与tab展开，并使用Core 14、OpenType或自动JP font宽度进行UAX #14换行；溢出时不绘制 |
| `insert_ocr_text_layer(words, rotation=)` | 保留方向的OCR不可见文本层；每次call固定上限为4,096词和1 MiB UTF-8文本 |
| `replace_text(search, replacement, default_char=, max_size=64 MiB)` | 带输入输出上限和copy-on-write的原子简单编码替换 |
| `annots()` / `get_links()` / `add_highlight_annot(...)` / `add_link_annot(rect, uri)` | 有界批注／link读取、每次call一个cycle-aware named-destination index与创建 |

`get_drawings()`返回`DrawingInfo`字典，其中包含`type="f"` / `"s"` / `"fs"`、
自包含的line/cubic `items`、`rect`、RGB/opacity、fill rule、width、cap、join和
dashes。对于pattern paint，会保留几何形状，而颜色和opacity为`None`。它不返回
clip path、clip应用后的可见性判断、group/soft-mask结构、optional-content layer名称、
text、image或annotation，但仍会应用optional-content的可见性。结果超过8,192 paths
或131,072 commands时会拒绝，而不是静默截断。

使用嵌入字体的`insert_text`需要一个包含所有所需字形的字体。未传入source且安装
`pylopdf[cjk]`时，日文／汉字会自动使用JP subset的Noto Sans，Times `fontname`则使用
Noto Serif。这是整段只选一个font，并非逐glyph fallback。简体中文排版应显式传入
Noto Sans SC等匹配本地字形的OpenType font；Hangul、其他script或其他书体同样如此。
每一行会被shape，但不提供双向段落layout或换行。RTL可正确渲染，但提取目前遵循
visual order。

`insert_textbox`并非富文本引擎；它保留显式换行、展开制表符、按Unicode机会换行CJK，
并对过长单词执行grapheme安全的紧急换行。对齐常量为`TEXT_ALIGN_LEFT`、
`TEXT_ALIGN_CENTER`、`TEXT_ALIGN_RIGHT`和`TEXT_ALIGN_JUSTIFY`。返回负值表示
垂直空间不足，此时不会添加页面内容或字体resource。

`set_form_field`会为文本、组合框／列表选择、复选框和单选按钮生成外观。WinAnsi文本
使用Helvetica自动缩小；传入OpenType `fontfile`或`fontbuffer`即可对子集嵌入Unicode。
安装`pylopdf[cjk]`后，非WinAnsi值会尝试JP subset sans；中文本地字形或Hangul应传入
匹配font。已有且非空的按钮外观
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
| `peek_metadata(filename=None, stream=None, password=None, *, max_file_size=None)` | 可选输入大小限制及127-byte password上限的快速元数据与页数读取；`repaired`报告受限的classic `startxref`修复 |
| `Permissions` | 加密权限标志（IntFlag） |
| `Rect` | 带`width` / `height`的矩形NamedTuple |
| `TextPage` / `TextBlock` / `TextLine` / `TextSpan` | `get_text("dict")`的TypedDict层级 |
| `ImageInfo` / `ImageCompressionResult` / `AnnotationInfo` / `LinkInfo` / `FormFieldInfo` / `DrawingInfo` | 页面、文档操作、表单与矢量绘制结果的TypedDict契约 |
| `DrawingItem` | 表示line/cubic绘制命令的类型别名 |
| `PageLabelInfo` / `PageLabelSpec` | 规范化页码标签输出／setter输入契约 |
| `DocumentMetadata` / `MetadataUpdate` / `MetadataProbe` | 元数据输出／部分更新／快速探测契约 |
| `DocumentLimits` / `DocumentComplexity` | 不受信任输入的不可变预算／轻量结构TypedDict |
| `OcrEngine` / `OcrWord` | 可复用的纯Rust PP-OCR引擎与带位置结果契约 |
| `OcrRotation` / `WordEntry` / `BlockEntry` / `FormFieldType` | 可在runtime导入的OCR旋转、tuple和literal类型别名 |
| `TableFinder` / `Table` / `TableDiagnostics` | 自包含的表格几何、单元格文本（合并延续位置为`None`）、策略与置信依据；`Table.to_markdown(max_size=64 MiB)`预检转义后的UTF-8输出 |
| `PdfError` / `LimitError` / `PasswordError` / `OcrError` / `DocumentClosedError` / `EncryptedDocumentError` / `StalePageError` | 异常层级；资源拒绝提供稳定`.code`（基类兼容ValueError） |
| `Pixmap` | 不可变RGBA8像素：`samples` / `width` / `height` / `stride` / `n` / `tobytes(max_size=64 MiB)` / 流式写入且失败时保留现有file的PNG专用`save(path)`；cp314t还支持只读、零复制的`memoryview()` |
| `PylopdfWarning` | 可恢复的解释警告（xref修复、字体解析、图像解码） |

`TypedDict`契约仅影响静态类型；运行时值仍是普通的pymupdf风格字典。
`LinkInfo`要求`kind`和`from`，而各类目标专用键为可选。
`PageLabelSpec`要求`startpage`；`style`、`prefix`和`firstpagenum`的运行时默认值不变。
