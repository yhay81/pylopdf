---
title: 安全
description: pylopdf支持版本、私密漏洞报告方式，以及处理不受信任PDF时的安全建议。
---

# 安全

只有PyPI上的最新版本会获得安全修复。

## 报告漏洞 { #report-a-vulnerability }

请通过[GitHub Security Advisories](https://github.com/yhay81/pylopdf/security/advisories/new)
私密报告漏洞，不要创建公开Issue。我们会争取在一周内首次响应。

## 处理不受信任的PDF { #untrusted-pdfs }

pylopdf由Rust编写且没有必需的Python依赖，但解析恶意PDF输入仍然存在固有风险。

!!! warning "使用完整的资源策略"
    请向`pylopdf.open()`传入`limits=pylopdf.DocumentLimits.web()`。
    该预设适合作为内存受限Web或queue worker处理用户上传内容的保守起点。

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

Web预设目前独立应用以下上限：

| 资源 | 上限 |
|---|---:|
| 输入文件 | 10 MiB |
| 页数 | 200 |
| 间接对象 | 100,000 |
| 单个解码流（包括图像RGBA估算） | 64 MiB |
| 单个页面内容流 | 10 MiB |
| 所有流累计解码或估算字节 | 128 MiB |
| 直接array/dictionary嵌套深度 | 64 |
| 已解释页面累计UTF-8 glyph payload | 1 MiB |

如需适配其他负载，可直接构造`DocumentLimits(...)`。每个非`None`值必须是正整数。
原有`max_decompressed_size=`仍可作为单流预算的兼容简写，但不能与`limits=`同时使用。

`LimitError`是`PdfError`的子类。稳定的`code`为`file_size`、`page_count`、
`object_count`、`object_depth`、`decompressed_size`、`page_content_size`、
`total_decompressed_size`、`text_size`、`embedded_file_size`、
`xmp_metadata_size`、`render_output_size`、`markdown_output_size`、
`svg_output_size`、`replacement_input_size`、`replacement_output_size`或
`pdf_output_size`、`image_input_size`、`image_pixel_count`、
`font_input_size`、`text_input_size`、`search_input_size`、`search_hit_count`、
`pixmap_output_size`、`ocr_model_size`、`ocr_dictionary_entries`、
`decompression_unverifiable`之一；
同一值也位于`error.args[0]`。无法安全估算上限的filter chain会被拒绝，而不是
乐观解码。

`doc.complexity`无需解码流或调用renderer，即可报告页数、对象数、流数、编码流
字节数以及直接对象最大深度，适合在重型提取前进行routing。结构和解压预算验证
打开时的source；生成结果若要跨越新的trust boundary，请用同一策略重新打开。

宽松打开只执行一种受限修复：仅当同一最终revision中存在完整classic xref table，
并且在原有上限下完整解析成功时，才替换错误的最终`startxref`。它不会扫描object
header、修复xref stream或回退到旧revision。修复会发出`PylopdfWarning`，令
`doc.is_repaired`（metadata probe中的`repaired`）为`True`；保存会规范化xref数据。

- 每页渲染上限为6400万像素。
- `Document.render_page()`、`Page.render()`与`Pixmap.tobytes()`的encoded PNG
  输出默认上限为64 MiB。writer会在返回Python `bytes`前拒绝越界write；
  `max_size=None`可显式取消。rendering使用`render_output_size`，
  Pixmap直接encode使用`pixmap_output_size`。
- `Document.tobytes()`对普通、object/xref stream及加密输出统一应用512 MiB默认
  serialization上限。Rust writer会在转换为Python `bytes`之前拒绝越界write；
  `max_size=None`可显式取消。`save()`先流式写入target同directory中安全创建的
  sibling，仅在完整写入后原子替换请求path，因此serialization或替换失败会保留
  现有file。它不受此in-memory预算限制。即使后续I/O失败，`garbage`、`deflate`、
  `object_streams`等save option仍保持已记录的mutation语义。
- `Pixmap.save()`把PNG encode直接流式写入target directory中不可预测且排他创建的
  sibling，仅在完整write成功后原子替换请求path，不会在内存中再保留一份完成PNG。
  替换失败会保留现有output并删除临时file。
- `Page.insert_image()`默认将encoded JPEG/PNG input限制为64 MiB，将decoded PNG
  input限制为64,000,000像素。filename通过释放GIL的Rust边界进行有上限读取，PNG
  dimension在分配decoded storage前检查。可信workload可用`max_size=None`／
  `max_pixels=None`显式取消。
- `insert_text`、`insert_textbox`、`set_form_field`与`set_fallback_font`的
  显式／自动OpenType input默认限制为64 MiB。buffer在PyO3 copy前拒绝，filename
  通过释放GIL的有界Rust path读取。可信workload可用`max_font_size=None`显式取消。
- `insert_text()`与`insert_textbox()`的生成文本输入默认限制为1 MiB UTF-8。
  Python在PyO3 copy前检查，Rust边界再次检查；textbox会在分配展开后的string前
  预检tab展开量。可信input可用`max_text_size=None`显式取消，拒绝code为
  `text_input_size`。
- `search_for()`将搜索词限制为4,096 UTF-8 byte，返回geometry默认限制为4,096项。
  Python在PyO3 copy前拒绝超限搜索词，Rust边界再次检查两项限制。可信结果集可用
  `max_hits=None`显式取消；拒绝code为`search_input_size`或
  `search_hit_count`，且不返回partial list。
- `render_page_svg()`和`Page.render_svg()`的UTF-8输出默认上限为64 MiB，在
  PyO3创建Python string前拒绝超限结果；`max_size=None`可显式取消。
  hayro-svg 0.7只返回完整`String`，因此pylopdf应用边界前的一份内部Rust string
  不受此限制。
- 绘图插入会在cache失效、输入decode或创建dependent object前检查page
  `/Contents`的raw array和引用chain。raw array上限为4,096个entry，chain深度为
  32，最终array上限为4,096个stream引用（包括只添加一次的`q`/`Q` isolation
  pair）。失败时不修改document。
- `Page.replace_text()`将search、replacement和fallback的合计限制为4,096个
  UTF-8 byte，并为解码page content、font encoding data、替换增长和最终stream
  设置64 MiB默认上限。它在commit前准备page专用stream，因此不会修改复制page的
  共享content；no-match/error会保留document和cache。可信输入可用
  `max_size=None`显式解除。
- `delete_pages()`、`select()`和`insert_pdf()`在Python与Rust中每次call最多接受
  4,096个page entry。iterable会在第4,097个item、graph修改前停止。空delete保留
  cache、generation及现有`Page` view。
- `Page.get_images()`会拒绝每页超过4,096个placement、累计64,000,000个source像素或
  64 MiB返回payload的部分结果。Flate-wrapped JPEG直通也只解压到剩余byte预算。
- `Document.embfile_add()`会在PyO3 copy前拒绝超过64 MiB的输入，
  `embfile_get()`对每个解码filter层采用相同默认上限。对于已知的大型附件可提高
  `max_size=`；`max_size=None`会显式接受无限制输入或materialization。附件name tree
  超过4,096个entry/node、32层或encoded/decoded名称合计1 MiB时也会拒绝。
  添加时key/filename/description输入合计上限为1 MiB。编辑会在clone inline
  FileSpec之前检查4,096个direct object、32层和1 MiB direct string/name/stream
  data上限，并预检Catalog写入目标，无需为rollback clone整个文档。
- `Document.get_pdfa_claim()`默认将每个filter层的XMP解码输出限制为1 MiB。
  对于已知的大型packet可提高`max_size=`；`max_size=None`会显式接受无限制
  materialization。
- `Page.insert_ocr_text_layer()`在超过4,096个非空word或UTF-8文本合计1 MiB时
  停止iterable materialization。core直接调用执行相同上限，在第65,535种CID分配前
  停止，并在PDF变更前准备所有输入派生buffer。
- 页码标签number tree会拒绝超过4,096个entry/node、32层或encoded/decoded
  style与prefix文本合计1 MiB的部分结果。引用cycle只访问一次，写入也执行相同的
  entry/text上限。
- AcroForm field tree会拒绝超过4,096个entry/node、8,192条edge、64层、1 MiB
  encoded/decoded/returned名称或值、或4,096个choice value item的部分结果。
  引用cycle只访问一次，继承值按每个返回leaf计入预算；填写也原子地执行相同的
  tree上限与1 MiB输入值上限。
- AcroForm button field会拒绝超过4,096个widget、8,192个normal appearance
  state entry、4,096个唯一返回state name或1 MiB encoded/returned state-name文本。
  填写会在修改前计入缺少的`Off`/on state key。
- 批注与link读取会拒绝超过4,096个`/Annots` entry或每次调用aggregate
  encoded/returned metadata文本1 MiB的部分结果。添加会在创建dependent object和
  失效cache之前检查相同的页面数量、生成subtype加Contents/URI输入合计1 MiB与
  4,096个highlight矩形。
- named destination lookup只访问引用cycle一次，并拒绝超过4,096个entry/node、
  8,192条edge、32层或1 MiB key byte的tree，而不会静默地将截断结果报告为未解析。
  `Page.get_links()`每次call只构建一个borrowed index，不会为每个named link重复遍历。
- TOC读取使用迭代式outline walk，只访问引用cycle一次并释放GIL；超过4,096个
  node/entry、8,192条edge、64层、32层destination间接引用或1 MiB source/returned
  文本时拒绝部分结果。写入也会在修改前检查entry、深度和title文本上限。
- `Document.metadata`只解码8个标准Info字段；aggregate source/returned文本超过
  1 MiB时会拒绝，custom entry不会materialize为Python输出。
  `peek_metadata(max_file_size=)`可在解析前拒绝path或byte input，并限制returned
  标准文本；输入默认不设上限。写入在修改前检查1 MiB source/encoded文本并原子应用。
- 嵌入JavaScript在设计上不受支持，也绝不会执行。
- `render_pages()`最多接受4,096个page entry，累计encoded PNG默认上限为512 MiB。
  并行结果共享一个atomic budget，失败时不返回部分list；`max_size=None`可显式
  取消。worker admission另行限制live raster/conversion buffer；不要在application
  层叠加无限并行。
- `Document.to_markdown()`最多接受4,096个page entry，累计UTF-8输出默认上限为
  64 MiB。heading size统计pass与render pass都只同时保留一页的interpreted
  layout、table和word。在组装page输出前，每个table都会获得剩余的累计预算。
  `Table.to_markdown()`默认使用同一上限，并预检包含合并单元格展开在内的转义后
  精确UTF-8大小。标题、段落、列表和表格在保留entry时计入预算，完整size获准后
  再线性组装page。超过上限时不返回部分string；`max_size=None`可显式取消。
- CPU deadline应由Worker、process或container宿主执行。资源预算限制已记录的
  allocation和输出增长，但不会按wall-clock时间中断正在运行的parser或interpreter。
- 批量处理不受信任的文件时，尽量在sandbox或container中运行。
  native与Pyodide CI共享同一hostile-input回归契约；定期Atheris fuzzing使用
  损坏xref、循环、深层对象、broken stream和压缩bomb作为seed。

## 依赖审计 { #dependency-auditing }

CI会在每次push时运行`cargo audit`，使用RustSec漏洞数据库审计Rust依赖树。

本政策在仓库中的正本为
[`SECURITY.md`](https://github.com/yhay81/pylopdf/blob/main/SECURITY.md)。
