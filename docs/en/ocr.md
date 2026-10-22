---
title: Offline OCR
description: Recognize text in scanned PDFs locally with pylopdf's built-in OCR engine, or add text from any other OCR engine.
---

# Offline OCR

pylopdf can recognize text in scanned pages and add it as an invisible text
layer, which makes the PDF searchable and lets `get_text()` and
`to_markdown()` read it. Install the model package:

```bash
pip install "pylopdf[ocr]"
```

Recognition runs locally in pylopdf's own engine, using the PP-OCRv6 small
models. It needs no network connection, no system program such as Tesseract,
and no other Python package. The model package is about 27 MB and supports 50
languages, including English, Japanese, and Simplified and Traditional
Chinese.

OCR is not available in the [WebAssembly](wasm.md) build.

## Recognize without editing { #recognize }

`Page.get_text_ocr()` returns the words it finds and leaves the document
unchanged:

```python
import pylopdf

with pylopdf.open("scan.pdf") as doc:
    for word in doc.pages[0].get_text_ocr():
        print(word["bbox"], word["text"], word["confidence"])
```

Each `OcrWord` has a `bbox` in the same page coordinates as text extraction
and rendering. `confidence` is useful for ranking and filtering, but it is not
a calibrated probability.

## Make a scan searchable { #searchable }

Create one engine and reuse it for every page:

```python
import pylopdf

engine = pylopdf.OcrEngine()
with pylopdf.open("scan.pdf") as doc:
    for page in doc.pages:
        page.apply_ocr(engine=engine)
    doc.save("searchable.pdf", dedupe=True, object_streams=True)
```

`apply_ocr()` does not change how the page looks. It skips a page that already
has text, so running the same script twice does not add the text twice. For a
page that mixes typed text and a scanned image, pass `clip=(x0, y0, x1, y1)`
around the scanned area: only text inside that area then counts as existing
text. Pass `skip_existing=False` to add OCR text regardless.

## Use another OCR engine { #other-engines }

`Page.insert_ocr_text_layer(words)` adds words from any OCR engine or cloud
service. Each word is `(x0, y0, x1, y1, text)` in page coordinates; extra
items after `text` are ignored:

```python
words = [(72, 90, 140, 104, "Invoice"), (146, 90, 190, 104, "#1024")]
doc.pages[0].insert_ocr_text_layer(words)
```

The layer uses no embedded font, so it adds very little to the file size.

## Rotated scans { #rotation }

If a page is scanned sideways or upside down, pass `rotation=90`, `180`, or
`270` to turn the image clockwise before recognition:

```python
words = page.get_text_ocr(rotation=270)
page.apply_ocr(rotation=270)
```

The page itself is not rotated, and the returned boxes stay in the page's
coordinates. The invisible text follows the recognized reading direction, so
search and extraction work after saving. pylopdf does not detect the rotation
for you and does not straighten slightly skewed scans.

## Memory and speed { #resources }

By default pages are rendered at 300 dpi and recognized in overlapping tiles of
1,408 pixels. One A4 page at these settings used about 420 MiB at its peak in
our measurements. To use less memory, lower `threads` and `tile_size`:

```python
engine = pylopdf.OcrEngine(threads=2)
words = page.get_text_ocr(engine=engine, tile_size=1280, min_confidence=0.6)
```

`clip=` also saves time and memory, because only the selected area is
recognized.

An `OcrEngine` can be shared across threads and documents. By default it runs
one recognition at a time (`max_concurrent=1`), so sharing it does not
multiply memory use. You can raise `max_concurrent` up to 16, but measure
first: in our tests two at a time was not faster. A `Document` itself must
still be used from one thread at a time; see [Concurrency](concurrency.md).

## Accuracy { #accuracy }

Character error rates (CER, lower is better) on two Japanese test documents
with known text. "Strict" ignores only whitespace; "NFKC" also treats
full-width and half-width forms as equal.

| Document | DPI | Strict CER | NFKC CER | Time |
|---|---:|---:|---:|---:|
| Government report (digital, 1,188 characters) | 150 | 3.788% | 0.842% | 5.50 s |
| Government report (digital, 1,188 characters) | 300 | 3.704% | 0.842% | 13.87 s |
| Archival scan (384 characters) | 150 | 1.823% | 1.562% | 2.05 s |
| Archival scan (384 characters) | 300 | 1.302% | 1.042% | 5.37 s |

For comparison, the RapidOCR v6 reference scored 0.926% and
0.758% NFKC CER on the government report at 150 and 300 dpi: pylopdf was more
accurate at 150 dpi and less accurate at 300 dpi. Times depend on hardware.
Run `uv run python bench/ocr.py` in the repository to reproduce the report.

## Limitations { #limitations }

- Word boxes are axis-aligned rectangles.
- Ruby (furigana), warichu, and text that mixes vertical and horizontal
  orientation are not interpreted.
- Custom models must be PP-OCR models converted to RTen format; pass their
  paths to `OcrEngine(detector_path, recognizer_path, dictionary_path)`.

The `pylopdf-ocr-models` package documents where the models come from, their
hashes, and their Apache-2.0 license.
