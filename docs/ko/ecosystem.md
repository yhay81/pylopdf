---
title: 생태계 연동
description: pylopdf를 Typst, pyHanko, veraPDF, OCR 엔진, NumPy, Pillow와 연결하는 테스트된 레시피입니다.
---

# 생태계 연동

pylopdf는 조판, PDF/A 출력, 디지털 서명, 이미지 처리를 구현하지 않습니다. 이미 잘 만들어진 라이브러리가 이런 작업을 잘 처리하며, 아래 레시피는 그 라이브러리를 pylopdf와 함께 쓰는 방법을 보여 줍니다. 각 레시피는 [`tests/test_interop.py`](https://github.com/yhay81/pylopdf/blob/main/tests/test_interop.py)와 [`tests/test_image_recipes.py`](https://github.com/yhay81/pylopdf/blob/main/tests/test_image_recipes.py)의 통합 테스트로 검증합니다.

## typst로 새 문서 조판하기 { #typesetting }

[typst](https://typst.app/)로 문서를 작성하고 [typst-py](https://pypi.org/project/typst/)로 컴파일한 뒤 결과를 pylopdf로 엽니다.

```python
import typst
import pylopdf

pdf_bytes = typst.compile("report.typ")   # typst가 문서를 조판
doc = pylopdf.open(pdf_bytes)      # pylopdf가 편집, 병합, 추출
```

## typst로 PDF/A 출력하기 { #new-pdfa }

typst는 PDF/A(파트 1–4)와 PDF/UA-1 파일을 쓸 수 있습니다.

```python
pdf_a: bytes = typst.compile("report.typ", pdf_standards="a-2b")
pylopdf.open(pdf_a).pdfa_claim   # PdfaClaim(part=2, conformance='B')
```

`Document.pdfa_claim`은 파일이 어떤 PDF/A 수준을 선언하는지만 읽습니다. 기존 PDF가 실제로 적합한지 확인하려면 표준 검증기인 [veraPDF](https://verapdf.org/)(Java에서 실행)를 사용하세요.

## typst로 디자인한 워터마크와 머리글 { #cjk-watermarks }

단순한 텍스트라면 `canvas.text()`로 충분합니다. `pylopdf[jp]`(또는 `[ko]`, `[zh-cn]`, `[zh-tw]`)를 설치하면 CJK 텍스트도 씁니다. 스탬프에 제대로 된 레이아웃이 필요하다면 typst에서 한 페이지로 디자인하고, `canvas.page()`로 문서의 모든 페이지에 그 페이지를 그리세요.

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

스탬프는 실제 텍스트로 남으므로, 그 뒤에도 `page.text()`가 `社外秘`를 찾습니다.

## pyHanko로 디지털 서명하기 { #signatures }

[pyHanko](https://pypi.org/project/pyHanko/)(MIT)는 PAdES 서명을 만듭니다. 서명을 파일 끝에 덧붙이므로 pylopdf가 쓴 바이트는 바뀌지 않습니다.

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

서명은 마지막 단계에서 하세요. 그 뒤에 pylopdf로 저장하면 파일을 다시 쓰므로 서명이 무효가 됩니다. `doc.form`의 서명 필드에 값을 대입하면 예외가 발생합니다.

## 다른 엔진으로 OCR하기 { #ocr }

pylopdf에는 [내장 OCR](ocr.md)이 있습니다. 대신 Tesseract나 클라우드 서비스 같은 다른 엔진을 쓰려면, 그 엔진이 인식한 단어를 `OcrWord` 레코드로 `Page.apply_ocr(words=...)`에 전달하세요. pylopdf가 이를 보이지 않는 텍스트 레이어로 추가하므로, 파일 크기는 거의 늘지 않으면서 스캔 문서를 검색할 수 있고 `markdown()`이 그 텍스트를 읽을 수 있습니다.

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

## NumPy와 Pillow로 이미지 처리하기 { #image-processing }

`Pixmap`은 바꿀 수 없고 편집 메서드도 없습니다. 픽셀 처리는 NumPy와 Pillow가 이미 잘합니다. `numpy.asarray(pix)`는 픽셀을 복사하지 않고 읽으며, 그레이는 `(height, width)`, 그 밖에는 `(height, width, channels)` 모양입니다. 결과는 `Pixmap.from_array(array, resolution=pix.resolution)`으로 새 pixmap을 만듭니다. 아래 함수가 pymupdf의 `Pixmap` 메서드를 대신하며, 모두 이 페이지의 코드를 그대로 실행하는 [`tests/test_image_recipes.py`](https://github.com/yhay81/pylopdf/blob/main/tests/test_image_recipes.py)로 테스트합니다.

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

### 픽셀 읽기와 설정 { #image-pixels }

`pix.pixel(x, y)`, `set_pixel`, `set_rect`, `clear_with`, `copy`는 인덱싱과 슬라이스 대입이 됩니다. 사각형은 픽셀 단위의 `IRect` 또는 `(x0, y0, x1, y1)` 튜플입니다.

```python
def pixel(pix: Pixmap, x: int, y: int) -> tuple[int, ...]:
    """Return the samples of one pixel, alpha included."""
    return tuple(int(value) for value in numpy.asarray(pix).reshape(pix.height, pix.width, pix.channels)[y, x])


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

### 색조, 감마, 반전 { #image-color }

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

### 알파 { #image-alpha }

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

### 색 통계 { #image-statistics }

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

`hashlib.md5(pix).digest()`가 `pix.digest`를, `memoryview(pix).nbytes`가 `len(pix)`와 `pix.size`를, `IRect(0, 0, pix.width, pix.height)`가 `pix.irect`를 대신합니다. pylopdf의 pixmap에는 원점이 없습니다.

### 회전, 축소, 변형 { #image-geometry }

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
    if image.mode not in {"L", "LA", "RGB", "RGBA"}:
        alpha = "transparency" in image.info or "A" in image.getbands()
        image = image.convert("RGBA" if alpha else "RGB")
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

`to_image(pix).convert("CMYK")`로 CMYK 샘플을 얻고, Pillow는 TIFF와 WebP처럼 자신이 아는 모든 형식으로 저장합니다. 그런 형식의 이미지는 `from_image`로 되돌립니다.
