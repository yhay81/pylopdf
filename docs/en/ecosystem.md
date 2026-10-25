---
title: Ecosystem recipes
description: Tested recipes connecting pylopdf with Typst, pyHanko, veraPDF, OCR engines, NumPy, and Pillow.
---

# Ecosystem recipes

pylopdf does not implement typesetting, PDF/A output, digital signatures,
image processing, or image formats beyond JPEG, PNG, and JPEG 2000.
Established libraries already do these well, and the recipes below show how
to combine them with pylopdf. Each recipe is covered by the
integration tests in
[`tests/test_interop.py`](https://github.com/yhay81/pylopdf/blob/main/tests/test_interop.py),
[`tests/test_image_recipes.py`](https://github.com/yhay81/pylopdf/blob/main/tests/test_image_recipes.py),
and [`tests/test_conversion_recipes.py`](https://github.com/yhay81/pylopdf/blob/main/tests/test_conversion_recipes.py).

## Typesetting new documents with typst { #typesetting }

Write the document in [typst](https://typst.app/), compile it with
[typst-py](https://pypi.org/project/typst/), and open the result in pylopdf:

```python
import typst
import pylopdf

pdf_bytes = typst.compile("report.typ")   # typst lays out the document
doc = pylopdf.open(pdf_bytes)      # pylopdf edits, merges, and extracts
```

## PDF/A output with typst { #new-pdfa }

typst can write PDF/A (parts 1 to 4) and PDF/UA-1 files:

```python
pdf_a: bytes = typst.compile("report.typ", pdf_standards="a-2b")
pylopdf.open(pdf_a).pdfa_claim   # PdfaClaim(part=2, conformance='B')
```

`Document.pdfa_claim` only reads which PDF/A level a file claims. To
check that an existing PDF really conforms, use
[veraPDF](https://verapdf.org/), the standard validator (it runs on Java).

## Watermarks and headers designed in typst { #cjk-watermarks }

For simple text, `canvas.text()` is enough; with `pylopdf[jp]` (or `[ko]`,
`[zh-cn]`, `[zh-tw]`) installed it writes CJK text too. When a stamp needs
real layout, design it as a page in typst and draw that page onto every page
of the document with `canvas.page()`:

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

The stamp stays real text: `page.text()` finds `社外秘` afterwards.

## Digital signatures with pyHanko { #signatures }

[pyHanko](https://pypi.org/project/pyHanko/) (MIT) creates PAdES signatures.
It appends the signature to the end of the file, so the bytes pylopdf wrote stay
unchanged:

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

To sign into a field placed with pylopdf, add a `SignatureField` and pass its
name as `field_name`; pylopdf then reads the signed state:

```python
from pylopdf import SignatureField, SignatureFlags

doc.pages[0].widgets.add(SignatureField("Approval", (40, 40, 240, 100)))
# ... sign doc.to_bytes() with field_name="Approval" as above ...
with pylopdf.open(signed_pdf) as signed:
    assert signed.form["Approval"].is_signed
    assert SignatureFlags.APPEND_ONLY in signed.form.signature_flags
```

A full save with pylopdf afterwards rewrites the file and invalidates the
signature, which `SignatureFlags.APPEND_ONLY` warns of. Edit a signed
document with `save(..., incremental=True)` instead: the signed bytes stay a
prefix of the output, so pyHanko still validates the signature, and its
difference analysis reports what the update changed. Filling form fields and
editing metadata count as allowed modifications; pyHanko reports other edits,
such as a new comment, and a certification signature that forbids changes,
as modifications it cannot explain. Assigning the value of a signature field
in `doc.form` raises.

```python
with pylopdf.open(signed_pdf) as signed:
    signed.form["Name"].value = "Alice"
    filled: bytes = signed.to_bytes(incremental=True)
```

## OCR with another engine { #ocr }

pylopdf has [built-in OCR](ocr.md). To use another engine instead, such as
Tesseract or a cloud service, pass its words as `OcrWord` records to
`Page.apply_ocr(words=...)`. pylopdf adds them as an invisible text layer,
which makes the scan searchable and lets `markdown()` read it, while adding
very little to the file size.

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

## Image processing with NumPy and Pillow { #image-processing }

A `Pixmap` cannot be changed, and it has no editing methods: NumPy and Pillow
already process pixels well. `numpy.asarray(pix)` reads the pixels without a
copy, shaped `(height, width)` for gray and `(height, width, channels)`
otherwise, and `Pixmap.from_array(array, resolution=pix.resolution)` makes a
new pixmap of the result. The functions below replace pymupdf's `Pixmap`
methods; each is tested by
[`tests/test_image_recipes.py`](https://github.com/yhay81/pylopdf/blob/main/tests/test_image_recipes.py),
which runs the code on this page.

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

### Reading and setting pixels { #image-pixels }

`pix.pixel(x, y)`, `set_pixel`, `set_rect`, `clear_with`, and `copy` become
indexing and slice assignment. Rectangles are `IRect` values or
`(x0, y0, x1, y1)` tuples in pixels.

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

### Tinting, gamma, and inversion { #image-color }

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

### Color statistics { #image-statistics }

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

`hashlib.md5(pix).digest()` replaces `pix.digest`, `memoryview(pix).nbytes`
replaces `len(pix)` and `pix.size`, and `IRect(0, 0, pix.width, pix.height)`
replaces `pix.irect`: a pylopdf pixmap has no origin.

### Turning, shrinking, and warping { #image-geometry }

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

`to_image(pix).convert("CMYK")` gives CMYK samples, and Pillow saves every
format it knows, such as TIFF and WebP; `from_image` brings an image of any
of those back.

## Images and comic archives to PDF { #images-to-pdf }

pylopdf draws JPEG, PNG, and JPEG 2000 images on pages, storing JPEG and
JPEG 2000 data as it is, and `image_info()` reads an image's size, resolution,
and EXIF orientation without decoding it. The functions below make a PDF
with one page per image, each page the size at which the image shows at its
own resolution; `canvas.image` stands the image up as its EXIF orientation
asks. They replace pymupdf's `convert_to_pdf()` of an image or a CBZ archive
and `insert_file()` of an image; each is tested by
[`tests/test_conversion_recipes.py`](https://github.com/yhay81/pylopdf/blob/main/tests/test_conversion_recipes.py),
which runs the code on this page.

```python
import io
import zipfile

import pylopdf

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".jp2", ".j2k", ".jpx")


def page_size(info: pylopdf.ImageInfo) -> tuple[float, float]:
    """Return the size in points at which an image shows upright at its own resolution."""
    width = info.width * 72 / info.resolution[0]
    height = info.height * 72 / info.resolution[1]
    # Orientations 5 to 8 turn the image a quarter, as canvas.image shows it.
    return (height, width) if info.orientation >= 5 else (width, height)


def images_to_pdf(images) -> bytes:
    """Return a PDF with one page per image, given as paths or encoded bytes."""
    doc = pylopdf.Document()
    for image in images:
        size = page_size(pylopdf.image_info(image))
        with doc.pages.new(size).draw() as canvas:
            # The page has the image's shape at its resolution, which can differ across and down.
            canvas.image((0, 0, *size), image, keep_aspect=False)
    return doc.to_bytes()


def cbz_to_pdf(path) -> bytes:
    """Return a PDF of a comic book archive's images, in the order of their names."""
    with zipfile.ZipFile(path) as archive:
        names = sorted(name for name in archive.namelist() if name.lower().endswith(IMAGE_SUFFIXES))
        return images_to_pdf([archive.read(name) for name in names])
```

To add the pages to an open document, extend its pages:
`doc.pages.extend(pylopdf.open(images_to_pdf(paths)).pages)`. Pillow reads
the formats that pylopdf does not, such as TIFF, WebP, and GIF; this function
turns every frame of such a file into PNG bytes for `images_to_pdf`, standing
it up and keeping its resolution:

```python
def pillow_frames(path) -> list[bytes]:
    """Return every frame of an image that Pillow reads as PNG bytes."""
    from PIL import Image, ImageOps, ImageSequence

    frames = []
    with Image.open(path) as image:
        for frame in ImageSequence.Iterator(image):
            upright = ImageOps.exif_transpose(frame)
            if upright.mode not in ("1", "L", "LA", "P", "RGB", "RGBA"):
                upright = upright.convert("RGBA" if "A" in upright.getbands() else "RGB")
            buffer = io.BytesIO()
            upright.save(buffer, "PNG", dpi=frame.info.get("dpi", (96, 96)))
            frames.append(buffer.getvalue())
    return frames
```

To make the pages of scanned images searchable, run
[`apply_ocr()`](ocr.md#searchable) on each page of
`pylopdf.open(images_to_pdf(paths))`.
