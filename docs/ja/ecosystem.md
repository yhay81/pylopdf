---
title: エコシステム連携
description: pylopdfをTypst、pyHanko、veraPDF、OCRエンジン、NumPy、Pillowとつなぐ、テスト済みのレシピ。
---

# エコシステム連携

pylopdfは組版、PDF/Aの出力、電子署名、画像処理を実装しません。これらはすでに実績あるライブラリがうまくこなしており、以下のレシピではそれらをpylopdfと組み合わせる方法を紹介します。各レシピは[`tests/test_interop.py`](https://github.com/yhay81/pylopdf/blob/main/tests/test_interop.py)と[`tests/test_image_recipes.py`](https://github.com/yhay81/pylopdf/blob/main/tests/test_image_recipes.py)の統合テストでカバーしています。

## typstで新しい文書を組版する { #typesetting }

文書を[typst](https://typst.app/)で書き、[typst-py](https://pypi.org/project/typst/)でコンパイルして、その結果をpylopdfで開きます。

```python
import typst
import pylopdf

pdf_bytes = typst.compile("report.typ")   # レイアウトはtypst
doc = pylopdf.open(pdf_bytes)      # 編集・結合・抽出はpylopdf
```

## typstでPDF/Aを出力する { #new-pdfa }

typstはPDF/A（パート1〜4）とPDF/UA-1のファイルを書き出せます。

```python
pdf_a: bytes = typst.compile("report.typ", pdf_standards="a-2b")
pylopdf.open(pdf_a).pdfa_claim   # PdfaClaim(part=2, conformance='B')
```

`Document.pdfa_claim`は、ファイルがどのPDF/Aレベルを宣言しているかを読むだけです。既存のPDFが本当に適合しているかを確認するには、標準的な検証ツールである[veraPDF](https://verapdf.org/)（Javaで動作）を使ってください。

## typstでデザインした透かしやヘッダー { #cjk-watermarks }

単純なテキストなら`canvas.text()`で十分です。`pylopdf[jp]`（または`[ko]`、`[zh-cn]`、`[zh-tw]`）をインストールしていれば、CJKのテキストも書けます。スタンプに本格的なレイアウトが必要なときは、typstでページとしてデザインし、そのページを`canvas.page()`で文書の全ページに描きます。

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

スタンプは本物のテキストのままなので、後から`page.text()`で`社外秘`が見つかります。

## pyHankoで電子署名する { #signatures }

[pyHanko](https://pypi.org/project/pyHanko/)（MIT）はPAdES署名を作成します。署名をファイルの末尾に追記するので、pylopdfが書き出したバイト列は変わりません。

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

署名は最後の手順にしてください。その後にpylopdfで保存するとファイルが書き直され、署名が無効になります。`doc.form`の署名フィールドに値を代入すると例外になります。

## ほかのエンジンでOCRする { #ocr }

pylopdfには[組み込みのOCR](ocr.md)があります。代わりにTesseractやクラウドサービスなど別のエンジンを使う場合は、その単語を`OcrWord`レコードとして`Page.apply_ocr(words=...)`に渡してください。pylopdfはそれを不可視のテキスト層として追加します。これでスキャンが検索可能になり、`markdown()`でも読み取れるようになり、ファイルサイズもほとんど増えません。

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

## NumPyとPillowで画像を処理する { #image-processing }

`Pixmap`は変更できず、編集用のメソッドも持ちません。画素の処理はNumPyとPillowがすでにうまくこなします。`numpy.asarray(pix)`は画素をコピーせずに読み、グレーなら`(height, width)`、それ以外は`(height, width, channels)`の形になります。結果は`Pixmap.from_array(array, resolution=pix.resolution)`で新しいピクセルマップにします。以下の関数がpymupdfの`Pixmap`のメソッドの代わりになり、どれもこのページのコードをそのまま実行する[`tests/test_image_recipes.py`](https://github.com/yhay81/pylopdf/blob/main/tests/test_image_recipes.py)でテストしています。

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

### 画素を読む・設定する { #image-pixels }

`pix.pixel(x, y)`、`set_pixel`、`set_rect`、`clear_with`、`copy`は、インデックスとスライスへの代入になります。矩形はピクセル単位の`IRect`か`(x0, y0, x1, y1)`タプルです。

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

### 色調、ガンマ、反転 { #image-color }

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

### アルファ { #image-alpha }

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

### 色の統計 { #image-statistics }

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

`pix.digest`は`hashlib.md5(pix).digest()`、`len(pix)`と`pix.size`は`memoryview(pix).nbytes`、`pix.irect`は`IRect(0, 0, pix.width, pix.height)`になります。pylopdfのピクセルマップは原点を持ちません。

### 回転、縮小、変形 { #image-geometry }

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

`to_image(pix).convert("CMYK")`でCMYKの値が得られ、PillowはTIFFやWebPなど自分が扱えるあらゆる形式で保存できます。それらの形式の画像は`from_image`で戻せます。
