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
| `is_repaired` | 打开时是否恢复了损坏的cross-reference数据（修正offset或重建table）；保存会规范化xref数据 |
| `objects` / `trailer` / `catalog` / `page_xref(pno)` | 对对象图的类型化低层访问，取代pymupdf的`xref_*`方法（[低层对象](#low-level-objects)） |
| `metadata` / `set_metadata(dict)` | 8个标准Info字段（支持UTF-16BE）；aggregate文本上限1 MiB，写入为原子操作 |
| `get_page_text(pno, option)` | `"text"` / `"words"` / `"blocks"` / `"dict"` |
| `get_text(pages=None)` | 单次batch提取所有页（`None`）或最多为页数与4,096中较大者的显式entry的plain text；每4,096个entry的chunk共享一个interpreter font cache |
| `to_markdown(pages=None, table_strategy="lines", max_size=64 MiB)` | 使用有界线性entry builder按页两pass转换Markdown；默认转换所有页，显式entry最多为页数与4,096中较大者，并有累计UTF-8输出上限（`None`取消），含标题、CJK、强调、列表、分栏、竖排顺序及表格控制 |
| `render_page(..., max_size=64 MiB)` / `render_pages(..., workers=, max_size=512 MiB)` / `render_page_svg(..., max_size=64 MiB)` | 有上限的PNG、在累计encoded output上限内渲染所有页或最多为页数与4,096中较大者的显式entry的保序并行PNG批次，或有上限的UTF-8 SVG（`None`取消） |
| `compress_images(dpi=150, quality=75)` | 按实际放置DPI对安全DCT/Flate raster XObject进行有损缩小和JPEG重压缩，并返回类型化byte/count统计 |
| `set_fallback_font(font, kind=, index=, font_language="ja", max_font_size=64 MiB)` | 区分Adobe Japan1／Korea1／GB1／CNS1的有界CJK后备font；可信font input可用`None`取消上限 |
| `select` / `delete_page(s)` / `insert_pdf` / `new_page` / `copy_page` | 页面管理；select/delete的entry上限为页数与4,096中较大者，`insert_pdf`接受源文档的完整范围并注册导入页面的表单字段，navigation清理与link导入共享TOC和named destination上限 |
| `get_toc()` / `set_toc(toc)` | 面向大型手册、可处理cycle且有上限的书签（页码从1开始；65,536个entry/node、131,072条edge、64层、16 MiB文本；named destination与`get_links()`相同） |
| `get_page_labels()` / `set_page_labels(labels)` | 页码标签范围；固定上限为4,096个entry/node、32层、1 MiB标签文本 |
| `get_form_fields()` / `set_form_field(name, value, font=, fontfile=, fontbuffer=, fontindex=, font_language=, max_font_size=64 MiB)` | 有界地列出与填写AcroForm，caller名称／值限制为1 MiB，并限制原生widget外观和font input；`font`可传入已加载的OpenType `Font` |
| `embfile_add(..., max_size=64 MiB) / embfile_names / embfile_get(name, max_size=64 MiB) / embfile_del` | 对输入与解码输出采用对称默认上限，并限制1 MiB caller文本、添加metadata及inline FileSpec clone形状；`max_size=None`可显式取消上限 |
| `get_pdfa_claim(max_size=1 MiB)` | 有上限地读取XMP PDF/A声明；`max_size=None`显式取消上限，且这不是验证 |
| `save(...)` / `tobytes(..., max_size=512 MiB)` | 完整写入同directory临时stream后原子替换file／有上限的PDF byte；`garbage=0`–`4`（与pymupdf相同的级别；3和4合并相同的对象和流）`deflate=` `use_objstms=`、`encryption=`（默认`PDF_ENCRYPT_NONE`，加密用`PDF_ENCRYPT_AES_256`；未指定`PDF_ENCRYPT_AES_256`时传入password会抛出异常）及127-byte上限的`user_pw=`／`owner_pw=`；`max_size=None`取消上限 |
| `close()` | 也可通过`with`调用 |

`compress_images()`会解释所有页面，找出每个间接raster object的最大放置尺寸，再原子地
编辑lopdf副本。`dpi=None`时不缩小，仅按quality重压缩。保守边界仅包含无mask或自定义
decode array的直接单filter 8-bit DeviceGray/DeviceRGB DCT/Flate stream。DCT decode
parameter不受支持；Flate可无predictor或使用与字典一致的PNG predictor。已解释但
不支持的间接图像以及不会变小的编码会被跳过；inline图像不计入统计。单次调用解释
超过65,536个间接raster placement时会被拒绝。用相同设置重复调用是幂等的。

## Page { #page }

| 成员 | 用途 |
|---|---|
| `number` / `parent` / `get_label()` | 标识与显示标签 |
| `xref` / `obj` | 页面字典的对象编号 / 实时低层句柄；继承的属性仍位于其祖先节点 |
| `get_text(option)` / `search_for(text, max_hits=4096)` | 提取与有界的不区分大小写搜索；搜索词上限为4,096 UTF-8 byte，可信结果集可用`None`取消 |
| `get_text_ocr(dpi=, engine=, tile_size=, overlap=, min_confidence=, rotation=, clip=)` | 不编辑页面，通过本地PP-OCRv6返回带位置的单词；`rotation`顺时针校正输入，`clip`使用显示坐标 |
| `apply_ocr(..., rotation=, clip=, skip_existing=True)` | 插入保留方向的不可见可搜索层；默认跳过所选区域的已有文本 |
| `find_tables(clip=None, strategy=None)` | 完整或保守补全的稀疏矢量边框与合并单元格；`"text"`启用无边框检测，`clip`指定显示坐标区域 |
| `to_markdown(table_strategy="lines", max_size=64 MiB)` | 使用相同表格及UTF-8输出控制的单页Markdown |
| `get_images()` | 已绘制图像（含`bbox`，JPEG直通 / PNG）；超过4,096个placement、累计64,000,000像素或64 MiB payload时拒绝部分结果 |
| `get_drawings()` | 页面中已解释的矢量fill/stroke路径；显示坐标中的line/cubic几何与规范化绘制属性；超过8,192条路径、131,072条命令或页面累计131,072个虚线值时拒绝返回部分结果 |
| `get_pixmap(scale=, dpi=, background=, clip=)` / `render(max_size=64 MiB)` / `render_svg(max_size=64 MiB)` | 有上限的PNG / UTF-8 SVG渲染；`clip`使用显示坐标 |
| `rotation` / `set_rotation(deg)` | 显示旋转 |
| `mediabox` / `cropbox` / `rect` / `set_mediabox` / `set_cropbox` | 页面框 |
| `insert_image(rect, filename= / stream= / pixmap=, rotate=, keep_proportion=, overlay=, max_size=64 MiB, max_pixels=64,000,000)` | 绘制有上限的JPEG/PNG或复用已有边界的RGBA `Pixmap`；可信encoded input／PNG像素可用`None`取消上限；`rotate`按90度顺时针旋转；再次放置相同输入时共享同一图像XObject |
| `show_pdf_page(rect, docsrc, pno=, keep_proportion=, overlay=)` | 以矢量叠加PDF页面；`docsrc`可为同一文档；再次放置未编辑`docsrc`的同一页面时共享其Form XObject |
| `insert_text(point, text, fontsize=, lineheight=, font=, fontname=, fontfile=, fontbuffer=, fontindex=, font_language=, color=, overlay=, max_font_size=64 MiB, max_text_size=1 MiB)` | 有界UTF-8与4,096行Standard-14或shape subset文本；与pymupdf相同，字符串用`str.splitlines()`分行，列表每项为一行，行距为`fontsize`的`lineheight`倍，默认为字体的`ascender`减去`descender`，超出页面底边的行不绘制，并返回绘制的行数；`font`可传入已加载的`Font`；自动选择已安装script／locale provider，歧义汉字可用`font_language`指定；对应可信input可用`None`取消上限 |
| `insert_textbox(rect, text, fontsize=, font=, fontname=, fontfile=, fontbuffer=, fontindex=, font_language=, color=, align=, expandtabs=, lineheight=, overlay=, max_font_size=64 MiB, max_text_size=1 MiB)` | 预检文本与tab展开，并使用Core 14、OpenType、已加载的`Font`或已安装provider宽度进行UAX #14换行；行的位置与pymupdf相同；物理行与换行后layout上限为4,096行，溢出时不绘制 |
| `new_shape()` | 一次绘制路径和文字的pymupdf `Shape`；参见[图形](#shapes) |
| `draw_line` / `draw_polyline` / `draw_rect` / `draw_quad` / `draw_circle` / `draw_oval` / `draw_sector` / `draw_curve` / `draw_bezier` / `draw_squiggle` / `draw_zigzag`(geometry, *, color=(0,), fill=None, width=1, dashes=None, lineCap=0, lineJoin=0, closePath=, overlay=True, stroke_opacity=1, fill_opacity=1) | 绘制一个已完成的图形，并返回与pymupdf相同的终点`Point`；`fill`与`closePath`仅在pymupdf提供的方法上可用，`draw_rect`有`radius=`，`draw_sector`有`fullSector=`，波浪线有`breadth`（与pymupdf相同，也可在点之后按位置传入） |
| `insert_ocr_text_layer(words, rotation=)` | 保留方向的OCR不可见文本层；每次call固定上限为4,096词和1 MiB UTF-8文本 |
| `replace_text(search, replacement, default_char=, max_size=64 MiB)` | 带输入输出上限和copy-on-write的原子简单编码替换 |
| `annots()` / `get_links()` / `add_highlight_annot(...)` / `add_link_annot(rect, uri)` | 有界批注／link读取与创建；named destination通过最多65,536个entry/node、131,072条edge、32层和16 MiB key的name tree解析，tree只建立一次index并在下次编辑前供后续读取复用；渲染时会为带有效`QuadPoints`且在上限内的RGB Highlight、Underline、StrikeOut和Squiggly保守补全缺失appearance，同时不修改原PDF |

`get_drawings()`返回`DrawingInfo`字典，其中包含`type="f"` / `"s"` / `"fs"`、
自包含的line/cubic `items`、`rect`、RGB/opacity、fill rule、width、cap、join和
dashes。对于pattern paint，会保留几何形状，而颜色和opacity为`None`。它不返回
clip path、clip应用后的可见性判断、group/soft-mask结构、optional-content layer名称、
text、image或annotation，但仍会应用optional-content的可见性。结果超过8,192 paths
或131,072 commands时会拒绝，而不是静默截断。

使用嵌入字体的`insert_text`需要一个包含所有所需字形的字体。未传入source且安装
`pylopdf[ar]`、`[he]`、`[hi]`、`[jp]`、`[ko]`、`[th]`、`[zh-cn]`和
`[zh-tw]`提供Noto Sans／Serif生成font。Kana、Hangul、Bopomofo、Arabic、
Hebrew、Devanagari和Thai会自动选择已安装provider。纯汉字为兼容旧行为仍优先JP；
地区字形很重要时请指定`font_language="zh-CN"`、`"zh-TW"`、`"ko"`或`"ja"`。
整段只选择一个font，并非逐glyph fallback。
每一行会被shape，但不提供双向段落layout或换行。RTL可正确渲染。纯RTL行，以及只有
一个被RTL文字包围的拉丁或数字token的行，会按逻辑Unicode顺序提取。行首或行尾token、
多个token以及一般混合方向paragraph仍保留producer的visual order。

`insert_textbox`并非富文本引擎；它保留显式换行、展开制表符、按Unicode机会换行CJK，
并对过长单词执行grapheme安全的紧急换行。对齐常量为`TEXT_ALIGN_LEFT`、
`TEXT_ALIGN_CENTER`、`TEXT_ALIGN_RIGHT`和`TEXT_ALIGN_JUSTIFY`。返回负值表示
垂直空间不足，此时不会添加页面内容或字体resource。

两个方法都与pymupdf在相同位置排列行。`insert_text`把每一行放在上一行下方
`fontsize`的`lineheight`倍处；省略`lineheight`时，倍数为字体的`ascender`减去
`descender`，该差不大于1时为1.2，例如10磅的Helvetica行距为13.74磅。与pymupdf
相同，该差按MuPDF报告的单精度度量比较，因此ascender为0.8、descender为-0.2的字体
略大于1 em，行距保持1 em。
`insert_textbox`使用相同的默认值，把第一条基线放在`rect`顶边下方一个ascender处，
之后各行依次下移ascender的`lineheight`倍；判断文字能否放下以及返回剩余高度时，
每行计为`fontsize`的`lineheight`倍，另加一个descender。

`insert_text`也与pymupdf相同地决定绘制哪些行。字符串用`str.splitlines()`分行，
因此末尾的换行不会增加行，`\v`、`\f`、`\x1c`至`\x1e`、`\x85`、`\u2028`与
`\u2029`也与`\n`、`\r`、`\r\n`一样分隔行；列表或元组每项为一行，按原样使用。
第一行总会绘制，之后的行仅在其基线不低于`page.rect`底边时绘制。返回值为绘制的
行数；像`""`或`"\n"`这样没有可绘制内容的文本不添加任何内容并返回0。pymupdf在未旋转
的坐标中测量到用户空间y = 0为止的空间，pylopdf则在显示坐标中测量到`page.rect`的
底边；两者在未旋转且可见区域在y = 0处结束的页面上一致。超出页面底边的行也会检查
字体能否绘制。

`Font`遵循pymupdf的`Font(fontname=None, fontfile=None, fontbuffer=None)`，并以
`insert_text(font=)`与`insert_textbox(font=)`传入；OpenType字体也可以传给
`set_form_field(font=)`。`Font("helv")`或
`Font("Times-Bold")`指定Standard 14字体，不嵌入即可绘制，并按Adobe AFM宽度测量；
pymupdf的其他内置字体不可用。`Font(fontfile=)`与`Font(fontbuffer=)`按原样加载
OpenType字体，可变字体使用其默认实例。默认实例不一定是Regular：可变字体Noto Sans CJK JP
的默认实例是Thin。传入`weight=`即可从可变字体的字重轴选择其他实例：
`Font(fontfile="NotoSansCJKjp-VF.otf", weight=700)`按Bold实例的前进宽度测量，并以
`NotoSansCJKjp-Bold`嵌入；`weight=650`同样可用，因此对这类字体，`weight=400`与省略
`weight`的结果不同。超出字重轴范围的字重会抛出`PdfError`，静态字体不具备的字重也一样。
传给`insert_text`、`insert_textbox`与`set_form_field`的`fontfile`和`fontbuffer`以默认
实例绘制；要以所选实例绘制，请将该`Font`传给`font=`。`Font(language="zh-CN")`或
`Font(family="Noto Sans SC")`选择已安装字体extra的字体（`is_serif=True`为衬线体）。
`font.text_length(text, fontsize)`使用与绘制相同的shaping，含字距调整与连字，因此
可以按实际绘制的字体计算换行、对齐与分页。`ascender`与`descender`是pymupdf
报告的相对em比例，`insert_text`与`insert_textbox`用它们排列行：OpenType字体在设置了
`USE_TYPO_METRICS`时取OS/2的typographic值，否则取`hhea`的值，与FreeType为MuPDF
读取的值相同；Standard 14字体取MuPDF绘制它时所用URW字体的外接矩形，例如
Helvetica为1.075与-0.299。`has_glyph(chr)`返回码位的字形编号（不存在时为0）。CJK的extra每种样式只安装一个静态字体，因此与字体不符的
`is_bold`、`is_italic`或OS/2 `weight`会抛出`PdfError`，Noto Sans SC Bold等其他字重
请从文件加载。而`pylopdf[ar]`、`[he]`、`[hi]`、`[th]`安装的是可变字体：
`Font(language="he", is_bold=True)`无需其他文件即可从字体的字重轴选择Bold实例，按该
实例的前进宽度测量，并以PostScript名称`NotoSansHebrew-Bold`嵌入。字重范围为100到
900，`pylopdf[ar]`的衬线字体Noto Naskh Arabic为400到700；轴外的字重或斜体会抛出
`PdfError`。已安装的可变字体（包括`insert_text`、`insert_textbox`与`set_form_field`
自动选择时）以Regular字重绘制，因为Noto Sans Hebrew的默认实例是Thin。pylopdf不保存字体注册表，也不搜索操作系统的字体目录；不会用描边或叠印模仿
字重，只有显式传入`allow_regular_fallback=True`时才使用extra的字体并发出
`PylopdfWarning`。即使pymupdf会忽略，与`fontname`或字体文件一起传入`is_bold`、
`is_italic`或`is_serif`也会抛出异常。每次绘制调用仍会嵌入该调用所用字形的子集；通过[图形](#shapes)绘制，则每次提交
只嵌入一次字体。

### 图形 { #shapes }

`page.new_shape()`返回pymupdf的`Shape`。`draw_line`、`draw_polyline`、
`draw_rect`（`radius=`使角变圆）、`draw_quad`、`draw_circle`、`draw_oval`、
`draw_sector`、`draw_curve`、`draw_bezier`、`draw_squiggle`和`draw_zigzag`在
显示坐标中添加路径，并返回与pymupdf相同的终点；从上一段终点开始的线段会延续同一
子路径。`finish(*, width=1, color=(0,), fill=None, lineCap=0, lineJoin=0,
dashes=None, even_odd=False, closePath=True, fill_opacity=1, stroke_opacity=1)`
以描边和填充完成上一次`finish`之后绘制的路径；颜色为0到1的灰度、RGB或CMYK，
`dashes`是`"[3 4] 0"`这样的PDF虚线模式。`insert_text`与`insert_textbox`接受与
`Page.insert_text`、`Page.insert_textbox`相同的字体选项（包括`font=`与
`lineheight=`），并在相同位置排列行。`insert_text`也与`Page.insert_text`相同地分行并
省略超出页面底边的行，返回添加的行数；`insert_textbox`返回剩余高度，放不下时返回
负值且不添加任何内容。`commit(overlay=True)`将图形绘制到页面：与pymupdf相同，先按顺序绘制
已完成的路径，再在其上绘制全部文字。

```python
with page.new_shape() as shape:
    shape.draw_rect((50, 50, 545, 792))
    shape.finish(color=(0.72, 0.78, 0.82), fill=(0.95, 0.96, 0.97), width=0.5)
    shape.draw_circle((80, 140), 7)
    shape.finish(color=(1, 1, 1), fill=(0.83, 0.26, 0.05), width=1.2)
    shape.insert_text((56, 80), "检查报告", font=bold, fontsize=18)
    shape.insert_textbox((56, 160, 540, 400), body, font=regular, fontsize=10.5)
```

每次提交放置一个Form XObject。路径和嵌入字体的文字一起生成，因此无论使用多少行，
每次提交每种字体只嵌入一次；用一个图形绘制一页的文字，则每页每种字体只嵌入一次。
`with`块正常结束时提交，发生异常时不绘制任何内容；含有未提交绘制的图形被丢弃时
会发出`ResourceWarning`。在pymupdf静默丢弃或以黑色绘制的情况下，pylopdf会抛出
`PdfError`：`commit()`拒绝未经`finish()`完成的路径，`finish()`拒绝不绘制任何内容
的样式（无填充，且`color=None`或`width=0`）。`morph=`与`oc=`不可用。提交前一个
图形最多容纳1,048,576个路径点，超出时抛出`LimitError`（code为`path_point_count`）。
`Page.draw_*`以关键字参数接受相同选项，各绘制一个已完成的图形。

`set_form_field`会为文本、组合框／列表选择、复选框和单选按钮生成外观。WinAnsi文本
使用Helvetica自动缩小；传入OpenType `fontfile`或`fontbuffer`即可对子集嵌入Unicode，
也可以将已加载的OpenType `Font`（例如可变字体的Bold实例）传给`font`。使用`Font`时，按
`text_length`测量的前进宽度缩小与对齐文本，并将其置于comb字段各位置的中央；`font`不能与
其他字体参数同时使用，Standard 14的`Font`会抛出`PdfError`。
已安装script／locale provider按`insert_text`的同一规则选择，歧义汉字可用
`font_language`指定。已有且非空的按钮外观
会保留，仅为缺失状态生成矢量标记。其他WinAnsi字段缺失的外观也会同时补齐；仅当
所有可填写widget都自包含时才清除`NeedAppearances`。comb文本字段遵循继承的
`MaxLen`与对齐方式，将每个Unicode grapheme置于相应位置中央，并在不修改文档的
情况下拒绝超长值。富文本、pushbutton动作和签名不在生成范围内。

`Table.confidence`是0–1的确定性排序heuristic，并非经过校准的概率。
`Table.diagnostics`是`TableDiagnostics` tuple；对无边框文本表格，它包含以em归一化的
对齐误差、最小列间距和行间距变化。完整矢量网格得分为1.0，补全稀疏边框的
hybrid grid为0.95；两者的文本专用指标均为`None`。
`TableFinder.strategy`和`TableFinder.clip`保留本次使用的设置。

## 低层对象 { #low-level-objects }

`doc.objects`将对象编号映射到文档的间接对象；`doc.trailer`、
`doc.catalog`和`page.obj`是字典句柄。这一类型化模型取代pymupdf基于字符串的
`xref_*`方法：对象是Python值，而不是PDF语法。

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

引用在读取时解析，指向不存在对象的引用读作`None`。写入立即生效并丢弃渲染与
提取缓存；改变页面顺序的写入还会使现有`Page`视图失效。列表和映射写成直接对象，
存入容器的间接句柄成为引用，而赋值给`doc.objects[xref]`或传给`add()`的句柄会被
复制。对象被删除，或`save(garbage=2)`及以上重新编号后，句柄会引发
`StaleObjectError`。保持原始编辑在语义上有效是调用方的责任。

## 模块级 { #module-level }

| 名称 | 用途 |
|---|---|
| `peek_metadata(filename=None, stream=None, password=None, *, max_file_size=None)` | 可选输入大小限制及127-byte password上限的快速元数据与页数读取；`repaired`报告cross-reference恢复 |
| `Permissions` | 加密权限标志（IntFlag），成员即pymupdf的`PDF_PERM_*`常量 |
| `EncryptionMethod` | `save(encryption=)`的加密方式（IntEnum），成员即pymupdf的`PDF_ENCRYPT_*`常量 |
| `LinkKind` / `TextAlign` | 成员即pymupdf的`LINK_*`与`TEXT_ALIGN_*`常量的IntEnum |
| `Rect` | 带`width` / `height`的矩形NamedTuple |
| `TextPage` / `TextBlock` / `TextLine` / `TextSpan` | `get_text("dict")`的TypedDict层级 |
| `ImageInfo` / `ImageCompressionResult` / `AnnotationInfo` / `LinkInfo` / `FormFieldInfo` / `DrawingInfo` | 页面、文档操作、表单与矢量绘制结果的TypedDict契约 |
| `DrawingItem` | 表示line/cubic绘制命令的类型别名 |
| `PageLabelInfo` / `PageLabelSpec` | 规范化页码标签输出／setter输入契约 |
| `DocumentMetadata` / `MetadataUpdate` / `MetadataProbe` | 元数据输出／部分更新／快速探测契约 |
| `DocumentLimits` / `DocumentComplexity` | 包含PDF snapshot用`max_interpretation_size`和带位置layout用`max_text_glyphs`的不受信任输入不可变预算／轻量结构TypedDict |
| `OcrEngine` / `OcrWord` | 可复用的纯Rust PP-OCR引擎与带位置结果契约 |
| `Font(fontname=None, fontfile=None, fontbuffer=None, *, language=None, is_bold=False, is_italic=False, is_serif=False, weight=None, fontindex=0, family=None, allow_regular_fallback=False, max_font_size=64 MiB)` | 带`text_length`、`ascender`／`descender`、`has_glyph`、`name`、`family`、`weight`、`is_bold`、`is_italic`、`is_serif`与`units_per_em`的可复用Standard 14或OpenType字体；`weight=`也可选择可变字体文件的实例 |
| `Shape(page)` | 一次绘制路径和文字的pymupdf `Shape`；也可用`page.new_shape()`创建 |
| `Name` / `String` / `Array` / `Dictionary` / `Stream` / `Objects` | 低层PDF值与实时对象句柄（[低层对象](#low-level-objects)） |
| `OcrRotation` / `WordEntry` / `BlockEntry` / `FormFieldType` | 可在runtime导入的OCR旋转、tuple和literal类型别名 |
| `TableFinder` / `Table` / `TableDiagnostics` | 自包含的表格几何、单元格文本（合并延续位置为`None`）、策略与置信依据；`Table.to_markdown(max_size=64 MiB)`预检转义后的UTF-8输出 |
| `PdfError` / `LimitError` / `PasswordError` / `OcrError` / `DocumentClosedError` / `EncryptedDocumentError` / `StalePageError` / `StaleObjectError` | 异常层级；资源拒绝提供稳定`.code`（基类兼容ValueError） |
| `Pixmap` | 不可变RGBA8像素：`samples` / `width` / `height` / `stride` / `n` / `tobytes(max_size=64 MiB)` / 流式写入且失败时保留现有file的PNG专用`save(path)`；所有wheel还支持只读、零复制的`memoryview()` |
| `PylopdfWarning` | 可恢复的解释警告（xref修复、字体解析、图像解码） |

`TypedDict`契约仅影响静态类型；运行时值仍是普通的pymupdf风格字典。
`LinkInfo`要求`kind`和`from`，而各类目标专用键为可选。
`PageLabelSpec`要求`startpage`；`style`、`prefix`和`firstpagenum`的运行时默认值不变。
