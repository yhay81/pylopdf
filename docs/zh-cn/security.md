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
| 传给rendering／extraction的完整PDF snapshot | 64 MiB |
| 已解释页面累计带位置glyph record | 65,536 |

如需适配其他负载，可直接构造`DocumentLimits(...)`。每个非`None`值必须是正整数。
原有`max_decompressed_size=`仍可作为单流预算的兼容简写，但不能与`limits=`同时使用。

所有公开page indexing都使用同一个迭代page-tree walker，包括page count／lookup、
complexity、render snapshot、TOC／link、annotation、Form import与页面结构编辑。
它会拒绝重复使用的Page／Pages对象与cycle、超过32次引用的间接`/Kids` chain、
超过256层的内部tree，以及超过间接对象数的edge。`max_pages`遇到第一个超限页面时
停止。未配置页面策略的open保持兼容，并将这些page-tree检查延迟到第一次
page-indexing操作。page count／lookup会复用一次walk得到的page index，因此访问
所有页面仍是线性的。旋转、page box、绘制与annotation编辑不会改变page tree，因此
保留该index：每次编辑只在未使用的对象编号处添加对象，并保持walk读取过的entry不变，
无法确认时则丢弃index。其他编辑会丢弃它，并在下一次访问时重建。失败的walk不会被
缓存，因此损坏的tree在每次访问时都会抛出异常。

page batch根据一次获取的页数校验entry。`pages=None`与`insert_pdf()`的完整范围
无论页数多少都覆盖所有页。显式sequence可以重复页面，但在超过页数与4,096中较大者
的第一个entry处停止materialization并返回`page_entry_count`，因此重复也不会超过
处理整个文档一次的成本。文本提取与渲染每4,096个entry重建一次call级hayro cache，
而`max_size`、`max_text_size`等输出预算作用于整个call。直接调用core API时也会
重复entry检查，core页面导入会在修改前拒绝重复的源页面。

`LimitError`是`PdfError`的子类。稳定的`code`为`file_size`、`page_count`、
`object_count`、`object_depth`、`decompressed_size`、`page_content_size`、
`total_decompressed_size`、`text_size`、`text_glyph_count`、`interpretation_size`、`embedded_file_size`、
`embedded_file_input_size`、`form_field_input_size`、`form_content_size`、
`annotation_input_size`、
`metadata_input_size`、`toc_input_size`、`toc_entry_count`、`toc_text_size`、
`named_destination_count`、`named_destination_text_size`、
`page_entry_count`、`page_label_input_size`、
`xmp_metadata_size`、`render_output_size`、`markdown_output_size`、
`svg_output_size`、`replacement_input_size`、`replacement_output_size`或
`pdf_output_size`、`image_input_size`、`image_pixel_count`、
`font_input_size`、`text_input_size`、`text_line_count`、`batch_page_count`、`path_point_count`、`search_input_size`、`search_hit_count`、
`password_input_size`、`pixmap_output_size`、`ocr_model_size`、`ocr_dictionary_entries`、
`stream_filter_count`、`decompression_unverifiable`、`recovery_scan_size`、
`content_nesting_depth`、`resource_nesting_depth`之一；
同一值也位于`error.args[0]`。无法安全估算上限的filter chain会被拒绝，而不是
乐观解码。pylopdf自有的有界lopdf解码路径还会在materialize filter列表前拒绝
超过16层的`/Filter` chain。

`doc.complexity`无需解码流或调用renderer，即可报告页数、对象数、流数、编码流
字节数以及直接对象最大深度，适合在重型提取前进行routing。结构和解压预算验证
打开时的source；生成结果若要跨越新的trust boundary，请用同一策略重新打开。
直接深度验证与complexity检查的迭代遍历stack采用fallible扩展，因此allocation
refusal会抛出`PdfError`，而不会暴露部分facts。

`max_interpretation_size`在hayro首次读取保留input，以及pylopdf在编辑、解密或
AcroForm state选择后serialize当前状态时生效。有界writer会拒绝越界write，且不会
安装不完整的renderer／extractor cache。为保持兼容，默认值为`None`；
`DocumentLimits.web()`使用64 MiB。

设置`max_text_size`后，plain-text提取会预检组装后精确的UTF-8大小，并将一次提取
call限制为glyph payload预算的两倍。每个非空glyph至少贡献一个payload字节，而推断
gap与换行的总数不会超过glyph数。该输出上限作用于整个call，而不是内部的4,096个
entry chunk；显式sequence最多接受页数与4,096中较大者的entry，因此重复页码无法
绕过策略。拒绝code仍为`text_size`；为保持兼容，默认值仍为`None`。

`max_text_glyphs`限制line组装前保留的record数，因此也限制结构化文本可materialize的
block、line、span和word数量。同一页的文本与表格解释共享一次累计admission，被拒绝
的页面不消耗预算。为保持兼容，默认值为`None`；`DocumentLimits.web()`使用65,536。

