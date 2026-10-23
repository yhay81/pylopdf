---
title: 生态系统方案
description: 经过测试的pylopdf与Typst、pyHanko、veraPDF、OCR引擎、NumPy及Pillow的协作方案。
---

# 生态系统方案

pylopdf不实现排版、PDF/A输出、数字签名或图像处理。这些工作已有成熟的库做得很好，下面的方案演示如何将它们与pylopdf结合使用。每个方案都由[`tests/test_interop.py`](https://github.com/yhay81/pylopdf/blob/main/tests/test_interop.py)和[`tests/test_image_recipes.py`](https://github.com/yhay81/pylopdf/blob/main/tests/test_image_recipes.py)中的集成测试覆盖。

## 用typst排版新文档 { #typesetting }

用[typst](https://typst.app/)编写文档，用[typst-py](https://pypi.org/project/typst/)编译，然后在pylopdf中打开结果：

```python
import typst
import pylopdf

pdf_bytes = typst.compile("report.typ")   # typst负责排版
doc = pylopdf.open(pdf_bytes)      # pylopdf负责编辑、合并和提取
```

## 用typst输出PDF/A { #new-pdfa }

typst可以输出PDF/A（第1至4部分）和PDF/UA-1文件：

```python
pdf_a: bytes = typst.compile("report.typ", pdf_standards="a-2b")
pylopdf.open(pdf_a).pdfa_claim   # PdfaClaim(part=2, conformance='B')
```

`Document.pdfa_claim`只读取文件声明的PDF/A级别。要检查现有PDF是否真正符合标准，请使用标准验证工具[veraPDF](https://verapdf.org/)（运行于Java）。

## 用typst设计水印和页眉 { #cjk-watermarks }

对于简单的文字，`canvas.text()`就足够了；安装`pylopdf[jp]`（或`[ko]`、`[zh-cn]`、`[zh-tw]`）后，它也能写入CJK文字。当印章需要真正的版面设计时，可以在typst中将其设计为一个页面，再用`canvas.page()`把该页面绘制到文档的每一页上：

```python
from pylopdf_fonts_jp import sans_path  # pip install pylopdf[jp]

stamp_typ = """
#set page(width: 595pt, height: 842pt, fill: none)
#set text(font: "Noto Sans JP", size: 48pt, fill: rgb(255, 0, 0, 40%))
#align(center + horizon)[社外秘]
"""
stamp = pylopdf.open(typst.compile(stamp_typ.encode(), font_paths=[str(sans_path().parent)]))
for page in doc.pages:
    with page.draw() as canvas:
        canvas.page(page.rect, stamp.pages[0])
```

印章仍然是真实的文本：之后`page.text()`能够找到`社外秘`。

## 用pyHanko进行数字签名 { #signatures }

[pyHanko](https://pypi.org/project/pyHanko/)（MIT）可创建PAdES签名。它把签名追加到文件末尾，因此pylopdf写出的字节保持不变：

```python
import io
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import signers

signer = signers.SimpleSigner.load("key.pem", "cert.pem")
out = signers.sign_pdf(
    IncrementalPdfFileWriter(io.BytesIO(doc.to_bytes())),
    signers.PdfSignatureMetadata(field_name="Signature1"),
    signer=signer,
)
signed_pdf: bytes = out.getvalue()
```

请把签名作为最后一步：之后再用pylopdf保存会重写文件，使签名失效。给`doc.form`中的签名字段赋值会引发异常。

## 使用其他OCR引擎 { #ocr }

pylopdf有[内置OCR](ocr.md)。如果想改用Tesseract或云服务等其他引擎，请将其识别出的单词以`OcrWord`记录的形式传给`Page.apply_ocr(words=...)`。pylopdf会把它们添加为不可见的文本层，使扫描件可被搜索，并让`markdown()`能够读取，同时几乎不增加文件大小。

```python
import pylopdf
from pylopdf import OcrWord, Rect

words = [  # from your OCR engine, in page coordinates
    OcrWord(text="Invoice", bbox=Rect(72, 90, 140, 104), confidence=0.98),
    OcrWord(text="#1024", bbox=Rect(146, 90, 190, 104), confidence=0.95),
]
with pylopdf.open("scan.pdf") as doc:
    doc.pages[0].apply_ocr(words=words)
    doc.save("searchable.pdf")
```

## 用NumPy和Pillow处理图像 { #image-processing }

`Pixmap`不可修改，也没有编辑方法：NumPy和Pillow早已擅长处理像素。`numpy.asarray(pix)`无需复制即可读取像素，灰度为`(height, width)`形状，其他为`(height, width, channels)`；`Pixmap.from_array(array, resolution=pix.resolution)`把结果变成新的pixmap。下面的函数取代pymupdf的`Pixmap`方法，每个都由直接运行本页代码的[`tests/test_image_recipes.py`](https://github.com/yhay81/pylopdf/blob/main/tests/test_image_recipes.py)测试。

```python
import numpy
from pylopdf import Pixmap


def samples(pix: Pixmap) -> numpy.ndarray:
    """Return a writable copy of the pixels, shaped (height, width, channels)."""
    return numpy.array(pix).reshape(pix.height, pix.width, pix.channels)


def color_channels(pix: Pixmap) -> int:
    """Return how many channels hold color: the alpha channel comes last."""
    return pix.channels - pix.alpha


def from_samples(array: numpy.ndarray, like: Pixmap) -> Pixmap:
    """Return a pixmap of the array, at the resolution of ``like``."""
    return Pixmap.from_array(numpy.ascontiguousarray(array), resolution=like.resolution)
```

### 读取和设置像素 { #image-pixels }

`pix.pixel(x, y)`、`set_pixel`、`set_rect`、`clear_with`和`copy`变为索引和切片赋值。矩形是以像素为单位的`IRect`或`(x0, y0, x1, y1)`元组。

```python
def pixel(pix: Pixmap, x: int, y: int) -> tuple[int, ...]:
    """Return the samples of one pixel, alpha included."""
    return tuple(int(value) for value in samples(pix)[y, x])


def fill(pix: Pixmap, rect, color: tuple[int, ...]) -> Pixmap:
    """Paint a rectangle with ``color``, as set_rect and clear_with do."""
    x0, y0, x1, y1 = rect
    array = samples(pix)
    array[y0:y1, x0:x1, : len(color)] = color
    return from_samples(array, pix)


def paste(target: Pixmap, source: Pixmap, rect) -> Pixmap:
    """Copy the pixels of ``source`` inside ``rect`` onto ``target``, as copy does."""
    x0, y0, x1, y1 = rect
    array = samples(target)
    array[y0:y1, x0:x1] = samples(source)[y0:y1, x0:x1]
    return from_samples(array, target)
```

### 着色、伽马和反相 { #image-color }

```python
def tint(pix: Pixmap, black: int, white: int) -> Pixmap:
    """Map 0 to the RGB integer ``black`` and 255 to ``white``, as tint_with does."""
    array = samples(pix)
    low = numpy.array([(black >> shift) & 255 for shift in (16, 8, 0)], dtype=float)
    high = numpy.array([(white >> shift) & 255 for shift in (16, 8, 0)], dtype=float)
    if color_channels(pix) == 1:  # gray tints with the green component
        low, high = low[1:2], high[1:2]
    color = array[..., : color_channels(pix)]
    array[..., : color_channels(pix)] = numpy.rint(low + (high - low) * color / 255)
    return from_samples(array, pix)


def gamma(pix: Pixmap, value: float) -> Pixmap:
    """Raise every color component to ``value``, as gamma_with does."""
    table = (255 * (numpy.arange(256) / 255) ** value).astype(numpy.uint8)
    array = samples(pix)
    array[..., : color_channels(pix)] = table[array[..., : color_channels(pix)]]
    return from_samples(array, pix)


def invert(pix: Pixmap, rect=None) -> Pixmap:
    """Invert the colors inside ``rect``, or everywhere, as invert_irect does."""
    x0, y0, x1, y1 = rect or (0, 0, pix.width, pix.height)
    array = samples(pix)
    region = array[y0:y1, x0:x1, : color_channels(pix)]
    array[y0:y1, x0:x1, : color_channels(pix)] = 255 - region
    return from_samples(array, pix)
```

### Alpha { #image-alpha }

```python
def with_alpha(pix: Pixmap, alpha) -> Pixmap:
    """Add or replace the alpha channel, as set_alpha does: a value or an array."""
    color = samples(pix)[..., : color_channels(pix)]
    plane = numpy.broadcast_to(numpy.asarray(alpha, dtype=numpy.uint8), color.shape[:2])
    return from_samples(numpy.dstack((color, plane)), pix)


def without_alpha(pix: Pixmap) -> Pixmap:
    """Drop the alpha channel, keeping the straight color samples."""
    return from_samples(samples(pix)[..., : color_channels(pix)], pix)
```

### 颜色统计 { #image-statistics }

```python
def color_counts(pix: Pixmap) -> dict[bytes, int]:
    """Count the pixels of each color, as color_count(colors=True) does."""
    colors = samples(pix)[..., : color_channels(pix)].reshape(-1, color_channels(pix))
    unique, counts = numpy.unique(colors, axis=0, return_counts=True)
    return {color.tobytes(): int(count) for color, count in zip(unique, counts)}


def top_color(pix: Pixmap) -> tuple[float, bytes]:
    """Return the share and the samples of the most used color, as color_topusage does."""
    counts = color_counts(pix)
    color = max(counts, key=counts.__getitem__)
    return counts[color] / (pix.width * pix.height), color


def is_unicolor(pix: Pixmap) -> bool:
    """Return whether every pixel has the same color."""
    return len(color_counts(pix)) == 1


def is_monochrome(pix: Pixmap) -> bool:
    """Return whether a gray pixmap holds only black and white."""
    return color_channels(pix) == 1 and set(color_counts(pix)) <= {b"\x00", b"\xff"}
```

`hashlib.md5(pix).digest()`取代`pix.digest`，`memoryview(pix).nbytes`取代`len(pix)`和`pix.size`，`IRect(0, 0, pix.width, pix.height)`取代`pix.irect`：pylopdf的pixmap没有原点。

### 旋转、缩小和变形 { #image-geometry }

```python
from PIL import Image

ROTATE_90, ROTATE_180, ROTATE_270 = -1, 2, 1  # quarter turns for numpy.rot90


def turned(pix: Pixmap, quarter_turns: int) -> Pixmap:
    """Turn the pixmap clockwise, as flip_rotate(Pixmap.ROTATE_90) does for -1."""
    return from_samples(numpy.rot90(samples(pix), quarter_turns), pix)


def flipped(pix: Pixmap, left_right: bool = True) -> Pixmap:
    """Mirror the pixmap, as FLIP_LEFT_RIGHT and FLIP_TOP_BOTTOM do."""
    return from_samples(numpy.flip(samples(pix), axis=1 if left_right else 0), pix)


def to_image(pix: Pixmap) -> Image.Image:
    """Return a Pillow image of the pixels."""
    mode = {1: "L", 2: "LA", 3: "RGB", 4: "RGBA"}[pix.channels]
    return Image.frombytes(mode, (pix.width, pix.height), bytes(pix))


def from_image(image: Image.Image, like: Pixmap) -> Pixmap:
    return from_samples(numpy.asarray(image).reshape(image.height, image.width, -1), like)


def shrunk(pix: Pixmap, factor: int) -> Pixmap:
    """Shrink each side by 2 ** factor, averaging blocks, as shrink does."""
    reduced = from_image(to_image(pix).reduce(2**factor), pix)
    x, y = pix.resolution
    return Pixmap.from_array(reduced, resolution=(max(1, x >> factor), max(1, y >> factor)))


def warped(pix: Pixmap, quad, width: int, height: int) -> Pixmap:
    """Map the quadrilateral ``quad`` (ul, ur, ll, lr) onto a width-by-height pixmap, as warp does."""
    (ul, ur, ll, lr) = quad
    corners = [(0, 0, *ul), (width, 0, *ur), (0, height, *ll), (width, height, *lr)]
    rows, targets = [], []
    for u, v, x, y in corners:  # solve for the output-to-input perspective coefficients
        rows += [[u, v, 1, 0, 0, 0, -u * x, -v * x], [0, 0, 0, u, v, 1, -u * y, -v * y]]
        targets += [x, y]
    coefficients = numpy.linalg.solve(numpy.array(rows, dtype=float), numpy.array(targets, dtype=float))
    image = to_image(pix).transform(
        (width, height), Image.Transform.PERSPECTIVE, tuple(coefficients), Image.Resampling.BILINEAR
    )
    return from_image(image, pix)
```

`to_image(pix).convert("CMYK")`得到CMYK样本，Pillow可以保存它支持的所有格式，例如TIFF和WebP；`from_image`可将这些格式的图像转回来。
