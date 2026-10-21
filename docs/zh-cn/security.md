---
title: 安全
description: pylopdf支持版本、私密漏洞报告方式，以及处理不受信任PDF时的安全建议。
---

# 安全

本页说明如何报告漏洞，以及如何用pylopdf处理不受信任的PDF：打开时应使用的资源策略、拒绝时携带的错误code、各API自身的上限，以及内部guard的工作方式。只有PyPI上的最新版本会获得安全修复。

## 报告漏洞 { #report-a-vulnerability }

请通过[GitHub Security Advisories](https://github.com/yhay81/pylopdf/security/advisories/new)私密报告漏洞，不要创建公开Issue。我们会争取在一周内首次响应。

## 处理不受信任的PDF { #untrusted-pdfs }

pylopdf由Rust编写且没有必需的Python依赖，但解析恶意PDF输入仍然存在固有风险。

### 快速开始 { #quick-start }

!!! warning "使用完整的资源策略"
    请向`pylopdf.open()`传入`limits=pylopdf.DocumentLimits.web()`。该预设适合作为内存受限Web或queue worker处理用户上传内容的保守起点。

!!! warning "强制执行CPU deadline并隔离进程"
    请在Worker、process或container宿主中强制执行CPU deadline。资源预算可防止已记录的allocation和输出增长，但不会按wall-clock时间中断正在运行的parser或interpreter。批量处理不受信任的文件时，尽量在sandbox或container中运行。

```python
import pylopdf

try:
    with pylopdf.open(
        "upload.pdf",
        limits=pylopdf.DocumentLimits.web(),
    ) as doc:
        facts = doc.complexity
        preview = doc[0].get_pixmap(dpi=144)
except pylopdf.LimitError as error:
    reject_upload(error.code)
```

当预算或固定guard拒绝处理时，pylopdf会抛出`PdfError`的子类`LimitError`。其`code`是下方[code表](#limit-error-codes)中的稳定标识符，同一值也位于`error.args[0]`，因此应用无需解析消息即可据此分支处理。

`doc.complexity`报告页数、对象数、流数、编码流字节数以及直接对象最大深度。它既不解码流也不调用renderer，适合在提取前进行routing。结构和解压预算验证打开时的source；生成结果若要跨越新的trust boundary，请用同一策略重新打开。

嵌入JavaScript在设计上不受支持，也绝不会执行。

### 资源预算 { #document-limits }

`DocumentLimits.web()`目前独立应用以下预算。为保持兼容，`DocumentLimits`的每个字段默认均为`None`（不设上限）。

| 资源 | `DocumentLimits`字段 | `web()`上限 | 拒绝code |
|---|---|---:|---|
| 输入文件 | `max_file_size` | 10 MiB | `file_size` |
| 页数 | `max_pages` | 200 | `page_count` |
| 间接对象 | `max_objects` | 100,000 | `object_count` |
| 任意单个解码流（包括图像RGBA估算） | `max_decompressed_size` | 64 MiB | `decompressed_size` |
| 单个页面内容流 | `max_page_content_size` | 10 MiB | `page_content_size` |
| 累计解码或估算的流字节 | `max_total_decompressed_size` | 128 MiB | `total_decompressed_size` |
| 直接array/dictionary嵌套 | `max_object_depth` | 64 | `object_depth` |
| 已解释页面累计UTF-8 glyph payload | `max_text_size` | 1 MiB | `text_size` |
| 传给rendering／extraction的完整PDF snapshot | `max_interpretation_size` | 64 MiB | `interpretation_size` |
| 已解释页面累计带位置glyph record | `max_text_glyphs` | 65,536 | `text_glyph_count` |

如需适配其他负载，可直接构造`DocumentLimits(...)`。每个非`None`值必须是正整数。`max_decompressed_size=`仍可作为其单流预算的兼容简写，但不能与`limits=`同时使用。

`max_interpretation_size`在hayro首次读取保留input，以及pylopdf在编辑或解密后、或为准备rendering副本（例如合成text markup appearance）而serialize当前状态时生效。有界writer会拒绝越界write，且不会安装不完整的renderer／extractor cache。为保持兼容，默认值为`None`；`DocumentLimits.web()`使用64 MiB。

设置`max_text_size`后，plain-text提取会预检组装后精确的UTF-8大小，并将一次提取call限制为glyph payload预算的两倍。每个非空glyph至少贡献一个payload字节，而推断gap与换行的总数不会超过glyph数。该输出上限作用于整个call，而不是内部的4,096个entry chunk；显式sequence最多接受页数与4,096中较大者的entry，因此重复页码无法绕过策略。拒绝code仍为`text_size`；为保持兼容，默认值仍为`None`。

`max_text_glyphs`限制line组装前保留的record数，因此也限制结构化文本可materialize的block、line、span、word和`rawdict` glyph entry数量。同一页的文本与表格解释共享一次累计admission，被拒绝的页面不消耗预算。为保持兼容，默认值为`None`；`DocumentLimits.web()`使用65,536。

`get_text("rawdict")`选项与其他提取模式使用相同的已解释glyph cache，以及相同的累计`max_text_size`/`max_text_glyphs` admission；请求多种表示不会对同一已缓存页面重复计费。一个entry可能包含多字符Unicode映射，因此glyph数量和UTF-8 payload仍是两项独立预算。Rust raw layout buffer与glyph文本副本采用fallible扩展；原生allocation被拒绝时会抛出`PdfError`，而不会返回部分layout。该策略限制的是保留的glyph数据，而不是Python字典的精确字节大小，也不是调用方之后的JSON/HTML serialization。从worker导出raw layout时，请另行限制该输出。

### 错误code { #limit-error-codes }

`LimitError.code`是以下标识符之一。没有取消方式的code来自固定guard，无论是否设置`DocumentLimits`都适用。

| Code | 限制对象 |
|---|---|
| `file_size` | 输入文件字节（`max_file_size`） |
| `page_count` | 页数（`max_pages`） |
| `object_count` | 间接对象（`max_objects`） |
| `object_depth` | 直接array/dictionary嵌套（`max_object_depth`）；也用于解释时嵌套超过64层的页面（[详情](#object-depth)） |
| `decompressed_size` | 任意单个解码流（`max_decompressed_size`） |
| `page_content_size` | 单个页面内容流（`max_page_content_size`） |
| `total_decompressed_size` | 累计解码流字节（`max_total_decompressed_size`） |
| `text_size` | 提取的累计UTF-8 glyph payload（`max_text_size`） |
| `text_glyph_count` | 累计带位置glyph record（`max_text_glyphs`） |
| `interpretation_size` | 交给rendering／extraction的PDF snapshot（`max_interpretation_size`） |
| `embedded_file_size` | 添加、写入或读取的附件数据（默认64 MiB） |
| `embedded_file_input_size` | 附件查找／删除名称，以及添加时的key/filename/description文本（1 MiB） |
| `form_field_input_size` | `set_form_field()`的caller名称和值（1 MiB） |
| `form_content_size` | 作为Form XObject导入的源页面解码内容 |
| `annotation_input_size` | caller批注文本和highlight矩形 |
| `metadata_input_size` | 元数据写入（1 MiB） |
| `toc_input_size` | 书签写入（标题文本） |
| `toc_entry_count` | 书签读取和写入（65,536个node/entry、131,072条edge） |
| `toc_text_size` | 书签source/returned文本（16 MiB） |
| `named_destination_count` | named destination遍历（65,536个entry/node、131,072条edge） |
| `named_destination_text_size` | named destination key字节（16 MiB） |
| `page_entry_count` | 超过页数与4,096中较大者的显式页面sequence |
| `page_label_input_size` | 页码标签写入 |
| `xmp_metadata_size` | `Document.get_pdfa_claim()`读取的解码XMP（默认1 MiB） |
| `render_output_size` | `render_pages()`的累计encoded PNG（默认512 MiB） |
| `markdown_output_size` | 累计UTF-8 Markdown输出（默认64 MiB） |
| `svg_output_size` | `Page.get_svg_image()`的UTF-8输出（默认64 MiB） |
| `replacement_input_size` | `Page.replace_text()`的search/replacement/fallback输入（4,096 UTF-8 byte） |
| `replacement_output_size` | `Page.replace_text()`的解码内容、encoding数据、增长与输出（默认64 MiB） |
| `pdf_output_size` | `Document.tobytes()`的serialize输出（默认512 MiB） |
| `image_input_size` | `Page.insert_image()`的encoded输入（默认64 MiB） |
| `image_pixel_count` | `Page.insert_image()`解码后的PNG像素（默认64,000,000） |
| `image_inventory_count` | `Page.get_images()`列出的图像和Form XObject（4,096） |
| `font_input_size` | OpenType字体输入（默认64 MiB） |
| `text_input_size` | `insert_text()`/`insert_textbox()`的生成文本输入（默认1 MiB） |
| `text_line_count` | 生成文本的物理行或换行后的行（4,096） |
| `path_point_count` | 一个`Shape`容纳的路径点（1,048,576） |
| `search_input_size` | `search_for()`的搜索文本（4,096 UTF-8 byte） |
| `search_hit_count` | `search_for()`返回的结果数（默认4,096） |
| `password_input_size` | 密码（127 UTF-8 byte） |
| `pixmap_output_size` | `Pixmap.tobytes()`的encoded PNG（默认64 MiB） |
| `ocr_model_size` | `OcrEngine`的detector、recognizer和字典文件累计输入（默认64 MiB） |
| `ocr_dictionary_entries` | `OcrEngine`识别字典的条目（65,536） |
| `object_value_depth` | 通过低层对象API写入的值的嵌套（32层） |
| `stream_data_size` | 低层`Stream.read()`读取的解码数据（默认64 MiB） |
| `stream_filter_count` | 超过16层连续的`/Filter`（[详情](#filter-chains)） |
| `decompression_unverifiable` | 无法安全确定上限的filter chain（[详情](#filter-chains)） |
| `recovery_scan_size` | 估算的cross-reference重建工作量（[详情](#recovery-scan)） |
| `content_nesting_depth` | 超过64层的内容流嵌套（[详情](#content-nesting)） |
| `resource_nesting_depth` | 超过16层的colour space与function链，或超过64层的Type 4 procedure（[详情](#resource-nesting)） |
| `structure_tree_count` | 删除和选择页面时的structure tree清理（[详情](#structure-tree)） |

## 各API的上限 { #api-limits }

各API有自己的输入和输出边界，无论是否设置`DocumentLimits`都适用。存在取消方式时，请仅对可信负载使用。

| API | 默认上限 | 取消方式 | Code |
|---|---|---|---|
| 渲染 | 每页6400万像素 | — | — |
| `Pixmap.tobytes()` | 64 MiB encoded PNG | `max_size=None` | `pixmap_output_size` |
| `render_pages()` | 累计512 MiB encoded PNG | `max_size=None` | `render_output_size` |
| `Page.get_svg_image()` | 64 MiB UTF-8 | `max_size=None` | `svg_output_size` |
| `Document.to_markdown()`、`Table.to_markdown()` | 累计64 MiB UTF-8 | `max_size=None` | `markdown_output_size` |
| `Document.tobytes()` | 512 MiB | `max_size=None` | `pdf_output_size` |
| `get_text()`、`to_markdown()`、`render_pages()`、`delete_pages()`、`select()` | 页数与4,096中较大者的显式entry | — | `page_entry_count` |
| `search_for()` | 搜索文本4,096 UTF-8 byte；4,096个结果 | `max_hits=None` | `search_input_size`、`search_hit_count` |
| `Page.insert_image()` | encoded 64 MiB；解码后PNG 64,000,000像素 | `max_size=None`、`max_pixels=None` | `image_input_size`、`image_pixel_count` |
| OpenType输入（`insert_text`、`insert_textbox`、`set_form_field`、`set_fallback_font`） | 64 MiB | `max_font_size=None` | `font_input_size` |
| `insert_text()`、`insert_textbox()` | 1 MiB UTF-8；4,096行 | `max_text_size=None` | `text_input_size`、`text_line_count` |
| `Shape` | 1,048,576个路径点 | — | `path_point_count` |
| `Page.replace_text()` | 输入4,096 UTF-8 byte；内容和输出64 MiB | `max_size=None` | `replacement_input_size`、`replacement_output_size` |
| 密码 | 127 UTF-8 byte | — | `password_input_size` |
| `Page.get_images()` | 4,096个图像或Form XObject | — | `image_inventory_count` |
| `Page.get_image_info()` | 4,096个placement | — | — |
| `Document.extract_image()` | 64,000,000像素；64 MiB payload | — | — |
| 附件 | 数据64 MiB；名称和文本1 MiB | `max_size=None` | `embedded_file_size`、`embedded_file_input_size` |
| `Document.get_pdfa_claim()` | 解码XMP 1 MiB | `max_size=None` | `xmp_metadata_size` |
| `Page.insert_ocr_text_layer()` | 4,096个非空word；1 MiB UTF-8 | — | — |
| 元数据 | 文本1 MiB | — | `metadata_input_size` |
| 页码标签 | 4,096个entry/node；32层；文本1 MiB | — | `page_label_input_size` |
| AcroForm field tree | 4,096个entry/node；8,192条edge；64层；1 MiB；4,096个choice value | — | `form_field_input_size` |
| AcroForm按钮 | 4,096个widget；8,192个state entry；4,096个名称；1 MiB | — | — |
| 批注与link | 4,096个`/Annots` entry；文本1 MiB | — | `annotation_input_size` |
| named destination | 65,536个entry/node；131,072条edge；key 16 MiB；32层 | — | `named_destination_count`、`named_destination_text_size` |
| 书签（TOC） | 65,536个node/entry；131,072条edge；文本16 MiB；64层 | — | `toc_entry_count`、`toc_text_size`、`toc_input_size` |

以下各节说明每个边界的详情。

### 渲染与图像输出 { #rendering-limits }

- 每页渲染上限为6400万像素。
- `Pixmap.tobytes()`的encoded PNG输出默认上限为64 MiB。writer会在返回Python `bytes`前以`pixmap_output_size`拒绝越界write；`max_size=None`可显式取消。
- `render_pages()`默认渲染所有页，显式entry最多为页数与4,096中较大者，累计encoded PNG默认上限为512 MiB。完成的并行结果共享一个atomic budget，失败时不返回部分list；`max_size=None`可显式取消。worker admission另行限制估算的live raster/conversion buffer，且每个worker每4,096页更换一次hayro cache；不要在application层叠加无限并行。
- `Page.get_svg_image()`的UTF-8输出默认上限为64 MiB，在PyO3创建Python string前拒绝超限结果；`max_size=None`可显式取消。hayro-svg 0.8会在pylopdf应用该边界之前materialize一份内部Rust string。
- `Page.get_images()`在列出的图像或Form XObject达到4,096个时以`image_inventory_count`停止，`Page.get_image_info()`在4,096个placement时停止。`Document.extract_image()`在64,000,000像素与64 MiB payload上限及content和颜色空间guard下解码单个图像，Flate-wrapped JPEG直通也只解压到该预算。

### 页面批处理 { #page-batches }

page batch根据一次获取的页数校验entry。`pages=None`与`insert_pdf()`的完整范围无论页数多少都覆盖所有页。显式sequence可以重复页面，但在超过页数与4,096中较大者的第一个entry处停止materialization并返回`page_entry_count`，因此重复也不会超过处理整个文档一次的成本。文本提取与渲染每4,096个entry更换一次call级hayro cache，而`max_size`、`max_text_size`等输出预算作用于整个call。直接调用core API时也会重复entry检查，core页面导入会在修改前拒绝重复的源页面。

- `delete_pages()`和`select()`在Python与Rust中每次call最多接受页数与4,096中较大者的entry，因此重复页面也不会使选择结果大于文档。iterable会在超过上限的第一个entry、graph修改前停止，并返回`page_entry_count`。`insert_pdf()`导入源文档的任意完整范围。空删除会保留cache、generation及现有`Page` view。
- 页面删除与选择会在pruning前清理指向被排除页面的navigation、这些页面上的AcroForm widget与field，以及tagged PDF中只存在于这些页面的structure tree内容。这些操作不是安全的涂黑（redaction）：任意自定义引用、附件、元数据以及内容的其他副本需要单独检查和删除。structure tree遍历有其自己的[上限](#structure-tree)。

### 文本输出 { #text-output-limits }

- `search_for()`将搜索文本限制为4,096 UTF-8 byte，返回geometry默认限制为4,096项。Python在PyO3 copy前拒绝超限搜索词，Rust边界再次检查两项限制。可信结果集可用`max_hits=None`显式取消；拒绝code为`search_input_size`或`search_hit_count`，且绝不返回部分list。
- `Document.to_markdown()`默认转换所有页，显式entry最多为页数与4,096中较大者，累计UTF-8输出默认上限为64 MiB。在heading统计pass与render pass中，它都只同时保留一页的已解释layout、table和word。在组装页面输出前，每个table都会获得剩余的累计预算。标题、段落、列表和表格在保留entry时计入预算，完整大小获准后再线性组装最终页面。`Table.to_markdown()`的直接调用默认使用同一上限，并预检包含合并单元格展开在内的转义后精确UTF-8大小。超过上限时不返回部分string；`max_size=None`可显式取消。

### 文本、图像与绘制输入 { #insertion-limits }

- `Page.insert_image()`默认将encoded JPEG/PNG输入限制为64 MiB，将解码后的PNG输入限制为64,000,000像素。filename输入在释放GIL的状态下，通过与core直接调用相同的有界Rust边界读取；PNG尺寸会在分配解码存储前检查。可信负载可用`max_size=None`和`max_pixels=None`显式取消。
- `insert_text`、`insert_textbox`、`set_form_field`与`set_fallback_font`的显式或自动选择的OpenType输入默认限制为64 MiB。buffer输入在PyO3 copy前拒绝，filename输入通过释放GIL的有界Rust路径读取。可信负载可用`max_font_size=None`显式取消。
- `insert_text()`与`insert_textbox()`的生成文本输入默认限制为1 MiB UTF-8，以及4,096个物理行或换行后的最终行；`insert_text()`按`str.splitlines()`分出的行计数，列表则每项计为一行。Python在分行与PyO3 copy前检查物理输入，Rust边界再次检查并在mutation前停止换行layout；textbox会在分配展开后的string前预检tab展开。可信插入输入可用`max_text_size=None`显式取消；拒绝code为`text_input_size`或`text_line_count`。AcroForm文本与选项外观保留固定的4,096行layout上限。UAX #14与grapheme index、行collection以及保留的行文本均采用fallible扩展，因此allocation被拒绝时既不会暴露部分layout，也不会编辑文档。pylopdf自有的规范化、grapheme引用、shaped line／glyph collection、WinAnsi输出与输入派生的text operator扩展也采用fallible处理；Standard 14宽度检查不会分配encoded文本副本。allocation被拒绝时返回`PdfError`；插入保持不修改文档，表单填写则原子rollback。
- `Shape.insert_text()`与`Shape.insert_textbox()`对每次调用应用相同的文本和字体上限。一个图形在提交前最多容纳1,048,576个路径点，添加点时与Rust边界都会检查；拒绝code为`path_point_count`。提交会在修改页面前完成绘制生成、导入计划与页面绘制目标的准备，因此被拒绝时文档保持不变。
- `Page.replace_text()`将search/replacement/fallback的合计输入限制为4,096 UTF-8 byte，并为解码的页面内容、字体encoding数据、替换增长和最终stream输出设置64 MiB默认上限。它在提交前准备页面专用stream，因此复制的页面不会编辑共享内容，no-match/error调用会保留文档和cache。caller文本在PyO3 copy前逐步计数，而不是为预检完整encode。可信输入可用`max_size=None`显式取消。
- 绘制插入会在cache失效、输入解码或创建依赖对象之前预检页面`/Contents`的raw array和引用chain。raw array上限为4,096个entry，chain为32层，最终array上限为4,096个stream引用（包括只添加一次的`q`/`Q` isolation pair）。失败时不修改文档。对页面的首次插入会将现有内容解码一次，并按hayro content parser的读法计数其中的`q`和`Q` operator，使isolation pair包含比内容中无匹配的`Q`多一个的`q`，以及比内容未闭合的`q`多一个的`Q`。与解释相同，计数不会在load时实施的上限之外重新应用解压上限，无法解码的stream按空内容计。
- `Page.insert_ocr_text_layer()`在超过4,096个非空word或UTF-8文本合计1 MiB时停止iterable materialization。core直接调用执行相同上限，CID分配在第65,535种不同字符之前停止，所有输入派生buffer都在PDF修改前准备好。
- open、authenticate、快速metadata probe与AES-256输出使用的密码在PyO3 copy或密码KDF之前限制为127 UTF-8 byte。Rust直接调用会再次检查；拒绝code为`password_input_size`，保存时的拒绝发生在文档修改或创建输出之前。

### 表单、批注与导航 { #structure-limits }

- AcroForm field tree读取会拒绝超过4,096个entry/node、8,192条edge、64层、encoded/decoded/returned名称或值合计1 MiB，或4,096个choice value item的部分结果。引用cycle只访问一次，继承值按每个返回leaf计入预算；填写会原子地执行相同的tree上限与1 MiB caller名称／值输入上限。caller输入会在font发现、button lookup或文件读取之前以`form_field_input_size`停止。
- 在修改接收文档之前，`insert_pdf()`会在1 MiB名称预算内比较顶层field名称；对部分范围，还会以相同的node、edge、widget与层数上限遍历源field tree。完整导入会直接复制field tree而不遍历。为共享而比较两个默认资源时最多读取4,096个对象与1 MiB；更大的组合会改名。
- AcroForm按钮field会拒绝超过4,096个widget、8,192个normal appearance state entry、4,096个唯一返回state名称或1 MiB encoded/returned state名称文本。填写会在修改前为缺失的`Off`与on state key计入预算。
- 批注与link读取会拒绝超过4,096个`/Annots` entry，或每次调用encoded/returned metadata文本合计1 MiB的部分结果。添加会在创建依赖对象和使cache失效之前检查相同的页面数量、生成的subtype加Contents/URI输入合计1 MiB，以及4,096个highlight矩形。caller文本会在PyO3 copy或矩形迭代之前以`annotation_input_size`拒绝；highlight迭代在第4,097项停止。
- named destination lookup只访问引用cycle一次；超过65,536个entry/node或131,072条edge时以`named_destination_count`拒绝，超过16 MiB key字节时以`named_destination_text_size`拒绝，超过32层时也会拒绝，而不会静默地将截断的tree报告为未解析。`get_outline()`、`Page.get_links()`、`insert_pdf()`的link导入，以及页面删除与选择时对name tree和旧式`/Dests`的改写共享这些上限，因此格式正确且可以解析的tree也能被清理和导入。改写会在1,048,576个direct object（每个entry 16个）和16 MiB文本的范围内复制保留的destination值。查找的destination名称计入正在遍历的结构的预算：书签计入TOC文本，name tree计入key文本，link计入每页1 MiB的link metadata。`Page.get_links()`在首次named lookup时为tree建立index，并在下次编辑前复用该index与page index，因此读取所有页面仍保持线性。保留的index在相同的entry与key文本上限内只保存每个不同key一次，被拒绝的tree绝不会被缓存。
- TOC读取使用迭代式outline walk，只访问引用cycle一次并释放GIL；超过65,536个node/entry或131,072条edge时以`toc_entry_count`拒绝部分结果，超过16 MiB source/returned文本时以`toc_text_size`拒绝，超过64层或32次destination间接引用时也会拒绝。写入会在PyO3 copy与修改之前，以`toc_entry_count`和`toc_input_size`检查相同的entry、深度和标题文本上限。页面删除与选择共享书签上限，因此写入的书签始终可以继续编辑。
- 页码标签number tree读取会拒绝超过4,096个entry/node、32层或encoded/decoded style/prefix文本合计1 MiB的部分结果。引用cycle只访问一次；写入会在PyO3 copy前以`page_label_input_size`执行相同的entry/文本上限。

### 元数据、附件与XMP { #metadata-limits }

- `Document.metadata`只解码8个标准Info字段，source或returned文本合计超过1 MiB时拒绝；自定义entry不会成为Python输出。`peek_metadata(max_file_size=)`可在解析前拒绝path或byte输入，并限制返回的标准文本；`None`保持输入不设上限的默认行为。写入会在PyO3 copy前以`metadata_input_size`预检合计1 MiB的source/encoded文本，并原子应用。
- `doc.attachments.add()`与`Attachment.write()`会在PyO3 copy前拒绝超过64 MiB的输入，`Attachment.read()`对每个解码filter层采用相同的默认上限。对于已知的大型附件可提高`max_size=`；`max_size=None`会显式接受不设上限的输入或materialization。附件name tree超过4,096个entry/node、32层或encoded/decoded名称合计1 MiB时会被拒绝。caller查找／删除名称以及添加时key/filename/description文本合计会在tree遍历或数据复制之前以1 MiB停止，并使用`embedded_file_input_size`。编辑会在clone之前检查inline FileSpec是否超过4,096个direct object、32层或1 MiB direct string/name/stream数据，并预检Catalog写入目标，而不是为rollback而clone整个文档。
- `Document.get_pdfa_claim()`默认将每个filter层的XMP解码大小限制为1 MiB。对于已知的大型packet可提高`max_size=`；`max_size=None`会显式接受不设上限的materialization。

### 保存文件 { #saving }

- `Document.tobytes()`对普通、object/xref stream及加密输出统一应用512 MiB默认serialize输出上限。其Rust writer会在转换为Python `bytes`之前拒绝越界write；`max_size=None`可显式取消。`save()`先流式写入目标目录中安全创建的sibling，仅在完整写入后原子替换请求路径，因此serialize或替换失败会保留现有文件。它不受此内存中输出预算的限制。即使后续I/O失败，`garbage`、`deflate`、`use_objstms`等保存选项仍保持已记录的mutation语义。
- `Pixmap.save()`把PNG编码直接流式写入目标目录中不可预测且排他创建的sibling，仅在完整写入成功后原子替换请求路径，不会在内存中再保留一份完成的PNG。替换失败会保留现有输出并删除临时文件。
- 在Windows上，两种文件保存操作都会保留现有普通文件的DACL和继承保护设置。在写入数据之前，会将访问限制应用到临时文件；如果无法复制安全描述符，则拒绝保存。失败时，只读或被锁定的目标保持不变，失败的临时输出会被删除。`Document.save()`在内部规范化长Windows路径，包括相对路径和Unicode名称，无需启用系统级长路径策略。

## 损坏与修复的文件 { #repaired-files }

宽松打开使用lopdf的cross-reference恢复。`startxref`、`/Prev`或`/XRefStm`的offset偏差在64 byte以内时，会移到附近的`xref`关键字。标准解析仍失败时，lopdf根据行首的object header和`/Root`存在的最新classic `trailer`重建table；后续revision的对象优先，但损坏区域中的对象可能缺失。已解析table的entry未指向catalog或page tree根节点时（例如在table之前丢失了字节的文件），同样以这种方式重建，并在重建后的table能到达page tree时采用它。没有classic trailer的损坏xref stream文件会被拒绝。

任一恢复都会发出`PylopdfWarning`，设置`doc.is_repaired`（以及metadata probe的`repaired`键），渲染与提取使用保存时以规范化xref数据写出的恢复后object graph。不能接受恢复输入的调用方可以拒绝`is_repaired`的文档。19 byte的xref entry、在无歧义对象边界修正的stream `/Length`等保留声明结构的宽松处理不会报告。`DocumentLimits`同样适用于恢复后的文档。预扫描会限制重建的成本；参见[cross-reference恢复扫描](#recovery-scan)。

## guard的工作方式 { #how-the-guards-work }

本节为需要精确推理其行为的读者说明上述code背后的固定guard。native与Pyodide CI共享同一hostile-input回归契约；定期Atheris fuzzing使用损坏xref、循环、深层对象、broken stream和压缩bomb作为seed。

### page tree walker { #page-tree-walker }

??? note "page tree索引详情"
    所有公开page indexing都使用同一个迭代page tree walker，包括page count与lookup、complexity、render snapshot、TOC与link、批注、Form导入以及页面结构编辑。它会拒绝重复使用的Page/Pages对象与cycle、超过32次引用的间接`/Kids` chain、超过256层的内部tree，以及超过间接对象数的edge数。`max_pages`遍历在第一个超限页面处停止。未配置页面策略的打开保持兼容，并将这些page tree检查延迟到第一次对页面进行索引的操作。

    page count与lookup复用一次walk得到的page index，因此访问所有页面仍是线性的。旋转、page box、绘制与批注编辑无法改变page tree，因此保留该index：每次编辑只在未使用的对象编号处添加对象，并保持walk读取过的每个entry不变，无法确认时则丢弃index。其他编辑都会丢弃它，并在下一次访问时重建。失败的walk绝不会被缓存，因此损坏的tree在每次访问时都会抛出异常。

### allocation失败 { #allocation-failures }

许多内部buffer采用fallible扩展：allocator拒绝分配内存时，pylopdf会抛出`PdfError`而不是中止，且不返回部分结果。例如，直接深度验证与complexity检查的迭代遍历stack采用fallible扩展，因此allocation被拒绝时会抛出`PdfError`，而不会暴露部分facts。上方各API的章节注明了适用的其他位置。

### filter chain { #filter-chains }

无法安全确定上限的filter chain会被拒绝，而不是乐观解码。pylopdf自有的有界lopdf解码路径还会在materialize filter列表之前拒绝超过16层连续的`/Filter`。

### 内容流嵌套 { #content-nesting }

hayro读取内容流中的array和dictionary时，读到64层便停止，并静默省略更深的operand。pylopdf将这种截断转换为以`content_nesting_depth`拒绝。

??? note "内容流检查读取的内容"
    在文本提取、布局、表格、搜索、绘图、图像读取、渲染（PNG、pixmap、SVG）、Markdown转换或图像压缩解释页面之前，pylopdf会解码该页面可达的内容流：其`/Contents`、资源中的Form XObject、批注和控件的appearance（check box和radio button的所有状态，hayro只绘制`/AS`选中的那一个）、soft-mask group、tiling pattern和Type 3 glyph procedure。当它们的array或dictionary嵌套超过64层、嵌入了stream对象，或隐藏了无法检查的inline image边界时，会以`content_nesting_depth`拒绝该页面。

    该上限固定不变，无论是否设置`DocumentLimits`都适用，其他页面仍可使用。每个页面在每个文档snapshot中只检查一次，编辑会清除结果。检查不会分析内容使用了哪些名称，因此即使没有operator绘制，页面资源dictionary中列出的条目也在检查范围内。

### colour space、shading与function { #resource-nesting }

hayro以不限深度的递归构建colour space、shading和function，因此足够深的链会耗尽栈并终止进程。pylopdf在hayro构建它们之前，以`resource_nesting_depth`拒绝这样的页面。这些上限限制的是栈的使用而不是时间：请继续在宿主中强制执行CPU deadline。

??? note "资源检查测量的内容"
    hayro构建colour space、shading和function时，使用与内容流同类的不限深度递归：Indexed或Pattern colour space由其base构建，Separation或DeviceN colour space由其alternate space和tint transform构建，ICCBased colour space在无法解析profile时由该profile的`/Alternate`构建，stitching function由`/Functions`的每个条目构建，Type 4 function则以递归方式解析其`{ }` procedure。约2万个互相引用的间接对象，或30万层嵌套的花括号，会耗尽栈并终止进程。

    在解释页面之前，pylopdf会测量该页面资源dictionary的`/ColorSpace`与`/Shading`条目、shading pattern、image XObject、soft-mask group，以及graphics state和soft mask的`/TR`与`/TR2`传递函数所能到达的对象；当colour space与function的链深度超过16层，或Type 4 function的procedure嵌套超过64层时，以`resource_nesting_depth`拒绝该页面。链的深度计入colour space的array、function和profile stream；hayro自身会切断环路，因此自引用或很短的环仍可渲染；互相可达的对象超过16个的组会被拒绝；共享的子图只测量一次，因此对象无论被多少个条目引用，检查都只读取一次。

    这些上限固定不变，无论是否设置`DocumentLimits`都适用，其他页面仍可使用。它们限制的是栈的使用而不是时间：对于仍在上限以内的图（每层两次引用同一个function、嵌套的Separation colour space等），hayro可能需要指数级CPU时间，因此请继续在宿主中强制执行CPU deadline。

### 对象嵌套深度 { #object-depth }

hayro不读取（连同容纳它的对象在内）嵌套超过64层的array或dictionary，并把整个对象视为不存在，而lopdf可读取至100层。若页面的dictionary连同从page tree继承的属性嵌套得更深，每当有操作解释该页面时都会以`object_depth`拒绝它，其他页面仍可使用。trailer、catalog或page tree的根节点嵌套到这种深度时，不会妨碍文档渲染：渲染和提取读取去掉过深条目的副本，而可编辑和保存的文档仍保留这些条目。其他嵌套到这种深度的对象会像在hayro中一样被视为不存在。`DocumentLimits.web()`在打开时就会以`object_depth`拒绝这类文档。

### structure tree清理 { #structure-tree }

页面删除与选择移除被丢弃页面的structure tree内容时，structure tree遍历会在达到1,048,576个元素与lookup tree节点、4,194,304个kid与entry、256层元素，或32层`/ParentTree`与`/IDTree`时，在文档修改之前以`structure_tree_count`停止。

### renderer panic { #renderer-panics }

hayro在某些畸形颜色数据上会panic，例如返回分量数不符的Separation或DeviceN tint transform，或Pattern colour space中的shading（[LaurenzV/hayro#1546](https://github.com/LaurenzV/hayro/issues/1546)）。所有让hayro解释页面的操作都会捕获这种panic，并抛出`PdfError`，而不是PyO3的`PanicException`（它派生自`BaseException`，会绕过`except Exception`）。renderer state会像编辑时一样被丢弃，因此之后的调用会从头开始，文档仍可继续使用。Rust仍会把panic消息写到标准错误输出。

### cross-reference恢复扫描 { #recovery-scan }

lopdf的重建会在每个后面没有`endstream`的`stream`关键字之后重新扫描剩余输入。在每次宽松打开和metadata probe之前，pylopdf以线性时间估算该工作量，超过每个输入byte 64 byte再加64 MiB时以`recovery_scan_size`拒绝，因此恶意输入无法使恢复变为二次复杂度。真实损坏文件在该位置最多只有一个被截断的stream。

## 依赖审计 { #dependency-auditing }

CI会在每次push时运行`cargo audit`，使用RustSec漏洞数据库审计Rust依赖树。

本政策在仓库中的正本为[`SECURITY.md`](https://github.com/yhay81/pylopdf/blob/main/SECURITY.md)。