宽松打开使用lopdf的cross-reference恢复。`startxref`、`/Prev`或`/XRefStm`的offset
偏差在64 byte以内时，会移到附近的`xref`关键字。标准解析仍失败时，lopdf根据行首的
object header和`/Root`存在的最新classic `trailer`重建table；后续revision的object
优先，但损坏区域中的object可能缺失。没有classic trailer的损坏xref stream文件会被
拒绝。任一恢复都会发出`PylopdfWarning`，令`doc.is_repaired`（metadata probe中的
`repaired`）为`True`；渲染与提取使用保存时写出的恢复后object graph，保存会规范化
xref数据。不能接受恢复输入的调用方可以拒绝`is_repaired`的文档。19 byte的xref
entry、在无歧义object边界修正的stream `/Length`等保留声明结构的宽松处理不会报告。
`DocumentLimits`同样适用于恢复后的文档。

lopdf的重建会在每个后面没有`endstream`的`stream`关键字之后重新扫描剩余输入。
pylopdf在每次宽松打开和metadata probe之前以线性时间估算该工作量，超过每个输入
byte 64 byte再加64 MiB时以`recovery_scan_size`拒绝，防止恶意输入使恢复变为二次
复杂度。真实损坏文件在该位置最多只有一个被截断的stream。

hayro 0.7以不限深度的递归读取内容流中的array和dictionary，因此只要有一个流打开数十万个
`[`，就会在不抛出异常的情况下耗尽栈并终止Python进程。pylopdf在文本提取、布局、表格、
搜索、绘图、图像读取、渲染（PNG、pixmap、SVG）、Markdown转换和图像压缩解释页面之前，
会先解码该页面可达的内容流（`/Contents`、资源中的Form XObject、注释和控件的
appearance、soft-mask group、tiling pattern和Type 3 glyph procedure）。当array或
dictionary嵌套超过64层、嵌入了stream对象，或存在无法检查的inline image边界时，会以
`content_nesting_depth`拒绝该页面。该上限固定不变，无论是否设置`DocumentLimits`都适用，
其他页面仍可使用。每个页面在每个文档snapshot中只检查一次，编辑会清除结果。hayro会沿
所有外层dictionary解析名称，因此即使没有operator绘制，页面资源dictionary中列出的条目
也在检查范围内。

hayro构建colour space、shading和function时同样使用不限深度的递归（hayro 0.7与0.8都是如此）：
Indexed或Pattern colour space由其base构建，Separation或DeviceN colour space由其
alternate space和tint transform构建，ICCBased colour space在无法解析profile时由该
profile的`/Alternate`构建，stitching function（type 3）由`/Functions`的每个条目构建，
Type 4 function则以递归方式解析`{ }` procedure。约2万个互相引用的indirect object，
或30万层嵌套的花括号，会耗尽栈并终止进程。pylopdf在解释页面之前，会测量该页面资源
dictionary的`/ColorSpace`与`/Shading`条目、shading pattern、image XObject、soft-mask
group，以及graphics state和soft mask的传递函数（`/TR`、`/TR2`）所能到达的对象；当
colour space与function的链深度超过32层，或Type 4 function的procedure嵌套超过64层时，
以`resource_nesting_depth`拒绝该页面。链的深度计入colour space的array、function和
profile stream；hayro自身会切断环路，因此自引用或很短的环仍可渲染，但互相可达的对象超过32个
的环会被拒绝；共享的子图只测量一次，对象无论被多少个条目引用都只读取一次。这些上限固定不变，无论是否设置`DocumentLimits`都适用，
其他页面仍可使用。它们限制的是栈的使用而不是时间：对于仍在上限以内但经过构造的图
（每层两次引用同一个function、嵌套Separation等），hayro可能需要指数级CPU时间，因此
仍需在宿主中强制执行CPU deadline。

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
- `insert_text()`与`insert_textbox()`的生成文本输入默认限制为1 MiB UTF-8，
  物理行与换行后layout限制为4,096行。Python在PyO3 copy前检查物理行，Rust边界
  再次检查并在mutation前停止换行layout；textbox会在分配展开后的string前预检tab
  展开量。可信插入input可用`max_text_size=None`显式取消，拒绝code为
  `text_input_size`或`text_line_count`。AcroForm文本／选项外观保留固定4,096行上限。
  UAX #14／grapheme index、行collection与保留的行text均采用fallible扩展，因此
  allocation refusal不会暴露部分layout或document编辑。pylopdf自有的规范化、
  grapheme引用、shaped line／glyph collection、WinAnsi输出与输入派生的text operator
  扩展也采用fallible处理。Standard 14宽度检查不会分配encoded text copy。
  allocation refusal返回`PdfError`；插入保持不修改文档，form fill则原子rollback。
- `search_for()`将搜索词限制为4,096 UTF-8 byte，返回geometry默认限制为4,096项。
  Python在PyO3 copy前拒绝超限搜索词，Rust边界再次检查两项限制。可信结果集可用
  `max_hits=None`显式取消；拒绝code为`search_input_size`或
  `search_hit_count`，且不返回partial list。
- open、authenticate、快速metadata probe与AES-256输出使用的password在PyO3 copy或
  password KDF前限制为127 UTF-8 byte。Rust直接调用会再次检查；拒绝code为
  `password_input_size`，保存拒绝发生在document mutation或创建output之前。
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
  共享content；no-match/error会保留document和cache。caller text会在PyO3 copy前
  逐步计数，而不创建完整encoded copy。可信输入可用
  `max_size=None`显式解除。
