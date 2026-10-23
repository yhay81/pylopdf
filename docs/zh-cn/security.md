---
title: 安全
description: 受支持的版本、私密报告漏洞的方式，以及如何用pylopdf安全地处理不受信任的PDF。
---

# 安全

## 受支持的版本 { #supported-versions }

只有PyPI上的最新版本会获得安全修复。

## 报告漏洞 { #report-a-vulnerability }

请通过[GitHub Security Advisories](https://github.com/yhay81/pylopdf/security/advisories/new)私密报告漏洞，不要创建公开的issue。你应会在一周内收到初次回复。

## 处理不受信任的PDF { #untrusted-pdfs }

pylopdf由Rust编写，没有必需的Python依赖，但来源不明的PDF仍可能被刻意构造成消耗过多内存或时间。处理不受你控制的文件（例如用户上传的文件）时，请在打开时指定资源策略：

```python
import pylopdf

try:
    with pylopdf.open("upload.pdf", limits=pylopdf.DocumentLimits.web()) as doc:
        if doc.complexity["page_count"] > 50:
            reject_upload("too many pages")
        preview = doc.pages[0].pixmap(dpi=144)
except pylopdf.LimitError as error:
    reject_upload(error.code)
```

!!! warning "限制执行时间并隔离进程"

    上限只约束内存和输出大小，不会中止耗时过长的调用。请在worker、进程或容器中强制设置CPU时间或实际时间的期限，并尽可能在沙箱或容器中处理不受信任的文件。

`DocumentLimits.web()`是面向内存有限的Web服务器和队列worker的保守起点。它设置以下上限：

| 资源 | 上限 |
|---|---:|
| 文件大小 | 10 MiB |
| 页数 | 200 |
| 对象数 | 100,000 |
| 数组和字典的嵌套 | 64层 |
| 任意单个解码后的流（包括解码后的图像） | 64 MiB |
| 单个页面的内容流 | 10 MiB |
| 所有解码后的流合计 | 128 MiB |
| 提取的文本合计 | 1 MiB |
| 提取的字符（字形）合计 | 65,536 |
| 交给渲染器的PDF数据 | 64 MiB |

要修改某个上限，请构建自己的策略。所有参数都是可选的，`None`表示不设上限：

```python
limits = pylopdf.DocumentLimits(
    max_file_size=50 * 1024 * 1024,
    max_pages=1_000,
    max_decompressed_size=128 * 1024 * 1024,
)
```

| 参数 | 限制的内容 |
|---|---|
| `max_file_size` | 输入文件的大小 |
| `max_pages` | 页数 |
| `max_objects` | 对象数 |
| `max_object_depth` | 数组和字典的嵌套 |
| `max_decompressed_size` | 任意单个解码后的流的大小 |
| `max_page_content_size` | 单个页面解码后内容的大小 |
| `max_total_decompressed_size` | 所有解码后的流合计的大小 |
| `max_text_size` | 提取文本的UTF-8总大小 |
| `max_text_glyphs` | 提取字符的总数 |
| `max_interpretation_size` | 渲染和提取所读取的PDF数据的大小 |

`doc.complexity`无需解码任何内容，即可报告页数、对象数、流数、编码后的流大小和最深的嵌套层数。可以在进行重型处理之前，用它来分流或拒绝文档。

不带`limits`打开的文档在打开时没有任何上限，但下文所述的单次调用上限仍然适用。

### 单次调用的上限 { #default-limits }

读取或生成大量数据的方法有各自的上限，其默认值对大多数用途都是安全的。可用表中的关键字参数调高上限，或对可信的输入传入`None`取消上限。

| 方法 | 默认上限 | 关键字参数 |
|---|---|---|
| `Document.to_bytes()` | 输出512 MiB | `max_size` |
| `Document.render()` | PNG输出合计512 MiB | `max_size` |
| `markdown()`、`Table.markdown()` | 输出64 MiB | `max_size` |
| `Pixmap.to_bytes()`、`Page.svg()` | 输出64 MiB | `max_size` |
| `Page.search()` | 4,096个结果 | `max_hits` |
| `Page.content()` | 字形、路径与裁剪的线段、描边的虚线长度以及项合计262,144个 | `max_elements` |
| `Canvas.text()`、`textbox()`、`Font.text_width()`、`text_height()` | 文本1 MiB、4,096行 | `max_text_size` |
| `Font.from_file()`、`from_bytes()`以及传给表单的字体 | 每个字体64 MiB | `max_size`、`max_font_size` |
| `Page.insert_image()` | 输入64 MiB、6,400万像素 | `max_size`、`max_pixels` |
| `Page.replace_text()` | 页面内容64 MiB | `max_size` |
| 附件：`add()`、`read()`、`write()` | 每个文件64 MiB | `max_size` |
| `Stream.read()` | 64 MiB | `max_size` |
| `Document.xmp`、`pdfa_claim` | XMP元数据1 MiB | `doc.catalog["Metadata"]`的`Stream.read(max_size=)` |
| `OcrEngine()` | 模型文件64 MiB | `max_model_size` |

有几项上限是固定的。渲染时每页最多6,400万像素。密码最长127个UTF-8字节，这是PDF 2.0规定的最大值。传给`doc.pages.select()`、`insert()`和`extend()`，以及`doc.text()`、`doc.markdown()`和`doc.render()`的页面列表，条目数最多为页数与4,096中的较大者。书签、页码标签、表单字段、批注和附件列表都设有宽松的固定上限，足以应对大型的真实文档。

被拒绝的调用会在修改文档之前抛出异常，绝不会返回部分结果。

### 错误代码 { #error-codes }

`LimitError`是`PdfError`的子类。其`code`属性（也就是`error.args[0]`）指明达到的是哪项上限，并且在各版本之间保持稳定，因此程序可以据此处理。消息文本是给人看的，可能会变化。

| 代码 | 触发时机 |
|---|---|
| `file_size`、`page_count`、`object_count`、`object_depth` | 文档超出其`DocumentLimits`的结构上限 |
| `decompressed_size`、`page_content_size`、`total_decompressed_size`、`decompression_unverifiable`、`stream_filter_count` | 流解码后超出允许的大小，或使用了输出大小无法限定的过滤器（包括串联超过16个过滤器） |
| `text_size`、`text_glyph_count`、`interpretation_size` | 提取或渲染要读取的数据超出策略允许的范围 |
| `content_element_count` | 页面绘制的字形、线段、虚线长度和项多于`max_elements` |
| `recovery_scan_size` | 修复损坏文件所需的工作量过大 |
| `content_nesting_depth`、`resource_nesting_depth` | 页面的嵌套过深，解析它并不安全；参见[下文](#built-in-protections) |
| `pdf_output_size`、`render_output_size`、`markdown_output_size`、`svg_output_size`、`pixmap_output_size`、`replacement_output_size` | 输出会超过`max_size` |
| `search_input_size`、`search_hit_count` | 搜索文本超过4,096字节，或结果数超过`max_hits` |
| `text_input_size`、`text_line_count`、`font_input_size`、`path_point_count` | 传入用于绘制的文本、字体或图形路径过大 |
| `canvas_scope_depth` | Canvas 的作用域嵌套超过 16 层 |
| `tag_text_size` | 新文档中某个标签的文本或某个书签标题超过 64 KiB |
| `image_input_size`、`image_pixel_count`、`image_inventory_count` | 图像过大，或页面使用的图像超过4,096个 |
| `embedded_file_size`、`embedded_file_input_size` | 附件或其名称、描述过大 |
| `stream_data_size`、`object_value_depth` | 底层流过大无法读取，或写入的值嵌套过深 |
| `password_input_size`、`metadata_input_size`、`annotation_input_size`、`form_field_input_size`、`toc_input_size`、`page_label_input_size`、`replacement_input_size` | 传给方法的文本过大 |
| `page_entry_count` | 页面列表超出允许的长度 |
| `toc_entry_count`、`toc_text_size`、`named_destination_count`、`named_destination_text_size`、`structure_tree_count`、`form_content_size` | 书签、链接目标表、标签PDF结构或导入的页面过大 |
| `xmp_metadata_size` | XMP元数据大于1 MiB |
| `xmp_metadata_input_size` | 赋值给`Document.xmp`的XMP元数据大于1 MiB |
| `ocr_model_size`、`ocr_dictionary_entries` | OCR模型文件过大 |

## 内置防护 { #built-in-protections }

无论是否使用`DocumentLimits`，以下防护都适用于所有文档：

- **深层嵌套。**渲染引擎hayro以递归方式读取嵌套结构。在渲染或提取页面之前，pylopdf会检查其内容、颜色空间和函数的嵌套深度，并拒绝会导致栈溢出或被悄悄截断的页面（`content_nesting_depth`、`resource_nesting_depth`、`object_depth`）。文档中的其他页面仍可正常使用。
- **渲染器崩溃。**如果渲染器在遇到格式错误的数据时发生panic，pylopdf会抛出`PdfError`，而不是`except Exception`捕获不到的异常，文档也仍可继续使用。
- **损坏的文件。**pylopdf会尽可能修复损坏的交叉引用数据，发出`PylopdfWarning`，并设置`doc.is_repaired`。如果你的应用不能接受经过修复的输入，请拒绝这类文档。有些损坏的文件无法打开，会抛出`MalformedPdfError`。
- **安全保存。**`save()`和`Pixmap.save()`先写入目标位置旁边的临时文件，只有在完整写入后才替换目标文件，因此失败时绝不会留下写了一半的文件。现有的文件权限（包括Windows访问控制列表）会被保留。
- **不执行脚本。**嵌入的JavaScript永远不会被执行。

## 建议 { #recommendations }

- **在宿主环境中强制执行时间限制。**这些上限约束的是内存和输出大小，但有些看似正常的文档渲染起来需要很长时间。请在worker、进程或容器中为不受信任的任务设置CPU时间或实际时间的截止期限。
- **隔离批处理。**条件允许时，请在沙箱或容器中处理不受信任的文件。
- **不要依靠删除页面来实现涂黑。**删除页面会移除其链接、表单字段和结构条目，但其内容的副本可能仍残留在文件的其他位置，例如附件或元数据中。
- **使用`doc.render()`进行并行渲染。**它已经自行限制内存用量，因此不要再在外层套上不受限制的并行处理。

pylopdf的持续集成会在原生Python和WebAssembly上运行同一套恶意输入测试，定期运行的模糊测试还会向其输入格式错误的交叉引用表、引用循环、深层嵌套的对象、损坏的流和压缩炸弹。

## 依赖审计 { #dependency-auditing }

每次推送时，CI都会针对Rust依赖和RustSec安全公告数据库运行`cargo audit`。

本政策在仓库中的副本为[`SECURITY.md`](https://github.com/yhay81/pylopdf/blob/main/SECURITY.md)。
