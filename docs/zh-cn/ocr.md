# 离线 OCR

pylopdf 可以在本地识别扫描页面，并添加不可见且可搜索的文本层。请安装可选模型包：

```bash
pip install "pylopdf[ocr]"
```

核心扩展通过纯 Rust 的 RTen 运行时执行 PP-OCRv6 small。运行时不需要系统可执行文件、共享库、网络请求或 ONNX 解析器。独立版本管理的模型 wheel 约为 26.6 MB，支持包括日语、简体中文、繁体中文和英语在内的 50 种语言。

## 识别而不编辑

`Page.get_text_ocr()` 返回带位置的单词，而不修改文档：

```python
import pylopdf

with pylopdf.open("scan.pdf") as doc:
    words = doc[0].get_text_ocr()
    for word in words:
        print(word["bbox"], word["text"], word["confidence"])
```

每个 `OcrWord` 都包含一个 `Rect`，使用与渲染和提取相同的、已解析旋转且左上角为原点的显示坐标。`confidence` 是用于结果排序的确定性识别指标，并非经过校准的概率。

## 使扫描件可搜索

加载一个引擎并在多个页面间复用：

```python
import pylopdf

engine = pylopdf.OcrEngine(threads=4)
with pylopdf.open("scan.pdf") as doc:
    for page in doc:
        page.apply_ocr(engine=engine)
    doc.save("searchable.pdf", garbage=3, deflate=True, object_streams=True)
```

`apply_ocr()` 会保留渲染像素和现有页面内容。默认情况下，它会跳过已有可提取文本的页面，因此重新运行流程不会重复添加不可见文本层。对于混合内容页面，可用显示坐标 `clip=(x0, y0, x1, y1)` 选择扫描区域；只有与该区域相交的现有文本才会触发跳过。仅在需要无视相交文本继续追加时使用 `skip_existing=False`。

## 资源控制

默认值为 300 dpi、1,408 像素的检测分块、192 像素重叠，以及最多四个 RTen 工作线程。重叠分块会合并边缘的重复检测，同时限制整页检测所需的内存。在一次 300 dpi A4 实测中，默认配置的峰值接近 419 MiB；实际值会随文档、平台、内存分配器和并发程度变化。

内存较紧张时可降低 `threads` 和 `tile_size`：

```python
engine = pylopdf.OcrEngine(threads=2)
words = page.get_text_ocr(
    engine=engine,
    tile_size=1280,
    overlap=192,
    min_confidence=0.6,
)
```

`clip` 会减少 OCR 检测器输入和识别工作，但 hayro 0.7 仍会在裁剪前渲染完整页面。返回的文本框继续使用整页显示坐标。

`OcrEngine` 是不可变的，可以在不同文档间复用。每个并发识别调用仍拥有独立的光栅和推理缓冲区，因此应限制外层并发。同一个 `Document` 上来自外部线程的并发调用或编辑不在 pylopdf 的并发契约内。

## 实测准确率门槛

已跟踪且可再分发的日本厚生劳动省测试文件提供了1,188个提取出的标准字符。原生流程的测量结果如下：

| DPI | 严格CER | NFKC CER | 用时 |
|---:|---:|---:|---:|
| 150 | 3.788% | 0.842% | 5.71秒 |
| 300 | 3.704% | 0.842% | 11.93秒 |

RapidOCR v6参考实现的NFKC CER分别为0.926%和0.758%，因此报告同时保留了pylopdf在150 dpi下的胜出和300 dpi下的落后。严格CER只移除空白；NFKC CER还会折叠全角拉丁字符等兼容形式。时间结果取决于硬件。运行`uv run python bench/ocr.py`可复现完整报告。

## 模型与版面边界

未指定路径时，`OcrEngine` 会发现由 `pylopdf[ocr]` 安装的已验证模型集。高级用户也可以显式传入兼容 RTen 格式的 PP-OCR 检测器、识别器和字典。

首个原生引擎返回轴对齐的单词框。它尚不支持任意角度纠偏、自动识别横置页面，也不解释注音、双行小注或混合方向排版。扫描页横置时，请在 OCR 前显式设置页面旋转。PP-OCRv6 模型的来源、源文件和产物哈希、转换命令及 Apache-2.0 声明均包含在 `pylopdf-ocr-models` 发行包中。