- `delete_pages()`和`select()`在Python与Rust中每次call最多接受页数与4,096中
  较大者的entry，因此重复页面也不会使选择结果大于文档。iterable会在超过上限的
  第一个entry、graph修改前停止，并返回`page_entry_count`。`insert_pdf()`接受源
  文档的完整范围。空delete保留cache、generation及现有`Page` view。
- `Page.get_images()`会拒绝每页超过4,096个placement、累计64,000,000个source像素或
  64 MiB返回payload的部分结果。Flate-wrapped JPEG直通也只解压到剩余byte预算。
- `Document.embfile_add()`会在PyO3 copy前拒绝超过64 MiB的输入，
  `embfile_get()`对每个解码filter层采用相同默认上限。对于已知的大型附件可提高
  `max_size=`；`max_size=None`会显式接受无限制输入或materialization。附件name tree
  超过4,096个entry/node、32层或encoded/decoded名称合计1 MiB时也会拒绝。
  caller查找／删除名称与添加时key/filename/description输入会在tree遍历或data copy前
  以1 MiB停止，并使用`embedded_file_input_size`。编辑会在clone inline
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
  entry/text上限，并在PyO3 copy前使用`page_label_input_size`。
- AcroForm field tree会拒绝超过4,096个entry/node、8,192条edge、64层、1 MiB
  encoded/decoded/returned名称或值、或4,096个choice value item的部分结果。
  引用cycle只访问一次，继承值按每个返回leaf计入预算；填写也原子地执行相同的
  tree上限与1 MiB caller名称／值上限。caller输入会在font发现、button lookup或
  file读取前以`form_field_input_size`拒绝。
- AcroForm button field会拒绝超过4,096个widget、8,192个normal appearance
  state entry、4,096个唯一返回state name或1 MiB encoded/returned state-name文本。
  填写会在修改前计入缺少的`Off`/on state key。
- 批注与link读取会拒绝超过4,096个`/Annots` entry或每次调用aggregate
  encoded/returned metadata文本1 MiB的部分结果。添加会在创建dependent object和
  失效cache之前检查相同的页面数量、生成subtype加Contents/URI输入合计1 MiB与
  4,096个highlight矩形。caller text会在PyO3 copy或rectangle iteration前以
  `annotation_input_size`拒绝，highlight iteration在第4,097个item停止。
- named destination lookup只访问引用cycle一次；超过65,536个entry/node或131,072条
  edge时以`named_destination_count`拒绝，超过16 MiB key byte时以
  `named_destination_text_size`拒绝，并限制为32层，而不会静默地将截断结果报告为
  未解析。`get_toc()`、`Page.get_links()`、`insert_pdf()`的link导入，以及页面删除与
  选择时对name tree和旧式`/Dests`的改写共享这些上限，因此格式正确且可以解析的
  tree也能被清理和导入。改写会在1,048,576个direct object（每个entry 16个）和
  16 MiB文本的范围内copy保留的destination值。查找的destination名计入正在遍历的
  结构的预算：outline计入TOC文本，name tree计入key文本，link计入每页1 MiB的
  link metadata。`Page.get_links()`在首次named lookup时只为tree建立一次index，
  并在下次编辑前复用该index与page index，因此读取所有页面的link仍保持线性时间。
  保留的index在相同的entry与key text上限内只保存每个不同的key一次，被拒绝的
  tree不会被cache。
- TOC读取使用迭代式outline walk，只访问引用cycle一次并释放GIL；超过65,536个
  node/entry或131,072条edge时以`toc_entry_count`拒绝部分结果，超过16 MiB
  source/returned文本时以`toc_text_size`拒绝，并限制64层与32层destination间接
  引用。写入会在PyO3 copy与修改前以`toc_entry_count`和`toc_input_size`检查相同的
  entry、深度和title文本上限。页面删除与选择共享outline上限，因此写入的outline
  始终可以继续编辑。
- `Document.metadata`只解码8个标准Info字段；aggregate source/returned文本超过
  1 MiB时会拒绝，custom entry不会materialize为Python输出。
  `peek_metadata(max_file_size=)`可在解析前拒绝path或byte input，并限制returned
  标准文本；输入默认不设上限。写入在PyO3 copy前以`metadata_input_size`检查
  1 MiB source/encoded文本并原子应用。
- 嵌入JavaScript在设计上不受支持，也绝不会执行。
- `render_pages()`默认渲染所有页，显式entry最多为页数与4,096中较大者，累计
  encoded PNG默认上限为512 MiB。并行结果共享一个atomic budget，失败时不返回
  部分list；`max_size=None`可显式取消。worker admission另行限制live raster/
  conversion buffer，且每个worker每4,096页重建一次hayro cache；不要在application
  层叠加无限并行。
- `Document.to_markdown()`默认转换所有页，显式entry最多为页数与4,096中较大者，
  累计UTF-8输出默认上限为64 MiB。heading size统计pass与render pass都只同时保留一页的interpreted
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
