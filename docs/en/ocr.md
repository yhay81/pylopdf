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

## Correct rotated input

Pass `rotation=90`, `180`, or `270` to turn the rendered OCR input clockwise
before detection and recognition:

```python
words = page.get_text_ocr(rotation=270)
page.apply_ocr(rotation=270)
```

The PDF page rotation and rendered pixels do not change. Returned word boxes
remain in the page's original display coordinates. `apply_ocr()` also orients
the invisible baseline, so extraction and search follow the recognized logical
text after save and reopen. A nonzero correction temporarily allocates one
additional `width * height * 4` RGBA raster inside the engine's complete-call
admission limit.

## Resource controls

The defaults are 300 dpi, 1,408-pixel detector tiles with 192-pixel overlap,
at most four RTen worker threads, and one complete recognition call at a time
per engine. Overlapping tiles bound detector memory on full pages while merging
duplicate edge detections. In one measured 300-dpi A4 workload, the default
geometry peaked near 419 MiB; documents, platforms, and allocators change that
value.

Model loading has a separate cumulative 64 MiB input boundary across the
detector, recognizer, and dictionary. All three files are admitted before RTen
parses either model, and dictionaries stop at 65,536 entries. Refusals raise
`LimitError` with code `ocr_model_size` or `ocr_dictionary_entries`. Pass
`max_model_size=None` only for a trusted custom model set.

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

Two tracked, redistributable Japanese fixtures cover different inputs. The
digital MHLW document supplies 1,188 extracted ground-truth characters. An
image-only Agency for Cultural Affairs archival scan supplies 384 manually
verified characters; small ruby is excluded because ruby association is outside
the OCR contract.

| Case | DPI | Strict CER | NFKC CER | Elapsed |
|---|---:|---:|---:|---:|
| MHLW digital | 150 | 3.788% | 0.842% | 5.50s |
| MHLW digital | 300 | 3.704% | 0.842% | 13.87s |
| archival scan | 150 | 1.823% | 1.562% | 2.05s |
| archival scan | 300 | 1.302% | 1.042% | 5.37s |

For the MHLW case, the RapidOCR v6 reference measured 0.926% and 0.758% NFKC
CER respectively, so the report retains both pylopdf's 150-dpi win and 300-dpi
loss. Strict CER only removes whitespace; NFKC CER additionally folds
compatibility forms such as full-width Latin characters. Timings are
hardware-specific.

A separate 150-dpi field check ran both documents through one shared engine.
`max_concurrent=1` completed in 6.31s, while `max_concurrent=2` completed in
6.75s; both exactly matched the sequential text. This workload showed no
throughput gain from the higher limit while each admitted call still owns
separate buffers, supporting the conservative default of 1. Reproduce the
complete report with `uv run python bench/ocr.py`.

## Model and layout boundaries

With no paths, `OcrEngine` discovers the verified model set installed by
`pylopdf[ocr]`. Advanced users can pass a compatible RTen-format PP-OCR
detector, recognizer, and dictionary explicitly.

The first native engine returns axis-aligned word boxes. Explicit clockwise
correction handles known 90-degree orientations, but it does not yet deskew
arbitrary text, detect the required correction automatically, or interpret
ruby, warichu, and mixed-orientation typography. PP-OCRv6 model provenance,
source and artifact hashes, conversion commands, and Apache-2.0 notices are
included in the `pylopdf-ocr-models` distribution.
