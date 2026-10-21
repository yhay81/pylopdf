---
title: API概览
description: pylopdf的Document、Page、Pixmap、Rect、权限、警告与异常的紧凑索引。
---

# API概览

完整docstring包含在包内（`help(pylopdf.Document)`）；本页是一份地图。包括书签在内，所有页码均从0开始。所有坐标均为左上角原点的显示空间。[API稳定性政策](stability.md)定义了公共边界和弃用流程。下文的每项字节、数量和深度上限都用于保护进程免受恶意输入的影响；[处理不受信任的PDF](security.md#untrusted-pdfs)说明了该策略，超出上限时会抛出带有稳定`.code`的`LimitError`。

## Document { #document }

`pylopdf.Document(filename=None, stream=None, filetype=None, *, password=None, limits=None, max_decompressed_size=None)` —与pymupdf相同，`pylopdf.open`是同一个可调用对象，并支持上下文管理器。`stream`接受bytes、`bytearray`、`memoryview`或`io.BytesIO`；`filetype`接受`"pdf"`；与pymupdf的`open("pdf", data)`相同，与`stream`一同传入的字符串`filename`表示文件类型。

| 成员 | 用途 |
|---|---|
| `doc[i]`、`load_page(pno)`、迭代 | `Page`视图；支持负数索引。结构变更后需重新获取页面。 |
| `page_count`、`len(doc)` | 页数。 |
| `limits`、`complexity` | 打开时的不可变资源策略，以及无需解码stream即可获得的轻量结构指标。 |
| `needs_pass`、`is_encrypted`、`authenticate(pw)` | 加密状态，以及使用与pymupdf相同的返回值0/1/2/4/6解锁。以空user password打开的文档仍会检查这些返回值。 |
| `is_repaired` | 打开时是否恢复了损坏的cross-reference数据（修正offset或重建table）。保存会规范化xref数据。 |
| `objects`、`trailer`、`catalog`、`page_xref(pno)` | 对对象图的类型化访问，取代pymupdf的`xref_*`方法；参见[低层对象](#low-level-objects)。 |
| `metadata`、`set_metadata(m)` | 读写与pymupdf相同的元数据键：`format`、包括`trapped`在内的Info字段，以及`encryption`。 |
| `get_page_text(pno, option)` | 单页文本；`option`为`"text"`、`"words"`、`"blocks"`、`"dict"`或`"rawdict"`。 |
| `get_text(pages=None)` | 批量提取所有页面或指定页面列表的纯文本。 |
| `to_markdown(pages=None, table_strategy="lines")` | 包含标题、CJK、强调、列表、分栏、竖排顺序和表格的Markdown。 |
| `extract_image(xref)`、`get_page_images(pno, *, full=False)` | 以与pymupdf相同的键返回单个图像的数据，以及页面的图像清单。 |
| `get_page_pixmap(pno, *, matrix=, dpi=, clip=, alpha=False)` | 页面`Pixmap`；pymupdf为`doc[pno].get_pixmap()`提供的简便形式。 |
| `render_pages(pages=None, *, dpi=, alpha=False, workers=)` | 对所有页面或指定页面列表进行保序的并行PNG渲染。 |
| `rewrite_images(*, dpi_threshold=None, dpi_target=0, quality=0)` | 按实际放置对安全的raster图像进行缩小和JPEG重压缩；返回类型化的字节与数量统计。 |
| `set_fallback_font(font, kind=, index=, font_language="ja")` | 为未嵌入的Adobe Japan1、Korea1、GB1或CNS1字体提供区分地区的CJK后备字体。 |
| `select`、`delete_page(s)`、`insert_pdf(docsrc, *, from_page=-1, to_page=-1, start_at=-1)`、`new_page`、`fullcopy_page` | 页面管理。 |
| `get_outline()`、`set_outline(entries)` | 以`OutlineEntry(level, title, page)`项表示的书签，页码从0开始。 |
| `get_page_labels()`、`set_page_labels(labels)` | 页码标签范围。 |
| `get_form_fields()`、`set_form_field(name, value, font=, fontfile=)` | 列出AcroForm字段，并以原生widget外观填写字段；参见[表单填写](#form-filling)。 |
| `attachments` | 由`Attachment`句柄组成的`Attachments`映射。 |
| `get_pdfa_claim()` | 读取XMP中的PDF/A声明。这不是验证。 |
| `save(...)`、`tobytes(...)` | 将PDF写入文件，或以bytes返回。 |
| `close()` | 关闭文档；也可通过`with`调用。 |

**详情**

- `set_metadata`支持UTF-16BE，忽略`format`与`encryption`，值为假时删除该项，传入`{}`时删除Info字典，写入为原子操作。
- `get_text`将显式entry按4,096个一组的chunk处理，每个chunk共享一个interpreter字体cache。`to_markdown`按页分两个pass工作，并使用有界的线性entry builder。
- `extract_image`将DCT以JPEG直通，将其他filter解码为PNG，不含soft mask。
- `get_page_pixmap`和`render_pages`都接受`matrix=`、`dpi=`、`alpha=`和`background=`；`get_page_pixmap`还接受`clip=`。
- `rewrite_images`接受与pymupdf相同的关键字`dpi_threshold=None`、`dpi_target=0`、`quality=0`、`lossy=True`、`lossless=True`、`color=True`和`gray=True`：超过`dpi_threshold`的轴缩小到`dpi_target`，`quality`为0表示不重压缩，因此缩小需要`quality`。`set_to_gray`和`options`会抛出异常。参见上限列表下方的段落。
- `insert_pdf`接受源文档的任意完整范围，并注册导入页面的表单字段。`select`和`delete_page(s)`会删除被丢弃页面的widget、字段和tagged structure内容。
- 书签中的named destination与`get_links()`一样解析。
- `set_form_field`还接受`fontbuffer=`、`fontindex=`和`font_language=`；`font`以已加载的OpenType `Font`绘制。
- `attachments`支持`add(name, data, /, *, filename, description)`和`del`。每个`Attachment`具有`filename`、`description`、`size`、`creation_date`、`mod_date`、`read()`和`write(data, /)`。
- `save`在同一目录中完成流式写入后原子替换文件。`save`和`tobytes`都接受`garbage=0`–`4`（与pymupdf相同的级别；3和4合并相同的对象和流）、`deflate=`、`use_objstms=`、`encryption=`（默认`PDF_ENCRYPT_NONE`，或`PDF_ENCRYPT_AES_256`；未指定`PDF_ENCRYPT_AES_256`时传入密码会抛出异常）、`user_pw=`和`owner_pw=`。

**上限**

- 密码（`password=`、`authenticate`、`user_pw=`、`owner_pw=`）上限为127 UTF-8 byte。
- `set_metadata`：文本合计1 MiB。
- `get_text`、`to_markdown`、`render_pages`、`select`和`delete_page(s)`的显式`pages` entry：最多为页数与4,096中较大者。`pages=None`覆盖所有页面。
- `to_markdown(max_size=64 MiB)`：累计UTF-8输出。
- `render_pages(max_size=512 MiB)`：累计encoded PNG输出。
- `tobytes(max_size=512 MiB)`：serialize后的PDF字节。
- `set_fallback_font(max_font_size=64 MiB)`和`set_form_field(max_font_size=64 MiB)`：字体输入。`set_form_field`还将caller名称和值限制为1 MiB，并限制widget外观。
- `get_outline` / `set_outline`：65,536个entry/node、131,072条edge、64层和16 MiB文本，面向大型手册且可处理cycle。`select`、`delete_page(s)`中的navigation清理以及`insert_pdf`中的link导入共享TOC和named destination上限。
- 页码标签：固定上限为4,096个entry/node、32层和1 MiB标签文本。
- 附件：`add(..., max_size=64 MiB)`、`read(*, max_size=64 MiB)`和`write(data, /, *, max_size=64 MiB)`对输入和解码输出采用对称的默认上限；caller文本上限为1 MiB，元数据和inline FileSpec的clone形状也有上限。
- `get_pdfa_claim(max_size=1 MiB)`：解码后的XMP。
- `rewrite_images`：单次调用解释超过65,536个间接raster placement时会被拒绝。
- `max_size=None`和`max_font_size=None`可为可信输入或输出取消相应的字节上限。

`rewrite_images()`会解释所有页面，找出每个间接raster对象的最大放置尺寸，再原子地编辑lopdf副本。`dpi=None`时不缩小，但仍按quality重压缩。保守边界仅包含无mask或自定义decode array的直接单filter 8-bit DeviceGray/DeviceRGB DCT或Flate stream。DCT decode parameter不受支持；Flate可无predictor或使用一致的PNG predictor。已解释但不支持的间接图像以及不会变小的编码会被跳过；inline图像不在考虑范围内。用相同设置重复调用是幂等的。

## Page { #page }

| 成员 | 用途 |
|---|---|
| `number`、`parent`、`get_label()` | 标识与显示标签。 |
| `xref`、`obj` | 页面字典的对象编号与实时低层句柄。继承的属性仍位于其祖先节点。 |
| `get_text(option)` | 纯文本或带位置的提取，包括glyph级的`"rawdict"`；参见[带位置的文本](#positioned-text)。 |
| `search_for(text, max_hits=4096)` | 不区分大小写的搜索，返回命中矩形。 |
| `get_text_ocr(dpi=, engine=, rotation=, clip=)` | 通过本地PP-OCRv6返回带位置的单词，不编辑页面。 |
| `apply_ocr(..., rotation=, clip=, skip_existing=True)` | 识别文本并插入保留方向的不可见可搜索层。默认情况下，所选区域内已有文本时跳过该页面。 |
| `insert_ocr_text_layer(words, rotation=)` | 根据给定的单词插入保留方向的不可见OCR层。 |
| `find_tables(clip=None, strategy=None)` | 完整或保守补全的矢量边框网格及合并单元格；`"text"`启用无边框检测。参见[表格](#tables)。 |
| `to_markdown(table_strategy="lines")` | 使用相同表格与输出控制的单页Markdown。 |
| `get_images(*, full=False)` | 以`ImageItem` NamedTuple表示的pymupdf图像XObject清单；`ImageItemFull`增加`referencer`。 |
| `get_image_info(*, hashes=False, xrefs=False)` | 包含`bbox`、`transform`、尺寸、颜色空间的放置字典，`xrefs=True`时还包含`xref`。 |
| `get_drawings()` | 已解释的矢量填充与描边路径；参见[矢量绘图](#vector-drawings)。 |
| `get_pixmap(*, matrix=None, dpi=None, clip=None, alpha=False)` | 与pymupdf相同地渲染为`Pixmap`：默认为白底不透明RGB，`alpha=True`时为straight-alpha RGBA。 |
| `get_svg_image(*, matrix=None, text_as_path=True)` | 以路径绘制文本的SVG渲染。 |
| `rotation`、`set_rotation(deg)` | 显示旋转。 |
| `mediabox`、`cropbox`、`rect`、`set_mediabox`、`set_cropbox` | 遵循pymupdf约定的页面框。 |
| `insert_image(rect, filename= / stream= / pixmap=, rotate=)` → xref | 绘制JPEG或PNG，或复用已有边界的RGBA `Pixmap`。再次放置相同输入时共享同一个图像XObject。 |
| `show_pdf_page(rect, docsrc, pno=)` → Form xref | 以矢量叠加PDF页面；`docsrc`可以是同一文档。 |
| `insert_text(point, text, fontsize=, font=)` | 绘制Standard 14 WinAnsi文本或经shaping的subset文本，并返回绘制的行数；参见[文本插入](#text-insertion)。 |
| `insert_textbox(rect, text, fontsize=, font=, align=)` | 使用UAX #14换行将文本排入矩形；溢出时不绘制任何内容。 |
| `new_shape()` | 一次绘制路径和文本的pymupdf `Shape`；参见[图形](#shapes)。 |
| `draw_*`方法 | 绘制一个已完成的图形，并返回与pymupdf相同的终点`Point`；参见[绘制方法](#drawing-methods)。 |
| `replace_text(search, replacement, default_char=)` | 在简单编码字体中进行原子的copy-on-write替换。 |
| `annots(*, types=None)` | 只读`Annot`句柄（`type`、`rect`、`info`、`colors`、`flags`、`xref`），类型为`PDF_ANNOT_*`，标志为`PDF_ANNOT_IS_*`。 |
| `get_links()` | 携带pymupdf的`xref`与`id`（`/NM`）的link字典。 |
| `add_highlight_annot(quads, ...)` | 添加高亮并返回其`Annot`。 |
| `insert_link(link, /)` | 从link字典创建URI link。 |

**详情**

- `get_text_ocr`还接受`tile_size=`、`overlap=`和`min_confidence=`。与`apply_ocr`相同，`rotation`顺时针校正输入，`clip`使用显示坐标。参见[OCR](ocr.md)。
- `find_tables(clip=)`是显示坐标中的区域。
- `get_pixmap`：`dpi`优先于缩放`matrix`，`clip`使用显示坐标，`background=`设置不透明背景。
- `mediabox`为PDF坐标，`cropbox`以MediaBox顶边为基准翻转y轴。`set_cropbox`只接受位于MediaBox内的矩形，`set_mediabox`会删除CropBox、BleedBox、TrimBox与ArtBox。
- `insert_image`和`show_pdf_page`还接受`keep_proportion=`和`overlay=`。`rotate`以90度为单位顺时针旋转。再次放置未编辑`docsrc`的页面时共享其Form XObject。
- `insert_text`和`insert_textbox`还接受`lineheight=`、`fontname=`、`fontfile=`、`fontbuffer=`、`fontindex=`、`font_language=`、`color=`和`overlay=`；`insert_textbox`还接受`expandtabs=`。`font`以已加载的`Font`绘制，已安装的script／locale provider会自动选择，歧义汉字可用`font_language`指定。
- `get_links()`中的named destination只建立一次index，并在下次编辑前供后续读取复用。
- 渲染时会为带有效`QuadPoints`且在上限内的RGB Highlight、Underline、StrikeOut和Squiggly字典保守补全缺失的外观，同时不修改源PDF。

**上限**

- `search_for`：搜索词上限为4,096 UTF-8 byte，结果上限为`max_hits=4096`；`None`可为可信结果集取消上限。
- `get_drawings`：输出超过8,192条路径、131,072条命令或累计131,072个虚线值时会被拒绝，而不是返回部分结果。
- `get_svg_image(max_size=64 MiB)`：UTF-8输出。
- `insert_image(max_size=64 MiB, max_pixels=64,000,000)`：encoded输入与解码后的PNG像素。
- `insert_text`和`insert_textbox`：`max_font_size=64 MiB`、`max_text_size=1 MiB` UTF-8，以及4,096行。对于`insert_textbox`，物理行和换行后的layout都在4,096行停止，tab展开会预检。
- `insert_ocr_text_layer`：每次调用固定上限为4,096个单词和1 MiB UTF-8文本。
- `replace_text(max_size=64 MiB)`：输入和输出都有上限。
- `get_links()`的named destination：name tree最多65,536个entry/node、131,072条edge、32层和16 MiB key。
- 这些`max_*`参数传入`None`时，可为相应的可信输入取消上限。

### 带位置的文本与字形 { #positioned-text }

`Page.get_text("rawdict")`和`Document.get_page_text(pno, "rawdict")`返回glyph级文本。结果保留`"dict"`的层级：页面`width`/`height`、文本块（`number`、`type=0`、`bbox`、`lines`）、行（`bbox`、`wmode`、`dir`、`spans`）以及span元数据（`bbox`、`origin`、`size`、`font`、`flags`）。每个span以`chars`列表代替`text`：

| `TextChar`键 | 含义 |
|---|---|
| `c` | 一个已解释PDF glyph的完整Unicode映射；可能包含多个Unicode字符 |
| `bbox` | 使用现有glyph几何计算的近似轴对齐边界`(x0, y0, x1, y1)` |
| `origin` | glyph基线的起点`(x, y)` |

```python
import pylopdf

with pylopdf.open("report.pdf") as doc:
    layout = doc[0].get_text("rawdict")
    for block in layout["blocks"]:
        for line in block["lines"]:
            for span in line["spans"]:
                for char in span["chars"]:
                    print(char["c"], char["bbox"], char["origin"])
```

位置使用与渲染相同的显示点：左上角原点，x向右、y向下，并已应用页面旋转与CropBox偏移。默认`/UserUnit=1`时，一点为1/72英寸；当前引擎不会对非默认的`/UserUnit`值应用物理缩放。边界是基于基线和字号的近似几何，并非精确的墨迹轮廓或四边形。字体名称和样式标志沿用现有`"dict"`的解释。

一个entry表示一个已映射的glyph，不一定是一个Unicode码位或grapheme。连字和其他映射在`c`中保持完整；不会为根据几何推断的空格或换行虚构额外entry。已映射glyph中显式存在的空格会保留。因此，把`c`值连接起来不一定能重现`get_text("text")`的间距。未映射的glyph会被跳过，并发出与其他提取模式相同的去重`PylopdfWarning`。

阅读顺序遵循现有的行、分栏以及保守的RTL/CJK heuristic。竖排CJK模式根据glyph的放置推断；ruby、割注、一般的混合方向段落以及混合方向排版仍是近似的。任意角度基线的阅读顺序可能与源顺序或语义顺序不同；应用需要自行排序时，请使用glyph的origin和行的`dir`。`rawdict`报告提取出的PDF文本，包括已有的不可见文本层；它不会运行OCR。

其层级与[PyMuPDF rawdict](https://pymupdf.readthedocs.io/en/latest/textpage.html#structure-of-dictionary-outputs)相似，但并非完整的兼容契约。pylopdf只返回文本块，在一个entry中保留多字符glyph映射，并提供现有的span元数据，而不提供PyMuPDF额外的图像／矢量、颜色、不透明度、字体度量或合成字符字段。提取图像或矢量时，请使用`get_image_info()`配合`extract_image()`，或使用`get_drawings()`。[叠加示例](https://github.com/yhay81/pylopdf/blob/main/examples/rawdict-overlay.py)会生成显示glyph边界和基线origin的独立HTML预览。

### 矢量绘图 { #vector-drawings }

`get_drawings()`返回`DrawingInfo`字典，其中包含`type="f"`、`"s"`或`"fs"`、自包含的line/cubic `items`、`rect`、RGB/opacity、fill rule、width、cap、join、dashes，以及`seqno`（该绘制操作在页面路径、文本和图像中的位置）。几何位于显示空间，绘制与描边属性均已规范化。pattern paint保留其几何，颜色和opacity为`None`。不返回clip path、clip应用后的可见性、group与soft-mask结构、optional-content layer名称、文本、图像或批注；但仍会应用optional-content的可见性。结果超过8,192条路径或131,072条命令时会被拒绝，而不是被截断。

### 文本插入 { #text-insertion }

使用嵌入字体的`insert_text`需要一个包含所有字形的字体。`pylopdf[ar]`、`[he]`、`[hi]`、`[jp]`、`[ko]`、`[th]`、`[zh-cn]`和`[zh-tw]`提供Noto Sans/Serif生成字体。假名、谚文、注音符号、阿拉伯文、希伯来文、天城文和泰文会自动选择已安装的provider。纯汉字保持JP优先的默认行为；地区字形很重要时，请传入`font_language="zh-CN"`、`"zh-TW"`、`"ko"`或`"ja"`。整段只使用一种字体，并非逐glyph的fallback。每一行都会经过shaping，但双向段落layout与换行不在此基本操作的范围内。RTL的shaping可正确渲染。纯RTL行，以及只有一个被RTL文字包围的拉丁或数字token的行，会按逻辑Unicode顺序提取。行首或行尾的token、多个token以及一般的混合方向段落仍保留producer的visual order。

复杂shaping可能需要PDF `/ActualText`来保留与glyph并非一一对应的逻辑字符串。生成的PDF为外部查看器包含该数据，但pylopdf当前的提取器不读取`/ActualText`；因此重新打开后，部分经过重排或依赖上下文的cluster（尤其是较长的天城文文本）的提取结果可能是近似的。

`insert_text`用`str.splitlines()`拆分字符串，列表则每项为一行。它省略超出页面底边的行，并返回绘制的行数。

`insert_textbox`增加了换行功能，但并非富文本引擎。它保留显式换行、展开制表符、按Unicode换行机会断开CJK，并对过长单词执行grapheme安全的紧急换行。对齐常量为`TEXT_ALIGN_LEFT`、`TEXT_ALIGN_CENTER`、`TEXT_ALIGN_RIGHT`和`TEXT_ALIGN_JUSTIFY`。返回负值表示垂直方向的不足量；此时不会添加任何页面内容或字体资源。

两个方法都与pymupdf相同地排列行：默认情况下，行间距为`fontsize`的`lineheight`倍，`lineheight`默认为字体的`ascender`减去`descender`。

??? note "行排列详情"

    `insert_text`把每一行放在上一行下方`fontsize`的`lineheight`倍处；省略`lineheight`时，倍数为字体的`ascender`减去`descender`，该差不大于1时为1.2，因此10磅的Helvetica行距为13.74磅。与pymupdf相同，该差按MuPDF报告的单精度度量比较，在这种度量下，ascender为0.8、descender为-0.2的字体略大于1 em，行距保持1 em。

    `insert_textbox`使用相同的默认值，把第一条基线放在`rect`顶边下方一个ascender处，之后各行依次下移ascender的`lineheight`倍；判断文本能否放下以及报告剩余高度时，每行计为`fontsize`的`lineheight`倍，另加一个descender。

    `insert_text`也与pymupdf相同地决定绘制哪些行。字符串用`str.splitlines()`分行，因此末尾的换行不会增加行，`\v`、`\f`、`\x1c`至`\x1e`、`\x85`、`\u2028`与`\u2029`也与`\n`、`\r`、`\r\n`一样分隔行；列表或元组每项为一行，按原样使用。第一行总会绘制，之后的行仅在其基线不低于`page.rect`底边时绘制。返回值为绘制的行数；像`""`或`"\n"`这样没有可绘制内容的文本不插入任何内容并返回0。pymupdf在未旋转的空间中测量到用户空间y = 0为止的空间，pylopdf则在显示坐标中测量到`page.rect`的底边；两者在未旋转且可见区域在y = 0处结束的页面上一致。字体仍会检查每一行，包括超出页面的行。

### 字体 { #fonts }

#### 加载字体

`Font`遵循pymupdf的`Font(fontname=None, fontfile=None, fontbuffer=None)`，并以`insert_text(font=)`与`insert_textbox(font=)`传入；OpenType字体也可以传给`set_form_field(font=)`。

```python
helvetica = pylopdf.Font("helv")
noto = pylopdf.Font(fontfile="NotoSansJP-Regular.otf")
page.insert_text((72, 72), "Hello", font=noto, fontsize=12)
```

`Font("helv")`或`Font("Times-Bold")`指定Standard 14字体，不嵌入即可绘制，并按Adobe的AFM宽度测量；pymupdf的其他内置字体不可用。`Font(fontfile=)`与`Font(fontbuffer=)`按原样加载OpenType字体。`Font(language="ja")`或`Font(family="Noto Sans JP")`选择已安装字体extra的字体，`is_serif=True`选择衬线体。

`font.text_length(text, fontsize)`使用与绘制相同的shaping（含字距调整与连字）进行测量，因此layout代码可以按实际绘制的字体计算换行、对齐与分页。`has_glyph(chr)`返回码位的字形编号，不存在时返回0。

pylopdf不保存字体注册表，也从不搜索系统字体目录。每次绘制调用会嵌入该调用所用字形的子集；通过[图形](#shapes)绘制，则每次提交每种字体只嵌入一次。

#### 字重与可变字体

`Font(fontfile=)`与`Font(fontbuffer=)`以默认实例加载可变字体，默认实例不一定是Regular：可变字体Noto Sans CJK JP的默认实例是Thin。`weight=`可从可变字体的字重轴选择其他实例。

```python
bold = pylopdf.Font(fontfile="NotoSansCJKjp-VF.otf", weight=700)
page.insert_text((72, 100), "Summary", font=bold, fontsize=18)
```

`Font(fontfile="NotoSansCJKjp-VF.otf", weight=700)`按Bold实例的前进宽度测量，并以`NotoSansCJKjp-Bold`嵌入。`weight=650`同样可用，因此对这类字体，`weight=400`与省略`weight`的结果不同。超出字重轴范围的字重会抛出`PdfError`，静态字体未声明的字重也一样。`insert_text`、`insert_textbox`与`set_form_field`的`fontfile`和`fontbuffer`参数以默认实例绘制；要以所选实例绘制，请将该`Font`传给`font=`。

CJK的extra每种样式只安装一个静态字体，因此与字体不符的`is_bold`、`is_italic`或精确的OS/2 `weight`会抛出`PdfError`；Noto Sans JP Bold等其他字重请从其文件加载。而`pylopdf[ar]`、`[he]`、`[hi]`和`[th]`的extra安装的是可变字体：`Font(language="he", is_bold=True)`无需其他文件即可从字体的字重轴选择Bold实例，按该实例的前进宽度测量，并以其自己的PostScript名称`NotoSansHebrew-Bold`嵌入。字重范围为100到900，`pylopdf[ar]`的衬线字体Noto Naskh Arabic为400到700；轴外的字重或斜体会抛出`PdfError`。

已安装的可变字体以Regular字重绘制，`insert_text`、`insert_textbox`与`set_form_field`自动选择它们时也一样，因为Noto Sans Hebrew的默认实例是Thin。pylopdf从不用描边或叠印字形来模仿字重；`allow_regular_fallback=True`会显式接受extra的字体并发出`PylopdfWarning`。与`fontname`或字体文件一起传入`is_bold`、`is_italic`或`is_serif`时会抛出异常，而pymupdf会忽略它们。

#### 行度量

`ascender`与`descender`是pymupdf报告的em比例，`insert_text`与`insert_textbox`用它们排列行。对OpenType字体，在字体设置了`USE_TYPO_METRICS`时取OS/2的typographic度量，否则取`hhea`度量，与FreeType为MuPDF读取的值相同。对Standard 14字体，它们是MuPDF绘制该字体时所用URW字体的外接矩形，例如Helvetica为1.075与-0.299。

### 图形 { #shapes }

`page.new_shape()`返回pymupdf的`Shape`。`draw_line`、`draw_polyline`、`draw_rect`（`radius=`使角变圆）、`draw_quad`、`draw_circle`、`draw_oval`、`draw_sector`、`draw_curve`、`draw_bezier`、`draw_squiggle`和`draw_zigzag`在显示坐标中添加路径段，并返回与pymupdf相同的终点；从上一段终点开始的路径段会延续其子路径。`finish(*, width=1, color=(0,), fill=None, lineCap=0, lineJoin=0, dashes=None, even_odd=False, closePath=True, fill_opacity=1, stroke_opacity=1)`对上一次`finish`之后绘制的所有内容进行描边和填充；颜色为0到1的灰度、RGB或CMYK，虚线为`"[3 4] 0"`这样的PDF虚线模式。`insert_text`与`insert_textbox`接受`Page.insert_text`与`Page.insert_textbox`的字体选项（包括`font=`与`lineheight=`），并与这些方法相同地排列行。`insert_text`也与`Page.insert_text`相同地分行并省略超出页面底边的行，返回添加的行数；`insert_textbox`返回未使用的高度，放不下时返回负值且不添加任何内容。`commit(overlay=True)`绘制图形：与pymupdf相同，先按顺序绘制已完成的路径，再在其上绘制所有文本。

```python
regular = pylopdf.Font("helv")
bold = pylopdf.Font("hebo")
body = "Findings are listed below. " * 20

with page.new_shape() as shape:
    shape.draw_rect((50, 50, 545, 792))
    shape.finish(color=(0.72, 0.78, 0.82), fill=(0.95, 0.96, 0.97), width=0.5)
    shape.draw_circle((80, 140), 7)
    shape.finish(color=(1, 1, 1), fill=(0.83, 0.26, 0.05), width=1.2)
    shape.insert_text((56, 80), "Inspection report", font=bold, fontsize=18)
    shape.insert_textbox((56, 160, 540, 400), body, font=regular, fontsize=10.5)
```

每次提交放置一个Form XObject。路径和嵌入字体的文本一起生成，因此无论有多少行使用同一字体，每次提交该字体只嵌入一次；用一个图形绘制一页的文本，则每页每种字体只嵌入一次。`with`块正常结束时提交，发生异常后不绘制任何内容；含有从未提交的绘制的图形被丢弃时会发出`ResourceWarning`。在pymupdf静默丢弃绘制或将其涂黑的情况下，pylopdf会抛出`PdfError`：`commit()`拒绝未经`finish()`设置样式的绘制，`finish()`拒绝不绘制任何内容的样式（无填充，且`color=None`或`width=0`）。`morph=`与`oc=`不可用。提交前一个图形最多容纳1,048,576个路径点；超出时抛出code为`path_point_count`的`LimitError`。

#### 绘制方法 { #drawing-methods }

`Page.draw_*`方法以关键字参数接受相同的选项，各绘制一个已完成的图形，并返回与pymupdf相同的终点`Point`：

- 方法：`draw_line`、`draw_polyline`、`draw_rect`、`draw_quad`、`draw_circle`、`draw_oval`、`draw_sector`、`draw_curve`、`draw_bezier`、`draw_squiggle`和`draw_zigzag`，每个方法先接受其几何参数。
- 通用关键字：`color=(0,)`、`fill=None`、`width=1`、`dashes=None`、`lineCap=0`、`lineJoin=0`、`closePath=`、`overlay=True`、`stroke_opacity=1`和`fill_opacity=1`。`fill`与`closePath`仅在pymupdf提供它们的方法上可用。
- 方法专用关键字：`draw_rect`的`radius=`、`draw_sector`的`fullSector=`，以及波浪线（`draw_squiggle`、`draw_zigzag`）的`breadth`；与pymupdf相同，`breadth`也可在点之后按位置传入。

### 表单填写 { #form-filling }

`set_form_field`会为文本、组合框／列表选择、复选框和单选按钮widget生成外观。WinAnsi文本使用Helvetica自动缩小；传入OpenType `fontfile`或`fontbuffer`即可子集嵌入Unicode，也可以将已加载的OpenType `Font`（例如可变字体的Bold实例）传给`font`。使用`Font`时，按`text_length`测量的前进宽度缩小与对齐文本，并居中comb位置；它不能与其他字体参数同时使用，Standard 14的`Font`会抛出`PdfError`。已安装的locale／script provider按与`insert_text`相同的规则选择，歧义汉字可用`font_language`指定。

已有且非空的复选框／单选按钮外观会保留，缺失的状态会获得矢量标记。其他WinAnsi字段缺失的外观也会同时补齐；仅当所有可填写widget都自包含时才清除`NeedAppearances`。comb文本字段遵循继承的`MaxLen`与对齐方式，将每个Unicode grapheme置于相应位置的中央，并原子地拒绝超长值。富文本、pushbutton动作和签名不在生成范围内。

### 表格 { #tables }

`Table.confidence`是0–1的确定性排序heuristic，并非经过校准的概率。`Table.diagnostics`是`TableDiagnostics` tuple，包含所用策略，对无边框文本表格还包含以em归一化的对齐误差、最小列间距和行间距变化。完整矢量网格得分为1.0，补全稀疏边框的hybrid grid为0.95，两者的这些文本专用指标均为`None`。`TableFinder.strategy`和`TableFinder.clip`保留所用的设置。

## 低层对象 { #low-level-objects }

`doc.objects`将对象编号映射到文档的间接对象；`doc.trailer`、`doc.catalog`和`page.obj`是字典句柄。这一类型化模型取代pymupdf基于字符串的`xref_*`方法：对象是Python值，而不是PDF语法。

```python
from pylopdf import Name

catalog = doc.catalog
catalog["PageLayout"] = Name("TwoColumnLeft")   # 名称；普通str会写成文本字符串
font = doc.objects.add({"Type": Name("Font"), "Subtype": Name("Type1"), "BaseFont": Name("Helvetica")})
content = doc.objects.add_stream(b"BT /F9 24 Tf 72 72 Td (Hi) Tj ET")
page.obj["Resources"] = {"Font": {"F9": doc.objects[font]}}   # 间接句柄以引用形式存储
page.obj["Contents"] = doc.objects[content]
doc.objects[content].read()                    # 已解码，默认上限64 MiB
```

| 值 | Python |
|---|---|
| null、布尔值、整数、实数 | `None`、`bool`、`int`、`float` |
| 名称 | `Name`，不含斜杠的`str`子类 |
| 字符串 | `String`，`bytes`子类，`.text`解码PDF文本字符串 |
| 数组 / 字典 / 流 | 实时的`Array`（可变序列） / `Dictionary`（可变映射） / `Stream`（带有`read(*, decode=True, max_size=64 MiB)`和`write(data, *, filter="FlateDecode")`的字典） |

引用在读取时解析，指向不存在对象的引用读作`None`。写入立即生效并丢弃渲染与提取缓存；改变页面顺序的写入还会使现有`Page`视图失效。列表和映射写成直接对象，存入容器的间接句柄成为引用，而赋值给`doc.objects[xref]`或传给`add()`的句柄会被复制。对象被删除，或`save(garbage=2)`及以上重新编号后，句柄会抛出`StaleObjectError`。保持原始编辑在语义上有效是调用方的责任。

## 模块级名称 { #module-level }

| 名称 | 用途 |
|---|---|
| `peek_metadata(filename=None, stream=None, password=None, *, max_file_size=None)` | 快速读取元数据与页数；`repaired`报告cross-reference恢复。 |
| `Permissions` | 加密权限标志（IntFlag），成员即pymupdf的`PDF_PERM_*`常量。 |
| `EncryptionMethod` | `save(encryption=)`的加密方式（IntEnum），成员即pymupdf的`PDF_ENCRYPT_*`常量。 |
| `Base14_fontnames`、`Base14_fontdict` | pymupdf的Standard 14 PostScript名称，以及`fontname=`背后的只读对照表；不区分大小写地接受缩写与完整名称。 |
| `LinkKind`、`TextAlign` | 成员即pymupdf的`LINK_*`与`TEXT_ALIGN_*`常量的IntEnum。 |
| `Rect`、`IRect` | 不可变的`(x0, y0, x1, y1)` NamedTuple，支持包含判断、并集、交集、变换和取整。 |
| `Quad` | 由四个`Point`组成的不可变`(ul, ur, ll, lr)` NamedTuple，支持变换、包含判断和形状测试。 |
| `Point` | 不可变的`(x, y)` NamedTuple，支持向量运算、仿射变换、距离和方向。 |
| `Matrix`、`IdentityMatrix`、`Identity` | 不可变的仿射`(a, b, c, d, e, f)` NamedTuple，支持组合与求逆，以及单位矩阵。 |
| `EMPTY_RECT()`、`INFINITE_RECT()`及其`IRECT` / `QUAD`形式、`FZ_MIN_INF_RECT`、`FZ_MAX_INF_RECT`、`planish_line(p1, p2)` | pymupdf的几何常量与使线段水平化的矩阵。 |
| `TextPageDict`、`TextBlock`、`TextLine`、`TextSpan` | `get_text("dict")`的TypedDict层级。 |
| `RawTextPageDict`、`RawTextBlock`、`RawTextLine`、`RawTextSpan`、`TextChar` | `get_text("rawdict")`的TypedDict层级，包含glyph映射、边界和基线origin。 |
| `ImageInfo`、`ImageCompressionResult`、`DrawingInfo`、`AnnotationInfo`、`LinkInfo`、`FormFieldInfo` | 映射形式的页面、文档操作与表单结果的TypedDict契约。 |
| `PageLabelInfo`、`PageLabelSpec` | 规范化的页码标签输出与setter输入契约。 |
| `DocumentMetadata`、`MetadataUpdate`、`MetadataProbe` | 元数据输出、部分更新与快速探测契约。 |
| `DocumentLimits`、`DocumentComplexity` | 不受信任输入的不可变预算，以及轻量结构TypedDict。 |
| `OcrEngine`、`OcrWord` | 可复用的纯Rust PP-OCR引擎，及其带位置结果契约。 |
| `Font(fontname=None, fontfile=None, fontbuffer=None, *, language=None, weight=None)` | 可复用的Standard 14或OpenType字体；参见[字体](#fonts)。 |
| `Shape(page)` | 一次绘制路径和文本的pymupdf builder；由`page.new_shape()`创建。 |
| `Name`、`String`、`Array`、`Dictionary`、`Stream`、`Objects` | 低层PDF值与实时对象句柄（[低层对象](#low-level-objects)）。 |
| `Word`、`Block` | 按pymupdf元组顺序的`get_text("words")`与`get_text("blocks")` NamedTuple。 |
| `OcrRotation`、`DrawingItem`、`FormFieldType` | 可在运行时导入的OCR旋转、矢量命令与literal类型别名。 |
| `TableFinder`、`Table`、`TableDiagnostics` | 自包含的表格几何、单元格文本（合并延续位置为`None`），以及策略与置信度依据；参见[表格](#tables)。 |
| `PdfError`、`LimitError`、`PasswordError`、`FileDataError`、`EmptyFileError`、`OcrError`、`DocumentClosedError`、`EncryptedDocumentError`、`StalePageError`、`StaleObjectError` | 异常层级，基类兼容ValueError。 |
| `Pixmap` | 不可变的RGB（`n == 3`）或straight-alpha RGBA（`n == 4`）像素。 |
| `Colorspace`、`csRGB`、`csGRAY`、`csCMYK`、`CS_RGB`、`CS_GRAY`、`CS_CMYK` | pymupdf的设备颜色空间及其编号。 |
| `PylopdfWarning` | 可恢复的解释警告（xref修复、字体解析、图像解码）。 |

**详情**

- 设置了`max_file_size`时，`peek_metadata`会拒绝超过该值的输入，并将密码限制为127 UTF-8 byte。
- `DocumentLimits`包括用于PDF snapshot的`max_interpretation_size`和用于带位置layout的`max_text_glyphs`。
- `OcrEngine.recognize(pixmap, *, tile_size=, overlap=, min_confidence=, rotation=)`以像素坐标读取任意`Pixmap`。
- `Font`还接受`is_bold=False`、`is_italic=False`、`is_serif=False`、`fontindex=0`、`family=None`、`allow_regular_fallback=False`和`max_font_size=64 MiB`。它提供`text_length`、`ascender` / `descender`、`has_glyph`、`name`、`family`、`weight`、`is_bold`、`is_italic`、`is_serif`和`units_per_em`；`weight=`也可选择可变字体文件的实例。
- `Table.to_markdown(max_size=64 MiB)`会预检转义后的UTF-8输出。
- 超出上限的失败提供稳定的`.code`。无法读取的输入抛出`FileDataError`（同时是`RuntimeError`），空输入抛出`EmptyFileError`，文件不存在时抛出内置的`FileNotFoundError`。
- `Pixmap`提供`samples`、`width`、`height`、`stride`、`n`、`alpha`、`colorspace`、`tobytes(max_size=64 MiB)`，以及流式写入、失败时保持原子性的PNG专用`save(filename)`。所有wheel还支持只读、零复制的`memoryview()`。

`TypedDict`契约仅影响静态类型；运行时值仍是普通的pymupdf风格字典。`LinkInfo`要求`kind`和`from`，各类目标专用键为可选。`PageLabelSpec`要求`startpage`；`style`、`prefix`和`firstpagenum`保留其运行时默认值。

## 点与矩阵几何 { #geometry }

`Point()`创建原点；`Point(x, y)`和`Point((x, y))`创建一个位置。`Matrix()`创建零矩阵，`Matrix(sx, sy)`表示缩放，`Matrix(degrees)`表示旋转，`Matrix(sx, sy, 1)`表示错切。要指定显式变换，请传入六个坐标或一个六项序列。提供所有坐标时也支持坐标关键字参数。两个类都是不可变NamedTuple：索引、解包、元组相等比较、哈希和pickle都与元组相同，而算术运算返回几何值。

```python
from pylopdf import Matrix, Point

scale = Matrix(2, 3)
shift = Matrix(1, 0, 0, 1, 20, 30)
transform = scale * shift
position = Point(10, 20) * transform  # Point(x=40.0, y=90.0)
original = position / transform      # Point(x=10.0, y=20.0)
page.insert_text(position, "Transformed position")
```

乘法从左到右组合变换。点`(x, y)`乘以`(a, b, c, d, e, f)`得到`(a*x + c*y + e, b*x + d*y + f)`。由于显示空间的y轴向下，`Matrix(90)`把`(1, 0)`映射到`(0, 1)`。`~matrix`返回其逆矩阵，奇异时返回零矩阵；除数奇异时，`point / matrix`和`matrix / other`抛出`ZeroDivisionError`。有限变换的结果无法表示时也会抛出`PdfError`。

| 运算 | 结果 |
|---|---|
| `point + other`、`point - other` | 与点、两项序列或标量逐分量运算 |
| `matrix + other`、`matrix - other` | 与矩阵、六项序列或标量逐分量运算 |
| `value * scalar`、`value / scalar`、`-value`、`+value` | 新的值；一元`+`返回同一个不可变值 |
| `abs(value)`、`value.norm()` | 坐标的欧几里得长度 |
| `bool(value)` | 仅当所有坐标均为零时为假 |
| `point.unit`、`point.abs_unit` | 单位方向，后者分量非负；原点保持为零 |
| `point.distance_to(other, unit="px")` | 到某点或到规范化矩形中最近位置的距离；单位为`px`（PDF点）、`in`、`cm`、`mm` |
| `matrix.is_rectilinear` | 坐标轴是否保持对齐，零容差为`1e-5` |

坐标必须是有限数。坐标序列长度不正确或值非有限时抛出`PdfError`；参数类型无效时抛出`TypeError`。输入是有界序列，而不是任意可迭代对象。`transform`、`invert`和`pretranslate`等原地修改的方法不存在：请改为赋值运算符的结果。加法和标量乘法要求几何值位于左侧。

这些是值运算，因此把变换后的点传给绘制会改变其位置，而不会改变文本的方向或大小。`get_pixmap(matrix=)`只接受缩放矩阵；不支持旋转和错切。

## 矩形与四边形 { #rectangles }

`Rect()`是零矩形。`Rect(x0, y0, x1, y1)`、`Rect((x0, y0, x1, y1))`、`Rect(top_left, bottom_right)`、`Rect(top_left, x1, y1)`和`Rect(x0, y0, bottom_right)`接受坐标或角点。`IRect`接受相同的参数，并将小数边向外取整：`x0`和`y0`向下，`x1`和`y1`向上，算术运算之后也是如此。`Quad(ul, ur, ll, lr)`接受四个点；`rect.quad`把矩形转换为四边形。所有返回矩形的页面API都返回`Rect`，所有接受矩形的API也接受普通的四项序列。

```python
from pylopdf import Matrix, Point, Rect

hits = page.search_for("Total")
area = hits[0] | hits[-1]                    # 第一个与最后一个命中的并集
inside = [hit for hit in hits if hit in page.rect]
box = area * Matrix(1, 0, 0, 1, 0, 20)        # 向下移动20点
quad = area.morph(area.tl, Matrix(30))       # 绕一个角旋转30度
```

| 运算 | 结果 |
|---|---|
| `rect + other`、`rect - other` | 与矩形、四项序列或标量逐分量运算 |
| `rect * scalar`、`rect / scalar` | 缩放每个坐标 |
| `rect * matrix`、`rect / matrix` | 变换后各角的外接矩形；无限矩形保持无限 |
| `rect \| point`、`rect \| other` | 同时包含某点或某矩形的最小矩形 |
| `rect & other` | 交集；不相交的矩形得到无效的空结果 |
| `point in rect` | `x0 <= x < x1`且`y0 <= y < y1` |
| `other in rect`、`quad in rect` | 有效矩形或四边形的外接矩形位于边界内，边界本身算作内部 |
| `rect.width`、`rect.height` | 永不为负 |
| `rect.is_empty`、`rect.is_valid`、`rect.is_infinite` | `x0 >= x1`或`y0 >= y1`；`x0 <= x1`且`y0 <= y1`；等于`INFINITE_RECT()` |
| `rect.intersects(other)` | 两个有限非空矩形是否有共同面积 |
| `rect.round()`、`rect.irect` | 覆盖该矩形的`IRect`，每条边容许0.001的误差 |
| `rect.torect(other)` | 把一个矩形映射到另一个矩形的矩阵 |
| `rect.morph(point, matrix)`、`quad.morph(point, matrix)` | 围绕固定点变换得到的`Quad` |
| `rect.normalized()` | 边已排序的矩形，取代pymupdf原地修改的`normalize()` |
| `abs(rect)`、`rect.get_area(unit="px")` | 面积；空矩形或无限矩形的`abs()`为0 |
| `quad * matrix`、`quad / matrix`、`quad + other` | 变换或平移每个角 |
| `point in quad`、`rect in quad`、`other_quad in quad` | 包含判断，含边界；对矩形或四边形，每个角都必须在内部 |
| `quad.rect`、`quad.width`、`quad.height` | 外接矩形，以及每组对边中较长的边 |
| `quad.is_rectangular`、`quad.is_convex`、`quad.is_empty` | 在`rect.quad`的转向方向上直角误差在1e-5以内；轮廓为凸；有一组边短于1e-5 |

pymupdf原地修改的`include_point`、`include_rect`、`intersect`、`normalize`和`transform`不存在；请改为赋值`r | p`、`r & other`、`r.normalized()`或`r * m`。与pymupdf相同，`IRect`没有`abs()`且总为真，而`Rect`仅在四个坐标均为零时为假，`Quad`在为空时为假。pymupdf只用两个角判断矩形是否在四边形内，而pylopdf检查全部四个角。
