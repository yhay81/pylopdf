# Offline OCR

pylopdf can recognize scanned pages locally and add an invisible searchable
text layer. Install the optional model package:

```bash
pip install "pylopdf[ocr]"
```

The core extension runs PP-OCRv6 small through the pure-Rust RTen runtime. It
does not need a system executable, shared library, network request, or ONNX
parser at runtime. The separately versioned model wheel is about 26.6 MB and
supports 50 languages, including Japanese, Simplified and Traditional Chinese,
and English.

## Recognize without editing

`Page.get_text_ocr()` returns positioned words without modifying the document:

```python
import pylopdf

with pylopdf.open("scan.pdf") as doc:
    words = doc[0].get_text_ocr()
    for word in words:
        print(word["bbox"], word["text"], word["confidence"])
```

Each `OcrWord` has a `Rect` in the same rotation-resolved, top-left display
coordinates used by rendering and extraction. `confidence` is a deterministic
recognizer ranking signal, not a calibrated probability.

## Make a scan searchable

Load one engine and reuse it across pages:

```python
import pylopdf

engine = pylopdf.OcrEngine(threads=4, max_concurrent=1)
with pylopdf.open("scan.pdf") as doc:
    for page in doc:
        page.apply_ocr(engine=engine)
    doc.save("searchable.pdf", garbage=3, deflate=True, object_streams=True)
```

`apply_ocr()` preserves rendered pixels and existing page content. It skips a
page that already has extractable text by default, so rerunning the pipeline
does not duplicate its invisible layer. On a mixed-content page, pass a
display-coordinate `clip=(x0, y0, x1, y1)` around the scanned region; only
existing text intersecting that region triggers the skip. Use
`skip_existing=False` deliberately to append despite intersecting text.

## Resource controls

The defaults are 300 dpi, 1,408-pixel detector tiles with 192-pixel overlap,
at most four RTen worker threads, and one complete recognition call at a time
per engine. Overlapping tiles bound detector memory on full pages while merging
duplicate edge detections. In one measured 300-dpi A4 workload, the default
geometry peaked near 419 MiB; documents, platforms, and allocators change that
value.

Reduce `threads` and `tile_size` when memory is tighter. Raise
`max_concurrent` only after measuring the combined live raster and inference
buffers:

```python
engine = pylopdf.OcrEngine(threads=2, max_concurrent=1)
words = page.get_text_ocr(
    engine=engine,
    tile_size=1280,
    overlap=192,
    min_confidence=0.6,
)
```

`clip` reduces OCR detector input and recognition work, but hayro 0.7 still
renders the full page before cropping. Returned boxes remain in full-page
display coordinates.

An `OcrEngine` is immutable and reusable across distinct documents. Its
`max_concurrent=1` default serializes each complete render-and-recognize call,
including calls made from free-threaded Python, so an accidentally shared
engine does not multiply the measured per-call memory. Set a higher value,
up to 16, only when the workload has been measured. Each admitted call still
owns its raster and inference buffers. Simultaneous external calls or edits on
the same `Document` remain outside pylopdf's concurrency contract.

## Measured accuracy gate

The tracked, redistributable MHLW fixture supplies 1,188 extracted
ground-truth characters. The native pipeline measured:

| DPI | Strict CER | NFKC CER | Elapsed |
|---:|---:|---:|---:|
| 150 | 3.788% | 0.842% | 5.71s |
| 300 | 3.704% | 0.842% | 11.93s |

The RapidOCR v6 reference measured 0.926% and 0.758% NFKC CER respectively, so
the report retains both pylopdf's 150-dpi win and 300-dpi loss. Strict CER only
removes whitespace; NFKC CER additionally folds compatibility forms such as
full-width Latin characters. Timings are hardware-specific. Reproduce the
complete report with `uv run python bench/ocr.py`.

## Model and layout boundaries

With no paths, `OcrEngine` discovers the verified model set installed by
`pylopdf[ocr]`. Advanced users can pass a compatible RTen-format PP-OCR
detector, recognizer, and dictionary explicitly.

The first native engine returns axis-aligned word boxes. It does not yet deskew
arbitrary text, detect a sideways page automatically, or interpret ruby,
warichu, and mixed-orientation typography. Set the page rotation explicitly
before OCR when a scan is sideways. PP-OCRv6 model provenance, source and
artifact hashes, conversion commands, and Apache-2.0 notices are included in
the `pylopdf-ocr-models` distribution.
