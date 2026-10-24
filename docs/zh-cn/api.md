---
title: API参考
description: pylopdf的Document、Page、Canvas、Font、Pixmap、几何类型、底层对象与异常一览。
---

# API参考

本页按任务列出公共API。每个函数和类也都带有docstring，运行`help(pylopdf.Document)`即可查看详细说明。

全文通用的约定：

- **页码从0开始**，书签中也是如此。负数索引从末尾倒数。
- **坐标**以点（1/72英寸）为单位，原点位于页面显示时的左上角，并已应用页面旋转和可见区域（CropBox）。提取、搜索、绘制和渲染共用这套坐标。
- **矩形**以[`Rect`](#rectangles)返回。凡是接受矩形或点的地方，也可以直接传入普通元组。
- **大小上限。**可能读取或生成大量数据的方法带有`max_size`（或类似）参数，并设有安全的默认值。对于可信的输入，传入`None`即可取消上限。超出上限时会抛出[`LimitError`](security.md#error-codes)。
- **pymupdf的名称保持pymupdf的含义。**pylopdf与之不同的地方，[迁移指南](migration.md)中都有说明。

## Document { #document }

```python
pylopdf.open(source, /, *, password=None, limits=None)
pylopdf.Document(*, limits=None)
```

`open`从路径（`str`或`os.PathLike`）或文件内容（`bytes`、`bytearray`或`memoryview`）读取PDF；文件对象请先读出内容，例如`pylopdf.open(file.read())`。`Document()`会创建一个空PDF。`limits`接受用于不可信输入的[`DocumentLimits`](security.md#untrusted-pdfs)策略。请将文档用作上下文管理器，或调用`close()`。

### 打开、查看与保存 { #document-io }

| 成员 | 用途 |
|---|---|
| `pages` | 文档的页面；参见[页面](#document-pages) |
| `metadata` | 现有Info条目的实时映射，键为`title`、`author`、`subject`、`keywords`、`creator`、`producer`、`created`、`modified`和`trapped`，日期为PDF日期字符串。给键赋值或调用`update()`即写入，`del`删除条目，赋值一个映射会替换全部条目，赋值`{}`或调用`clear()`则全部删除 |
| `version`、`encryption_method` | 文件头中的PDF版本（例如`"1.7"`），以及打开文档时使用的加密方式（例如`"Standard V5 R6 256-bit AES"`）或`None` |
| `needs_password`、`unlock(password)` | 文档是否处于锁定状态，以及解锁文档：`unlock`返回`Access.USER`或`Access.OWNER`，密码错误时抛出`PasswordError` |
| `is_repaired` | 打开时是否修复了损坏的交叉引用数据 |
| `permissions` | 文档允许的操作，以`Permissions`标志表示：锁定期间为空，未加密的文档或使用所有者密码解锁后为`Permissions.ALL`，否则为加密所授予的标志。用`Permissions.PRINT in doc.permissions`检查某个标志 |
| `is_dirty` | 文档自打开以来是否被编辑过。与pymupdf相同，新建的空文档一开始就算作已编辑，保存也不会重置该状态 |
| `is_closed`、`path` | 文档是否已关闭，以及打开时所用文件的`pathlib.Path`；从bytes打开的文档和空文档为`None` |
| `limits`、`complexity` | 打开文档时使用的策略，以及用于判断是否处理该文档的轻量结构信息（页数、对象数、流数） |
| `save(path, /, *, compress=True, prune=True, dedupe=False, renumber=False, object_streams=False, encryption=None)` | 写出PDF。只有在完整写入成功后才会替换文件 |
| `to_bytes(..., max_size=512 MiB)` | 以`bytes`形式返回相同的输出 |
| `close()` | 释放文档；使用`with`时会自动调用 |

默认情况下，保存时会压缩经Flate压缩后变小的流，并删除没有任何引用的对象；`compress=False`和`prune=False`会让它们保持原样。`dedupe=True`合并重复的对象和流，`renumber=True`为其余对象连续编号，`object_streams=True`将对象打包进压缩的对象流。这些选项改变的是文档本身，而不仅是输出。要加密，请传入`encryption=Encryption(user_password=..., owner_password=..., permissions=...)`，它会写出AES-256加密；所有者密码默认与用户密码相同，内存中的文档保持未加密状态。

```python
doc.save("out.pdf", dedupe=True, object_streams=True)
doc.save(
    "locked.pdf",
    encryption=pylopdf.Encryption(
        user_password="secret",
        owner_password="owner-secret",
        permissions=pylopdf.Permissions.PRINT,
    ),
)
```

### 页面 { #document-pages }

`doc.pages`是由[`Page`](#page)视图组成的序列：支持`len(doc.pages)`、`doc.pages[i]`（负数索引从末尾倒数）、`doc.pages[2:5]`这样返回列表的切片，以及迭代。

| 成员 | 用途 |
|---|---|
| `doc.pages.new(size=(595, 842), *, index=None)` | 添加一个空白页，默认A4；未指定`index`时追加到末尾 |
| `doc.pages.insert(index, pages)`、`doc.pages.extend(pages)` | 插入或追加单个页面或一组页面的副本，连同其链接和表单字段；页面可以来自本文档或另一个文档 |
| `del doc.pages[i]`、`del doc.pages[a:b]` | 删除页面 |
| `doc.pages.select(indexes)` | 按列出的顺序保留列出的页面；重复某个索引即复制该页 |
| `doc.pages.move(index, to)` | 移动某页，使其最终位于索引`to` |
| `doc.pages.labeled(label)` | 标签为`label`的页面的索引 |
| `outline` | 以`OutlineItem`句柄树表示的书签（大纲）；参见[书签与页码标签](#outline) |
| `page_labels` | 以`PageLabelRange`记录表示的页码标签范围，例如前言部分使用罗马数字 |

```python
merged = pylopdf.Document()
for path in ["a.pdf", "b.pdf"]:
    merged.pages.extend(pylopdf.open(path).pages)
del merged.pages[0]                         # 删除第一页
merged.pages.insert(0, merged.pages[-1])    # 将最后一页复制到最前面
merged.pages.select(range(len(merged.pages) - 1, -1, -1))  # 颠倒页面顺序
```

`insert`和`extend`像`list.insert`一样接受索引位置，因此超出末尾的索引会追加到末尾。一次调用中的页面都来自同一个文档，而且另一个文档的同一页在一次调用中最多出现一次。来自另一个文档的页面会连同其链接、批注和表单字段一起导入；已被使用的字段名会加上最小的未使用后缀`+N`。同一文档中某页的副本是一个新的页面对象，在任何一方被编辑之前，它与原页面共享内容流和资源。

添加、删除或重排页面后，之前获取的`Page`视图会失效：使用它会抛出`StalePageError`，在修改页面的同时迭代`doc.pages`会抛出`RuntimeError`。删除或选择页面时，也会删除仅指向被删页面的链接、表单字段和标签PDF（tagged PDF）结构；指向被删页面的书签条目会像pymupdf一样留在原处，但不再有目标。但这不会删除其内容的所有副本，因此不能作为安全的涂黑（redaction）手段。

### 书签与页码标签 { #outline }

`doc.outline`以顶层`OutlineItem`句柄的序列表示书签（大纲）。每个条目的`children`包含树的其余部分，`doc.outline.walk()`按文档顺序遍历所有条目。条目在每次访问时读取文档，为其属性赋值会立即编辑文档。

| `OutlineItem`成员 | 用途 |
|---|---|
| `title` | 查看器显示的标题；可赋值 |
| `destination` | 条目指向的位置，即下表中的目标或`None`；可赋值 |
| `page` | `Destination`的从0开始的页码，否则为`None` |
| `level`、`children` | 深度（顶层条目为1）和子条目 |
| `color`、`bold`、`italic` | 标题的显示方式：不透明的`Color`或`None`，以及两个标志；可赋值 |
| `is_open` | 打开文档时是否展开子条目；可在有子条目的条目上赋值 |
| `xref`、`obj` | 条目的对象编号及其在[底层对象](#low-level-objects)中的字典 |
| `to_entry()` | 不含子条目的该条目，作为`OutlineEntry`返回 |

赋值一个`OutlineEntry(level, title, destination=None, *, color=None, bold=False, italic=False, is_open=False)`记录列表会替换整个书签，`del doc.outline`会删除书签。层级从1开始，每个条目比前一个最多深一级；一个条目的子条目是紧随其后、层级深一级的条目。`doc.outline.entries()`以相同形式返回当前书签，因此可以作为列表编辑后再赋值回去。

```python
from pylopdf import Destination, OutlineEntry

doc.outline = [
    OutlineEntry(1, "Chapter 1", Destination(0), bold=True, is_open=True),
    OutlineEntry(2, "Section 1.1", Destination(1, point=(72, 100))),
    OutlineEntry(1, "Project site", "https://example.com"),
]
for item in doc.outline.walk():
    if item.page == 1:
        item.title = item.title.upper()
```

| 目标 | 指向 |
|---|---|
| `Destination(page, point=None, zoom=None)` | 本文档中从0开始的页面。`point`是视图左上角在显示坐标中的位置，`zoom`是缩放倍率；两者都没有时，查看器使页面适合窗口 |
| `str` | URI |
| `RemoteDestination(file, page=0, name=None)` | 另一个PDF文件的页面或命名目标 |
| `Launch(file)` | 用查看器关联的应用程序打开的文件 |
| `NamedAction(name)` | 查看器操作，例如`"NextPage"`或`"FirstPage"` |

命名目标读取为它所指的`Destination`，页面已不存在的目标读取为`None`。读写的上限为65,536个条目、64层，以及16 MiB的标题和目标文本（`LimitError`代码为`toc_entry_count`、`toc_text_size`和`toc_input_size`）。

修改标题或目标时，会先检查修改后整个大纲的文本总量，再写入更改。被拒绝的修改不会改变大纲。文件目标支持Unicode文件名；坐标和缩放比例必须在PDF有限实数的表示范围内。批量修改标题或目标时，可以修改`entries()`返回的列表后一次性赋值，这样只需检查一次文本总量。

`doc.page_labels`是`PageLabelRange(start, style=DECIMAL, prefix="", first=1)`记录的列表。每个范围为从从0开始的`start`到下一个范围之前的页面加上标签：`prefix`后接一个从`first`开始计数、按`PageLabelStyle`（`DECIMAL`、`UPPER_ROMAN`、`LOWER_ROMAN`、`UPPER_LETTERS`、`LOWER_LETTERS`，或只用前缀的`None`）书写的编号。赋值一个列表会按页面顺序替换这些范围，第一个范围必须从第0页开始；赋值`[]`或`del doc.page_labels`会删除它们。`page.label`是单个页面的标签，文档没有标签时为`""`；`doc.pages.labeled(label)`按标签查找页面。

```python
from pylopdf import PageLabelRange, PageLabelStyle

doc.page_labels = [PageLabelRange(0, PageLabelStyle.LOWER_ROMAN), PageLabelRange(4, prefix="A-")]
print([page.label for page in doc.pages])  # ['i', 'ii', 'iii', 'iv', 'A-1', 'A-2']
```

### 文本与Markdown { #document-text }

| 成员 | 用途 |
|---|---|
| `text(pages=None)` | 将所有页面或列出页面的纯文本合并为一个字符串 |
| `markdown(pages=None, *, table_strategy="lines", max_size=64 MiB)` | 生成包含标题、列表、强调、多栏顺序、CJK竖排和表格的Markdown |

`table_strategy="lines"`转换以框线绘制的表格，`"text"`还会转换对齐明确的无边框表格，`None`则关闭表格转换。

同一行的词间空格遵循`text()`的规则，即使字体或样式发生变化也不例外。空格保留在强调标记之外，组合字符与其基础字符保持相连。

### 渲染与图像 { #document-rendering }

| 成员 | 用途 |
|---|---|
| `render(pages=None, *, dpi=None, matrix=None, alpha=False, background=None, annotations=True, colorspace="rgb", format="png", quality=95, workers=None, max_size=512 MiB)` | 并行渲染所有页面或列出的页面，按顺序返回PNG或JPEG bytes |
| `images` | 按对象编号排列的图像对象；参见[图像](#images) |
| `rewrite_images(*, dpi_threshold=None, dpi_target=0, quality=0, ...)` | 对图像降采样并重新压缩，以减小文件 |
| `fallback_fonts` | 按语言列出用于显示未嵌入字体的中文、日语或韩语文本的字体；CJK extra会自动完成设置 |

`rewrite_images`沿用pymupdf的关键字参数：在绘制位置上每英寸点数超过`dpi_threshold`的图像会重新采样到`dpi_target`，`quality`（1–100）将其重新编码为JPEG。它只修改不带蒙版的普通8位灰度或RGB图像，新编码不会更小时保留原图，并返回统计信息。

`fallback_fonts`将Adobe-Japan1、Korea1、GB1和CNS1字符集合的语言`"ja"`、`"ko"`、`"zh-CN"`和`"zh-TW"`映射到由OpenType `Font`组成的`FallbackFont(sans=None, serif=None)`记录。名称表明其为衬线字体的未嵌入字体（例如明朝体、宋体、明体或Batang）用`serif`渲染，其余用`sans`渲染；其中一个为`None`时，另一个同时负责两者。在编辑之前，该映射列出已安装的字体extra（`pylopdf[jp]`、`[ko]`、`[zh-cn]`和`[zh-tw]`）。为某个语言赋值或删除它只改变该语言，赋值一个映射会替换所有语言，`del doc.fallback_fonts`则恢复为已安装的extra。字体是共享而非复制的，因此一个`Font`可以用于多个文档。

```python
from pylopdf import FallbackFont, Font

doc.fallback_fonts["ja"] = FallbackFont(Font(fontfile="NotoSansJP-Regular.otf"), Font(fontfile="NotoSerifJP-Regular.otf"))
doc.fallback_fonts = {}           # no fallback: such text is not drawn
del doc.fallback_fonts            # back to the installed font extras
```

### 表单、附件与其他数据 { #document-data }

| 成员 | 用途 |
|---|---|
| `form` | 以从完整名称到`Field`句柄的映射给出表单字段；参见[表单](#forms) |
| `attachments` | 以映射形式访问嵌入文件；参见[附件](#attachments) |
| `pdfa_claim` | 文件声明的PDF/A部分和一致性级别，为`PdfaClaim(part=2, conformance="B")`记录，或为`None`。这只读取声明，并不进行验证 |
| `xmp` | 以文本形式表示的XMP元数据包，或为`None`。赋值字符串即写入，`del doc.xmp`将其删除。读写均以1 MiB为上限；更大的数据包可用`doc.catalog["Metadata"].read(max_size=None)`读取。`metadata`不会修改它 |
| `language` | 文档的语言，为完整的BCP 47标签，例如`zh-TW`或`sr-Latn-RS`，或为`None`。赋值标签即写入，`del doc.language`将其删除；格式不正确的标签会抛出异常 |
| `page_mode`、`page_layout` | 阅读器打开文档的方式，为`PageMode`和`PageLayout`成员，例如`PageMode.USE_OUTLINES`或`PageLayout.TWO_COLUMN_LEFT`；未设置时为`USE_NONE`和`SINGLE_PAGE`。可赋值成员或其准确名称 |
| `mark_info` | 以`MarkInfo(marked=, user_properties=, suspects=)`记录表示的标签PDF标志，不存在时为`None`。赋值时写入全部三个标志，`del`将其删除 |
| `objects`、`trailer`、`catalog` | 直接访问PDF对象；参见[底层对象](#low-level-objects) |

`language`保留完整的标签，而pymupdf的`language`最多只保留三个字母，因此在那里`zh-TW`和`zh-HK`都会被读成`zh-Hant`。写入XMP时，即使保存时压缩流也不会压缩数据包，因此在文件中搜索XMP的工具仍能找到它。

## Page { #page }

`Page`是文档中某一页的视图，用`doc.pages[i]`获取。两个视图指向同一文档的同一页，且其间没有添加、删除或重排页面时，二者相等。

### 文本与搜索 { #page-text }

| 成员 | 用途 |
|---|---|
| `text()` | 按阅读顺序返回页面的文本 |
| `words()` | 按阅读顺序返回单词，形式为`Word`记录 |
| `layout(*, chars=False)` | 以`Layout`返回区块、行和span；参见[带位置的文本](#positioned-text) |
| `search(text, *, max_hits=4096)` | 不区分大小写的搜索；返回`Rect`列表 |
| `tables(*, strategy="lines", clip=None)` | 以`Table`记录列表返回表格；参见[表格](#tables) |
| `markdown(*, table_strategy="lines")` | 本页的Markdown |
| `ocr(...)`、`apply_ocr(*, words=None, ...)` | OCR，以及由其结果或其他引擎的单词生成的不可见文本层；参见[离线OCR](ocr.md) |
| `content(*, text_runs=True, paths=True, images=True, clip=None, annotations=True)` | 页面绘制的所有文本段、路径和图像；参见[页面内容](#content) |
| `label` | 页码标签，例如`"iv"`；参见[书签与页码标签](#outline) |

`Word`具有`text`、`bbox`，以及表示其在页面布局中位置的`block`、`line`和`index`：分别是其所在区块的索引、其所在行在该区块中的索引，以及该单词在该行中的索引。

阅读顺序会依据行和分栏确定，包括从右到左的文字以及日文和中文竖排。注音（ruby）、夹注（warichu）以及横竖混排的文字不会被解析。

### 渲染 { #page-rendering }

| 成员 | 用途 |
|---|---|
| `pixmap(*, dpi=None, matrix=None, clip=None, alpha=False, background=None, annotations=True, colorspace="rgb")` | 渲染为[`Pixmap`](#pixmap) |
| `svg(*, annotations=True, max_size=64 MiB)` | 渲染为SVG字符串，文本绘制为路径 |
| `resources` | 页面资源所及的内容：`resources.images`列出其图像对象；参见[图像](#images) |

pixmap默认为72 dpi的白底RGB，`alpha=True`时为RGBA。`dpi`和`matrix`只能传入其一；任何可逆矩阵都能渲染，pixmap覆盖变换后的页面：`Matrix(90) * Matrix(2, 2)`将页面顺时针旋转90度并以144 dpi渲染，平移不会移动像素。`clip`只渲染页面的一部分，`annotations=False`不绘制注释和表单控件，`colorspace="gray"`渲染为灰度像素。

### 页面几何 { #page-geometry }

| 成员 | 用途 |
|---|---|
| `index`、`document` | 该页在文档中的索引，以及所属文档 |
| `rect` | 已应用旋转的可见页面区域 |
| `rotation` | 0、90、180或270；赋值90的倍数可将页面顺时针旋转 |
| `boxes` | 以PDF用户空间中的`Rect`表示的MediaBox、CropBox、BleedBox、TrimBox和ArtBox，均可赋值 |
| `user_to_display` | 将PDF用户空间映射到显示坐标的`Matrix` |
| `xref`、`obj` | 页面对象的编号和[底层句柄](#low-level-objects) |

页面框定义了显示坐标，因此是唯一使用PDF用户空间（y从MediaBox底边向上）的地方。`page.boxes`按存储的值读取它们，并整理角点顺序：没有`media`时为原点处的A4，没有`crop`时为MediaBox，没有`bleed`、`trim`和`art`时为CropBox。为`media`赋值会像pymupdf的`set_mediabox`一样删除其他页面框；为其他页面框赋值需要一个位于MediaBox内且有面积的矩形，`del page.boxes.crop`（`bleed`、`trim`、`art`同理）会恢复其默认值。`page.user_to_display`将用户空间映射到显示坐标，`~page.user_to_display`则反向映射。

```python
page.boxes.crop = (36, 36, 576, 756)                # show a Letter page without its half-inch margin
trim_on_screen = page.boxes.trim * page.user_to_display
page.rotation = 90
```

### 添加内容 { #page-content }

| 成员 | 用途 |
|---|---|
| `draw(*, underlay=False)` | 按顺序绘制文本、直线和矩形等路径、图像以及任意文档页面的[画布](#canvas)；参见[写入文本](#writing-text) |
| `replace_text(search, replacement, *, fallback_font=None)` | 以绘制该文本的字体替换文本，并保持其后文本的位置 |

同一图像或页面再次绘制时，在文件中只存储一份。

### 批注与链接 { #page-annotations }

| 成员 | 用途 |
|---|---|
| `annotations` | 页面上除链接和表单控件以外的批注，以`Annotation`句柄给出 |
| `links` | 页面的链接，按页面列出的顺序给出`Link`句柄 |

`page.annotations`是按页面列出顺序排列的`Annotation`句柄序列；弹出窗口属于指向它的批注，不单独列出。句柄每次访问都读取文档，具有`AnnotationType`成员`type`、显示坐标中的`rect`、文本`contents`、`author`、`subject`、`name`、`icon`和`intent`、以存储的PDF日期字符串给出的日期`created`和`modified`、以`Color`记录或`None`给出的`color`和`interior_color`、`opacity`、`AnnotationFlags`值`flags`，以及`xref`、`obj`和`page`。`page.annotations.add(Highlight(rects, *, color=Color(1, 1, 0), opacity=0.4, contents=None))`高亮一个或多个矩形或四边形（例如搜索结果）并返回新句柄，`del page.annotations[i]`连同弹出窗口一起删除批注。`AnnotationType`和`AnnotationFlags`的成员与pymupdf的`PDF_ANNOT_*`和`PDF_ANNOT_IS_*`常量取值相同。

```python
from pylopdf import AnnotationType, Color, Highlight

note = page.annotations.add(Highlight(page.search("total"), color=Color(1, 0.8, 0), contents="Check"))
for annotation in page.annotations:
    if annotation.type is AnnotationType.TEXT:
        print(annotation.author, annotation.contents)
```

读取批注时每页最多4,096个批注和1 MiB文本，高亮的`contents`最多1 MiB（`LimitError`代码`annotation_input_size`）。

`page.links`是`Link`句柄的序列：`page.links.add(rect, target)`在显示坐标的矩形上添加链接并返回其句柄，`del page.links[i]`删除一个链接。句柄每次访问都读取文档，给`rect`或`target`赋值即编辑链接。链接目标与[书签](#outline)相同，是URI字符串、`Destination`、`RemoteDestination`、`Launch`或`NamedAction`之一。动作为JavaScript等pylopdf不读取的动作、或目标不指向任何页面的链接，其目标为`None`。`xref`和`obj`给出批注的对象编号及其在[对象模型](#low-level-objects)中的字典，边框、颜色和标志都在这个字典里。

```python
from pylopdf import Destination

for rect in page.search("Chapter 2"):
    page.links.add(rect, Destination(5))
for link in page.links:
    if isinstance(link.target, str) and link.target.startswith("http:"):
        link.target = "https:" + link.target.removeprefix("http:")
```

读取链接时每页最多4,096个批注和1 MiB的目标文本，写入的目标文本最多1 MiB（`LimitError`代码`annotation_input_size`）。

## 带位置的文本与字形 { #positioned-text }

`page.layout()`返回一个`Layout`，其中包含页面的`width`、`height`和`blocks`。每种记录都包含下一层级的记录，所有边界框都位于显示空间中：

| 记录 | 字段 |
|---|---|
| `Block` | `text`、`bbox`、`lines` |
| `Line` | `text`、`bbox`、`direction`（基线的单位向量）、`is_vertical`、`spans` |
| `Span` | `text`、`bbox`、`origin`、`size`、`font`、`is_bold`、`is_italic`、`is_serif`、`is_monospaced`、`chars` |
| `Char` | `text`、`bbox`、`origin` |

`layout(chars=True)`会把每个span的字形填入其`chars`。`Char`保留一个字形的完整文本，因此"fi"等连字会保留两个字母；其`origin`是字形基线的起点。

```python
with pylopdf.open("report.pdf") as doc:
    layout = doc.pages[0].layout(chars=True)
    for block in layout.blocks:
        for line in block.lines:
            for span in line.spans:
                for char in span.chars:
                    print(char.text, char.bbox, char.origin)
```

边界是根据基线和字号估算的，而不是根据字形轮廓。pylopdf根据间距推断出的空格不会作为字符加入，因此把这些字符连接起来并不一定与`text()`的结果相同。PDF中没有Unicode映射的字形会被跳过，并发出`PylopdfWarning`。

`layout.to_dict()`返回可用于JSON的普通字典，其形状与[pymupdf的dict相同，带字符的布局则与其rawdict相同](https://pymupdf.readthedocs.io/en/latest/textpage.html#structure-of-dictionary-outputs)。它只包含文本块，不含图像块和矢量块，也不含颜色、不透明度或额外的字体度量。[叠加示例](https://github.com/yhay81/pylopdf/blob/main/examples/rawdict-overlay.py)将字形边界和起点绘制为HTML预览。

## 页面内容 { #content }

`page.content()`按绘制顺序返回页面所绘制的内容，与hayro的解释一致：一个由显示坐标中的`TextRun`、`Path`和`Image`组成的`Content`序列。所有值都不可变。

```python
with pylopdf.open("report.pdf") as doc:
    content = doc.pages[0].content()
    for run in content.text_runs:
        print(run.text, run.font.name, run.size, run.fill)
        for glyph in run.glyphs:
            print(glyph.text, glyph.origin, glyph.bbox)
    for path in content.paths:
        print(path.bbox, path.fill, path.stroke)
    for image in content.images:
        print(image.xref, image.bbox, image.width, image.height)
```

| 项 | 字段 |
|---|---|
| `TextRun` | `glyphs`、`font`、`render_mode`、`fill`、`stroke`和`bbox`；`text`和`size`由它们得出 |
| `Glyph` | `glyph_id`、`text`、`matrix`、`advance`和`bbox`；`origin`和`size`由矩阵得出 |
| `Path` | `segments`（`MoveTo`、`LineTo`、`CurveTo`和`ClosePath`）、`fill`、`fill_rule`、`stroke`和`bbox` |
| `Image` | `xref`、`width`、`height`、`bits`、`colorspace`、`components`、`is_stencil`、`mask`、`encoded_size`、`resolution`、`matrix`和`bbox` |

每一项还有`blend_mode`、`soft_mask`和`context`。`context`是一个`GraphicsContext`，从外到内列出包围该项的裁剪（`clips`）、透明组（`groups`）和标记内容（`marked_content`），其`opacity`和`clip_bbox`是它们合成的结果。在同一上下文中绘制的项共享同一个上下文对象。

颜料（paint）是`Color`（sRGB加alpha）、`ShadingPattern`或`TilingPattern`之一。`Stroke`包含`paint`、`width`、`cap`、`join`、`miter_limit`、`dashes`和`dash_offset`，均换算到显示空间。文本段的`font`是一个`FontRef`，包含字体程序的`name`、`weight`和样式标志。

- 字形框是字形轮廓的精确外接框，因此空格没有框；Type 3字形的框是其过程所绘制的范围。
- 带裁剪的渲染模式下的文本按其填充或描边绘制，只做裁剪的文本不构成一项。同一路径的填充之后紧接描边时，与`B`一样算作一条路径。
- 用`sh`绘制的着色是一条覆盖裁剪区域并以`ShadingPattern`填充的`Path`。
- hayro把每幅图像都绘制在一个带有当时的不透明度、混合模式和软蒙版的透明组中，因此图像的上下文以该组结尾。覆盖整个可见区域的裁剪与hayro一样不包含在内。

`text_runs=False`、`paths=False`和`images=False`不构建对应种类的项，因此只读取文本密集页面的路径或图像时会快很多倍。`clip=`只保留外接框与给定矩形相接的项，`annotations=False`不包含注释外观。一次调用读取的字形、路径与裁剪的线段、描边的虚线长度以及项合计最多为`max_elements`，默认为262,144。

hayro尚未报告的信息不在模型中：颜色的色彩空间和分量、图层名称等标记内容属性、`FontRef`背后的字体字典，以及图像蒙版的对象。它们列在[引擎工作](https://github.com/yhay81/pylopdf/blob/main/ROADMAP.md#engine-work)中。

## 图像 { #images }

`doc.images`把对象编号映射到文档的图像对象，`page.resources.images`则列出页面资源所及的图像，包括页面所用Form XObject中的图像，每个只列一次。它们都是每次访问时读取文档的`ImageObject`：

| 成员 | 含义 |
|---|---|
| `xref`、`obj` | 对象编号，以及图像在[对象模型](#low-level-objects)中的流 |
| `width`、`height`、`bits` | 以像素计的尺寸和每个分量的位数 |
| `colorspace`、`alternate_colorspace` | 色彩空间，如`"DeviceRGB"`或`"ICCBased"`，以及索引、ICC或分色色彩空间的基础或替代空间 |
| `filters` | 按解码顺序排列的流过滤器，如`("DCTDecode",)` |
| `soft_mask`、`mask`、`is_stencil` | 作为图像对象的软蒙版和模板蒙版，以及图像本身是否为模板蒙版 |
| `extract()` | 以带有`format`、`data`和`resolution`的`EncodedImage`返回图像数据：JPEG数据按存储原样返回，其他图像以PNG返回，不含蒙版 |
| `to_pixmap(*, alpha=False)` | 以[`Pixmap`](#pixmap)返回图像的像素：不含蒙版的RGB，或由蒙版决定透明度的RGBA |

```python
from pathlib import Path

with pylopdf.open("scan.pdf") as doc:
    for image in doc.pages[0].resources.images:
        encoded = image.extract()
        Path(f"image-{image.xref}.{encoded.format}").write_bytes(encoded.data)
```

图像画在何处属于页面内容：`page.content(text_runs=False, paths=False).images`列出每个放置及其框和矩阵。

## 画布 { #canvas }

`page.draw()`返回`Canvas`。请在`with`块中使用它：块中绘制的所有内容会在块结束时按调用顺序写成一个Form XObject；如果块抛出异常，则不写入任何内容。坐标与其他地方一样是显示坐标。

```python
from pylopdf import Color, Matrix, PathBuilder, Stroke

navy = Color(0.1, 0.2, 0.4)
with page.draw() as canvas:
    canvas.rect((40, 40, 555, 120), radius=8, fill=Color(0.95, 0.96, 0.97))
    canvas.text("Inspection report", (56, 90), size=20, fill=navy)
    canvas.line((56, 105), (539, 105), stroke=Stroke(paint=navy, width=0.5))
    fit = canvas.textbox((56, 140, 539, 400), body, size=10.5)
    canvas.image((400, 420, 539, 520), "chart.png")
    with canvas.opacity(0.15), canvas.transform(Matrix(1, 0, 0, 1, 0, 600)):
        canvas.page((56, 0, 539, 200), letterhead[0])
```

| 方法 | 绘制的内容 |
|---|---|
| `path(path, *, fill=None, stroke=None, fill_rule=FillRule.NONZERO)` | `PathBuilder`、`Path`项的几何形状，或线段列表 |
| `line`、`rect(rect, *, radius=0)`、`circle`、`ellipse`、`polygon`、`bezier` | 各绘制一条路径，选项与`path`相同 |
| `text(text, at, *, font=None, size=11, fill=..., stroke=None, line_height=None)` | 第一条基线从`at`开始的若干行文本 |
| `textbox(rect, text, *, font=None, size=11, align="left", line_height=None, ...)` | 在`rect`内自动换行的文本；返回`TextFit` |
| `image(rect, source, *, keep_aspect=True, rotate=0)` | JPEG或PNG文件路径、已编码的bytes，或`Pixmap`；`rotate`按顺时针旋转 |
| `page(rect, source, *, keep_aspect=True)` | 以矢量形式绘制任意已打开文档（包括本文档）的某一页 |
| `draw(item)` | [`page.content()`](#content)返回的`Path`项 |

路径接受`Color`填充和描边：描边可以是带有宽度、线帽、连接方式、斜接限制和虚线的`Stroke`，也可以是表示1点宽线条的`Color`。两者都未指定的路径以1点宽的黑线描边。颜色的`alpha`使相应的填充或描边半透明。`PathBuilder`用`move_to`、`line_to`、`curve_to`、`quad_to`和`close`构建几何形状，并用`rect`、`ellipse`、`circle`、`polygon`、`sector`、`zigzag`和`wave`添加完整的图形；每个方法都返回构建器本身，因此可以链式调用。

文本用[`Font`](#font)绘制。OpenType字体用HarfRust进行字形整形（shaping），并以子集形式嵌入；无论有多少次调用用到它，每个画布只嵌入一次。不指定`font`时，西欧文本用Helvetica绘制，其他文本用该文字对应的已安装[字体extra](getting-started.md#installation)绘制。文本可以填充、描边或两者兼有。`text`把每一行放在上一行下方`line_height × size`处，默认使用字体自身的行高，即上伸部减去下伸部。`textbox`在Unicode允许换行的位置断行，并以相同的间距排列各行，第一条基线位于`rect`顶边下方一个上伸部处；文本放不下时不绘制任何内容，`TextFit.spare_height`为表示不足量的负值，因此`fit.fits`为假。[`Font.text_height`](#font)在绘制之前测出textbox所需的高度，可据此确定文本外框的大小或决定分页：

```python
font = pylopdf.Font.from_file("NotoSansJP-Bold.otf")
height = font.text_height(note, 10.5, width=300)
if y + height + 16 > page.rect.height - 40:
    page = doc.pages.new()
    y = 40
with page.draw() as canvas:
    canvas.rect((40, y, 356, y + height + 16), radius=4, stroke=navy)
    canvas.textbox((48, y + 8, 348, y + 8 + height), note, font=font, size=10.5)
```

以下四个上下文管理器作用于其中绘制的所有内容，并且可以嵌套：

| 作用域 | 效果 |
|---|---|
| `clip(path, *, fill_rule=...)` | 只在路径内部绘制 |
| `transform(matrix)` | 变换绘制内容；嵌套的变换从最内层开始应用 |
| `opacity(alpha)` | 先把绘制内容合成为一个组，再以不透明度`alpha`绘制 |
| `blend(mode)` | 用`BlendMode`将绘制内容与其下方的内容混合 |

`page.draw(underlay=True)`写入到页面现有内容的下方。画布多次绘制的图像或页面，或文档之前已放置过的图像或页面，只存储一份。页面按调用`canvas.page()`时的状态绘制。`draw(item)`会重现项的裁剪、组不透明度和混合模式，但不重现其软蒙版；文本段、图像和图案颜料暂时还无法绘制。一个画布最多容纳1,048,576个路径点，最多嵌套16层作用域；超出时会抛出`LimitError`，其代码为`path_point_count`或`canvas_scope_depth`。

## 新文档 { #create }

`pylopdf.create()`开始创建一个由krilla整体写出的文档。`builder.page(size)`为新页面返回一个[画布](#canvas)，该页面在其`with`块结束时加入文档；`builder.to_bytes()`返回文件，`builder.finish()`则将其作为`Document`打开。

```python
from pathlib import Path

import pylopdf
from pylopdf import Color, TagRole

font = pylopdf.Font.from_file("NotoSans-Regular.ttf")
builder = pylopdf.create(standard="pdf/ua-1", language="en", title="Annual report")
with builder.page((595.28, 841.89)) as canvas:
    canvas.bookmark("Summary")
    with canvas.tag(TagRole.HEADING_1, title="Summary"):
        canvas.text("Summary", (56, 80), font=font, size=20)
    with canvas.tag(TagRole.PARAGRAPH):
        canvas.textbox((56, 100, 539, 300), body, font=font, size=11)
    with canvas.tag(TagRole.FIGURE, alt="Revenue by quarter"):
        canvas.image((56, 320, 300, 480), "chart.png")
    canvas.line((56, 800), (539, 800), stroke=Color(0.6, 0.6, 0.6))
Path("report.pdf").write_bytes(builder.to_bytes())
```

| `create()`的参数 | 含义 |
|---|---|
| `standard` | 从`"pdf/a-1a"`到`"pdf/a-4f"`、`"pdf/ua-1"`，或其中多个的组合，例如`("pdf/a-2a", "pdf/ua-1")` |
| `tagged` | 根据`tag`作用域构建结构树；PDF/UA和PDF/A的`a`级别会自动启用它 |
| `language`、`title`、`author`、`subject`、`keywords` | 文档的BCP 47语言和元数据 |
| `created` | 创建日期，默认为当前时间；指定固定值可使输出可复现 |

写出指定了`standard`的文档时会对其进行验证；如有违规，则不写出文件，而是抛出`PdfError`，列出krilla发现的所有问题，例如PDF/A-1中的透明效果，或PDF/UA中缺少标题或书签。需要人工判断的要求（例如有意义的替代文本）仍需由你自己满足。

新文档的画布能绘制页面画布所能绘制的内容，但有以下不同：

- 所有字体都会嵌入，因此文本需要OpenType[`Font`](#font)：不指定`font`的文本使用该文字对应的已安装字体extra，而西欧文本在krilla能够绘制Standard 14字体之前都需要指定`font`。
- `canvas.page()`暂时还不能放置其他页面；请在完成后的文档的页面上绘制它们。
- `tag(role, *, alt=None, lang=None, title=None, actual_text=None)`把块中绘制的内容作为一个结构元素。角色是`TagRole`的成员，例如`PARAGRAPH`、`HEADING_1`到`HEADING_6`、`LIST`和`LIST_ITEM`、`TABLE`、`TABLE_ROW`和`TABLE_DATA`，以及带有`alt`文本的`FIGURE`。作用域可以嵌套，作用域内的每行文本都是它的一个span；位于所有作用域之外或`ARTIFACT`作用域之内的内容是工件（artifact）。
- `bookmark(title, *, level=1, at=(0, 0))`添加一个书签条目，显示页面上的`at`位置；级别每次只能加深一级。

添加更多页面后，可以再次调用`finish()`和`to_bytes()`。保存`finish()`返回的`Document`会用lopdf重新写出，因此如需与krilla验证时完全一致的文件，请使用`to_bytes()`。一个文档最多包含65,536页和65,536个书签，一个标签的文本或一个书签标题最多64 KiB（`tag_text_size`）。

## 表格 { #tables }

`page.tables()`查找以框线绘制的表格，包括合并单元格。`strategy="text"`在行和列对齐明确时还能检测无边框表格；它不是默认选项，因为对齐的多栏正文可能看起来像表格。`clip=`只保留完全位于某个矩形内的表格。

返回结果为`Table`记录列表，带有`bbox`、`rows`（逐行排列的单元格文本）、`cells`（以相同形状排列的各单元格`Rect`）、`diagnostics`和`markdown()`。合并单元格的文本和框保存在其左上角的槽位中，被它覆盖的槽位为`None`。`diagnostics.confidence`是用于给候选结果排序的0–1分数，并非概率；其他诊断项给出其依据。

```python
for table in page.tables():
    header, *body = table.rows
    print(table.bbox, header, len(body))
```

## 文本与字体 { #fonts }

### 写入文本 { #writing-text }

文本用[画布](#canvas)绘制：`canvas.text(text, at, ...)`从某个点开始写入各行，`canvas.textbox(rect, text, ...)`在矩形内写入自动换行的文本。

```python
from pylopdf import Color, Font

with page.draw() as canvas:
    canvas.text("CONFIDENTIAL", (40, 40), size=18, fill=Color(1, 0, 0))
    canvas.text("社外秘", (40, 70), size=18)                                    # 需要 pylopdf[jp]
    canvas.text("简体中文", (40, 100), font=Font.installed(language="zh-CN"))  # 需要 pylopdf[zh-cn]
```

用`font=`选择字体：

- 不指定：西欧（WinAnsi）文本使用Helvetica，其他文本使用与其文字对应的已安装extra中的字体；
- `Font.standard(name)`：Standard 14字体，例如`"Times-Bold"`。Standard 14字体不会嵌入，且只覆盖西欧字符；
- `Font.from_file(path)`或`Font.from_bytes(data)`：OpenType字体，只嵌入用到的字形；
- `Font.installed(language=...)`或`Font.installed(family=...)`：已安装extra中的字体。

一种字体必须包含文本中的所有字符；pylopdf不会逐字符切换字体。包含假名、韩文、注音符号、阿拉伯文、希伯来文、天城文或泰文的文本会自动选用已安装的对应extra。仅由汉字组成的文本有歧义，默认使用日文字体；要选用其他语言，请传入`font=Font.installed(language="zh-CN")`（或`"zh-TW"`、`"ko"`）。

从右到左的文本会被正确地进行字形整形（shaping）。读回PDF时，完全由从右到左文字组成的行，以及在从右到左文字中夹着一个拉丁单词或数字的行，会按逻辑顺序输出；其他混合方向的行按视觉顺序输出。部分复杂文字的字形簇（尤其是较长的天城文）提取结果可能与原文略有不同。

### Font { #font }

```python
pylopdf.Font.standard(name)
pylopdf.Font.from_file(path, /, *, index=0, weight=None, max_size=64 MiB)
pylopdf.Font.from_bytes(data, /, *, index=0, weight=None, max_size=64 MiB)
pylopdf.Font.installed(*, language=None, family=None, serif=False, weight=None, italic=False)
```

| 示例 | 加载的字体 |
|---|---|
| `Font.standard("Helvetica")`、`Font.standard("Times-Bold")` | Standard 14字体，按PostScript名称指定 |
| `Font.from_file("NotoSansJP-Bold.otf")`、`Font.from_bytes(data)` | OpenType字体，来自文件或其字节 |
| `Font.from_file("NotoSansCJKjp-VF.otf", weight=700)` | 可变字体的某个字重 |
| `Font.from_file("fonts.ttc", index=1)` | 字体集合中的一个字体 |
| `Font.installed(language="ja")`、`Font.installed(family="Noto Sans JP")` | 已安装extra中的字体；`serif=True`选择该语言的衬线体 |
| `Font.installed(language="he", weight=700)` | 已安装可变字体的粗体字重 |

字体按[画布](#canvas)的绘制方式测量文本。`text_width(text, size)`是一行的宽度，含字距调整和连字。`text_height(text, size, *, width, line_height=None)`是文本按`width`点宽度换行时`canvas.textbox`所需的高度，因此这个高度的textbox正好容纳该文本。属性有`name`、`family`、`weight`、`is_bold`、`is_italic`、`is_serif`、`units_per_em`、`ascender`和`descender`；`has_glyph(char)`返回字体是否有该字符的字形。`typing.get_args(pylopdf.StandardFontName)`列出`Font.standard()`接受的14个名称。Standard 14字体不嵌入，只能书写西欧（WinAnsi）字符。

不指定`weight`加载的可变字体使用其默认实例，而默认实例不一定是常规字重：可变字体Noto Sans CJK JP的默认实例是Thin。要使用常规字重，请传入`weight=400`。填写表单所用的字体（`doc.form.font`）也是如此。

阿拉伯语、希伯来语、印地语和泰语的extra提供可变字体，因此字重轴上的任意字重都可以使用（100–900；Noto Naskh Arabic为400–700）。CJK extra每种样式只提供一个常规字重的字体：`Font.installed(language="ja", weight=700)`会抛出`PdfError`，请用`Font.from_file()`加载Noto Sans JP Bold等粗体字体。pylopdf从不通过扭曲字形来模仿粗体或斜体。

pylopdf不搜索系统字体目录，也没有全局字体注册表，因此输出只取决于你传入的字体。

### 表单 { #forms }

`doc.form`是按名称排序、从`person.first`这样的完全限定字段名到`Field`句柄的映射。句柄每次访问都读取文档，具有`name`、`type`（`FieldType.TEXT`、`CHECKBOX`、`RADIO`、`PUSHBUTTON`、`COMBOBOX`、`LISTBOX`、`SIGNATURE`等`FieldType`成员）、`value`、`flags`（从父字段继承的`FieldFlags`）、`choices`（选择字段的`FieldChoice(value=, label=)`选项），以及`xref`和`obj`。

```python
from pylopdf import Font

for name, field in doc.form.items():
    print(name, field.type, field.value)
doc.form["customer"].value = "Alice"   # 写入值及其显示外观
doc.form["agree"].value = True         # 复选框的选中状态
doc.form.font = Font.from_file("NotoSansJP-Regular.otf")
doc.form["name"].value = "山田 太郎"
```

给`value`赋值可填写文本框、组合框、列表框、复选框和单选按钮字段，并重新生成每个字段的外观。`True`和`False`选择按钮的选中状态和`"Off"`。西欧（WinAnsi）文本会用Helvetica自动缩放以适应字段，其他字符使用已安装的字体extra。给`doc.form.font`设置一个OpenType `Font`后，之后的每次填写都使用该字体，例如中文文本可用`Font.installed(language="zh-CN")`。此设置属于`Document`对象，不会被保存。梳状字段（comb）、对齐方式和最大长度都会被遵守；值过长时会抛出异常，文档保持不变。不支持富文本、按钮动作和签名；签名请参阅[生态系统方案](ecosystem.md#signatures)。

## 附件 { #attachments }

`doc.attachments`是从名称到`Attachment`对象的映射：

```python
doc.attachments.add("data.csv", csv_bytes, description="Source data")
for name in doc.attachments:
    item = doc.attachments[name]
    print(item.filename, item.size, item.description)
data = doc.attachments["data.csv"].read()
doc.attachments["data.csv"].write(new_bytes)
del doc.attachments["data.csv"]
```

`Attachment`还有`name`、`creation_date`和`mod_date`。名称不存在时抛出`KeyError`。

## 底层对象 { #low-level-objects }

`doc.objects`将对象编号映射到PDF的对象，`doc.trailer`、`doc.catalog`和`page.obj`是字典句柄。对象以Python值而非PDF语法表示：

```python
from pylopdf import Name

catalog = doc.catalog
catalog["PageLayout"] = Name("TwoColumnLeft")   # 名称对象；普通str是文本字符串
font = doc.objects.add({"Type": Name("Font"), "Subtype": Name("Type1"), "BaseFont": Name("Helvetica")})
content = doc.objects.add_stream(b"BT /F9 24 Tf 72 72 Td (Hi) Tj ET")
page.obj["Resources"] = {"Font": {"F9": doc.objects[font]}}   # 以引用形式存储
page.obj["Contents"] = doc.objects[content]
doc.objects[content].read()                     # 解码后的流数据
```

| PDF值 | Python值 |
|---|---|
| null、布尔值、整数、实数 | `None`、`bool`、`int`、`float` |
| 名称 | `Name`，不带前导斜杠的`str` |
| 字符串 | `String`，其`.text`可解码的`bytes` |
| 数组、字典 | `Array`（可变序列）、`Dictionary`（可变映射） |
| 流 | `Stream`，带有`read(*, decode=True)`和`write(data, *, filter="FlateDecode")`的字典 |

修改会立即生效。指向不存在对象的引用读取为`None`。将句柄存入另一个对象时，存储的是指向它的引用。对象被删除后，或`save(renumber=True)`对对象重新编号后，句柄会抛出`StaleObjectError`。pylopdf不会检查你的修改在PDF层面是否合理。

## 几何 { #geometry-types }

`Point`、`Matrix`、`Rect`、`IRect`和`Quad`都是不可变的具名元组，因此索引、解包和比较的用法与元组相同。运算符返回新值；pymupdf中`transform()`、`normalize()`等就地修改的方法并不存在，请改为赋值结果（`r = r * m`）。所有坐标都必须是有限数。

### Point与Matrix { #geometry }

`Point(x, y)`表示位置。`Matrix(a, b, c, d, e, f)`是仿射变换；`Matrix(sx, sy)`缩放，`Matrix(degrees)`旋转，`Matrix(sx, sy, 1)`错切。

```python
from pylopdf import Matrix, Point

scale = Matrix(2, 3)
shift = Matrix(1, 0, 0, 1, 20, 30)
transform = scale * shift            # 先缩放，再平移
position = Point(10, 20) * transform  # Point(x=40.0, y=90.0)
original = position / transform      # Point(x=10.0, y=20.0)
```

| 运算 | 结果 |
|---|---|
| `p + q`、`p - q`、`p * 2`、`-p` | 向量运算 |
| `p * m` | 变换后的点：`(a*x + c*y + e, b*x + d*y + f)` |
| `m1 * m2` | 先应用`m1`，再应用`m2` |
| `~m` | 逆矩阵（`m`不可逆时为零矩阵） |
| `p / m`、`m1 / m2` | 乘以逆矩阵；逆矩阵不存在时抛出`ZeroDivisionError` |
| `abs(p)`、`p.unit`、`p.distance_to(other, unit="px")` | 长度、方向和距离（`px`、`in`、`cm`、`mm`） |
| `m.is_rectilinear` | 变换后边是否仍保持水平和竖直 |

由于y轴向下，`Matrix(90)`会把`(1, 0)`变为`(0, 1)`。`Matrix.IDENTITY`是单位矩阵，`Matrix.planish(p1, p2)`返回把`p1`移到原点、把`p2`移到x轴正方向上的矩阵。

### Rect、IRect与Quad { #rectangles }

`Rect(x0, y0, x1, y1)`也接受元组，或者像`Rect(top_left, bottom_right)`这样的角点。`IRect`接受相同的参数，并向外取整为整数。`Quad(ul, ur, ll, lr)`是任意四角形状，例如旋转后的矩形；`rect.quad`和`quad.rect`可在两者之间转换。

```python
from pylopdf import Matrix

hits = page.search("Total")
area = hits[0] | hits[-1]                    # 覆盖第一个和最后一个结果
inside = [hit for hit in hits if hit in page.rect]
box = area * Matrix(1, 0, 0, 1, 0, 20)        # 向下移动20点
quad = area.morph(area.tl, Matrix(30))       # 绕一个角旋转30度
```

| 运算 | 结果 |
|---|---|
| `r | p`、`r | other` | 同时覆盖某个点或矩形的最小矩形 |
| `r & other` | 重叠部分 |
| `r * m`、`r / m` | 变换后矩形的边界框 |
| `r + other`、`r * 2` | 坐标运算 |
| `p in r`、`other in r` | 包含关系 |
| `r.width`、`r.height`、`abs(r)`、`r.get_area(unit="px")` | 尺寸和面积 |
| `r.is_empty`、`r.is_valid`、`r.is_infinite`、`r.intersects(other)` | 判断 |
| `r.normalized()` | 满足`x0 <= x1`和`y0 <= y1`的矩形 |
| `r.round()`、`r.irect` | 覆盖它的`IRect` |
| `r.torect(other)` | 将一个矩形映射到另一个矩形的矩阵 |
| `r.morph(point, m)`、`q.morph(point, m)` | 绕固定点变换得到的`Quad` |
| `q * m`、`p in q`、`q.is_rectangular`、`q.is_convex` | 四边形的变换与判断 |

`Rect.EMPTY`和`Rect.INFINITE`是MuPDF的空矩形和无限矩形，`IRect`和`Quad`也有相同的类属性。与pymupdf不同，判断矩形是否位于四边形内时会检查全部四个角。

## Pixmap { #pixmap }

`Pixmap`保存渲染页面或图像的像素，不可修改。

| 成员 | 用途 |
|---|---|
| `width`、`height`、`channels`、`alpha`、`stride`、`colorspace` | 尺寸和布局：灰度时`channels`为1，带alpha的灰度为2，RGB为3，带alpha的RGB为4；`colorspace`为`Colorspace.GRAY`或`Colorspace.RGB` |
| `numpy.asarray(pix)`、`memoryview(pix)` | 无需复制即可访问像素，灰度时形状为`(height, width)`，否则为`(height, width, channels)` |
| `bytes(pix)` | 像素的副本 |
| `resolution` | 水平和垂直分辨率（dpi）：渲染的页面为其渲染分辨率，否则为96；PNG和JPEG输出会写入它 |
| `Pixmap.from_array(array, /, *, resolution=None)` | 复制上述形状的`uint8`数组得到的pixmap，`resolution`是由1到65,535的整数组成的`(x, y)`元组，默认为96；传入`Pixmap`时共享其像素，未指定时沿用其分辨率 |
| `Pixmap.from_file(path, /, *, max_size=64 MiB, max_pixels=64,000,000)`、`Pixmap.from_bytes(data, /, ...)` | 解码PNG或JPEG：PNG以8位样本保留灰度、RGB和alpha，JPEG解码为灰度或RGB（CMYK会转换），分辨率取自文件，否则为96；超出上限的输入引发代码为`image_input_size`或`image_pixel_count`的`LimitError` |
| `to_bytes(format="png", *, quality=95, max_size=64 MiB)` | PNG或JPEG bytes；JPEG没有alpha |
| `save(path, /, *, format=None, quality=95)` | 写出PNG或JPEG；未指定`format`时由扩展名`.png`、`.jpg`或`.jpeg`决定 |

图像处理交给NumPy、Pillow和OpenCV：`PIL.Image.fromarray(numpy.asarray(pix))`即可得到Pillow图像。

## 模块级函数与类型 { #module-level }

| 名称 | 用途 |
|---|---|
| `open(source, /, *, password=None, limits=None)` | 打开文档；参见[Document](#document) |
| `probe(source, /, *, password=None, max_file_size=None)` | 无需加载文档即可读取的`Probe`记录，包含`page_count`、`version`、`needs_password`、`is_repaired`和`metadata`（存在时包含`title`、`author`、`subject`、`keywords`、`creator`、`producer`、`created`和`modified`） |
| `DocumentLimits`、`DocumentLimits.web()` | 用于不可信输入的资源上限；参见[安全](security.md#untrusted-pdfs) |
| `OcrEngine` | 可复用的OCR引擎；参见[离线OCR](ocr.md) |
| `Canvas`、`PathBuilder`、`TextFit` | 参见[画布](#canvas) |
| `create(...)`、`Builder`、`Standard`、`TagRole` | 参见[新文档](#create) |
| `Font`、`StandardFontName` | 参见[Font](#font) |
| `Name`、`String`、`Array`、`Dictionary`、`Stream`、`Objects` | [底层对象](#low-level-objects) |
| `Content`、`TextRun`、`Path`、`Image`、`Glyph`、`FontRef`、`Color`、`Stroke`、`GraphicsContext`及其他内容类型 | [页面内容](#content) |
| `Colorspace` | [`Pixmap`](#pixmap)的颜色空间：`GRAY`和`RGB` |
| `Outline`, `OutlineItem`, `OutlineEntry`, `Destination`, `RemoteDestination`, `Launch`, `NamedAction`, `PageLabelRange`, `PageLabelStyle` | [书签与页码标签](#outline) |
| `Annotation`, `Annotations`, `AnnotationType`, `AnnotationFlags`, `Highlight`, `Link`, `Links` | [批注与链接](#page-annotations) |
| `PageBoxes` | [页面几何](#page-geometry) |
| `Form`, `Field`, `FieldType`, `FieldFlags`, `FieldChoice` | [表单](#forms) |
| `FallbackFont`, `FallbackFonts` | [渲染与图像](#document-rendering) |

代码和标志只作为枚举成员提供，没有模块级常量：pymupdf的`PDF_PERM_PRINT`是`Permissions.PRINT`，`PDF_ANNOT_TEXT`是`AnnotationType.TEXT`，`PDF_ANNOT_IS_HIDDEN`是`AnnotationFlags.HIDDEN`，取值相同。`Access`的成员是`USER`和`OWNER`，`Encryption`描述如何加密保存的文件。页面内容的枚举是`BlendMode`、`FillRule`、`LineCap`、`LineJoin`、`TextRenderMode`、`ShadingType`、`SoftMaskType`和`ImageMask`。

结果是记录，即`Word`、`Block`、`Line`、`Span`、`Char`、`Layout`、`Table`、`TableDiagnostics`、`OcrWord`、`Path`、`Image`、`MarkInfo`、`PdfaClaim`、`OutlineEntry`、`PageLabelRange`、书签目标、`DocumentComplexity`和`ImageCompressionResult`等不可变的数据类（frozen dataclass）。API只有`Layout.to_dict()`返回普通字典，它以pymupdf的形式供JSON使用。

## 异常与警告 { #exceptions }

| 名称 | 抛出时机 |
|---|---|
| `PdfError` | pylopdf错误的基类；`ValueError`的子类 |
| `MalformedPdfError` | 输入不是可读取的PDF，包括输入为空的情况 |
| `PasswordError` | 密码既不是用户密码也不是所有者密码 |
| `EncryptedDocumentError` | 必须先用`unlock()`解锁文档 |
| `LimitError` | 达到了[大小或资源上限](security.md#error-codes)；`.code`说明是哪一个 |
| `DocumentClosedError` | 文档已关闭 |
| `StalePageError` | `Page`是在添加、删除或重排页面之前获取的 |
| `StaleObjectError` | 底层句柄已不再指向任何对象 |
| `OcrError` | OCR无法运行 |
| `PylopdfWarning` | 可恢复的问题，例如文件被修复或某个字体无法读取 |

文件不存在时抛出Python内置的`FileNotFoundError`。
