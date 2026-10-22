---
title: 离线OCR
description: 使用pylopdf内置的OCR引擎在本地识别扫描PDF中的文本，或添加其他任意OCR引擎的识别结果。
---

# 离线OCR

pylopdf可以识别扫描页面中的文本，并将其添加为不可见的文本层，使PDF可被搜索，并让`text()`和`markdown()`能够读取。请安装模型包：

```bash
pip install "pylopdf[ocr]"
```

识别在本地由pylopdf自己的引擎完成，使用PP-OCRv6 small模型。它不需要网络连接，不需要Tesseract之类的系统程序，也不需要其他Python包。模型包约27 MB，支持包括英语、日语、简体中文和繁体中文在内的50种语言。

[WebAssembly](wasm.md)版本不提供OCR。

## 只识别、不编辑 { #recognize }

`Page.get_text_ocr()`返回识别到的单词，不修改文档：

```python
import pylopdf

with pylopdf.open("scan.pdf") as doc:
    for word in doc.pages[0].get_text_ocr():
        print(word["bbox"], word["text"], word["confidence"])
```

每个`OcrWord`都有一个`bbox`，使用与文本提取和渲染相同的页面坐标。`confidence`可用于排序和筛选，但并非经过校准的概率。

## 使扫描件可搜索 { #searchable }

创建一个引擎，并在所有页面上复用：

```python
import pylopdf

engine = pylopdf.OcrEngine()
with pylopdf.open("scan.pdf") as doc:
    for page in doc.pages:
        page.apply_ocr(engine=engine)
    doc.save("searchable.pdf", dedupe=True, object_streams=True)
```

`apply_ocr()`不会改变页面的外观。它会跳过已有文本的页面，因此同一脚本运行两次也不会重复添加文本。对于既有排版文字又有扫描图像的页面，请用`clip=(x0, y0, x1, y1)`框出扫描区域：此时只有该区域内的文本才算作已有文本。如需无论如何都添加OCR文本，请传入`skip_existing=False`。

## 使用其他OCR引擎 { #other-engines }

`Page.insert_ocr_text_layer(words)`可添加任意OCR引擎或云服务识别出的单词。每个单词为页面坐标中的`(x0, y0, x1, y1, text)`；`text`之后的多余项会被忽略：

```python
words = [(72, 90, 140, 104, "Invoice"), (146, 90, 190, 104, "#1024")]
doc.pages[0].insert_ocr_text_layer(words)
```

该文本层不使用嵌入字体，因此几乎不会增加文件大小。

## 旋转的扫描件 { #rotation }

如果页面是横着或倒着扫描的，请传入`rotation=90`、`180`或`270`，在识别前将图像顺时针旋转：

```python
words = page.get_text_ocr(rotation=270)
page.apply_ocr(rotation=270)
```

页面本身不会被旋转，返回的框仍使用页面的坐标。不可见文本会沿识别出的阅读方向排列，因此保存后搜索和提取都能正常工作。pylopdf不会自动检测旋转角度，也不会校正轻微倾斜的扫描件。

## 内存与速度 { #resources }

默认情况下，页面以300 dpi渲染，并按1,408像素、彼此重叠的图块进行识别。在我们的测量中，以此设置处理一页A4时，内存峰值约为420 MiB。要减少内存使用，请调低`threads`和`tile_size`：

```python
engine = pylopdf.OcrEngine(threads=2)
words = page.get_text_ocr(engine=engine, tile_size=1280, min_confidence=0.6)
```

`clip=`也能节省时间和内存，因为只会识别所选区域。

`OcrEngine`可以在多个线程和多个文档之间共享。默认情况下它一次只执行一个识别（`max_concurrent=1`），因此共享不会使内存用量成倍增加。`max_concurrent`最多可调到16，但请先测量：在我们的测试中，同时运行两个并没有更快。`Document`本身仍然必须一次只由一个线程使用；参见[并发](concurrency.md)。

## 准确率 { #accuracy }

下表是两份已知正确文本的日文测试文档上的字符错误率（CER，越低越好）。“Strict”只忽略空白；“NFKC”还将全角和半角形式视为相同。

| 文档 | DPI | Strict CER | NFKC CER | 耗时 |
|---|---:|---:|---:|---:|
| 政府报告（电子文档，1,188字） | 150 | 3.788% | 0.842% | 5.50 s |
| 政府报告（电子文档，1,188字） | 300 | 3.704% | 0.842% | 13.87 s |
| 档案扫描件（384字） | 150 | 1.823% | 1.562% | 2.05 s |
| 档案扫描件（384字） | 300 | 1.302% | 1.042% | 5.37 s |

作为对比，RapidOCR v6参考实现在150 dpi和300 dpi下对政府报告的NFKC CER分别为0.926%和0.758%：pylopdf在150 dpi下更准确，在300 dpi下则略逊一筹。耗时取决于硬件。在仓库中运行`uv run python bench/ocr.py`即可复现该报告。

## 限制 { #limitations }

- 单词框是与坐标轴对齐的矩形。
- 不解析注音（振假名）、夹注（warichu）以及横竖混排的文本。
- 自定义模型必须是转换为RTen格式的PP-OCR模型；请将其路径传给`OcrEngine(detector_path, recognizer_path, dictionary_path)`。

`pylopdf-ocr-models`包说明了模型的来源、哈希值以及其Apache-2.0许可证。
