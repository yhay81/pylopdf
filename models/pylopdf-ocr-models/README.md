# pylopdf-ocr-models

A data-only package containing the PP-OCRv6 small detector, recognizer, and
multilingual character dictionary used by
[pylopdf](https://github.com/yhay81/pylopdf). The models run locally through
pylopdf's pure-Rust RTen backend; OCR does not require a system executable,
ONNX Runtime, an ONNX parser at runtime, or network access.

Install it through the main package extra:

```bash
pip install "pylopdf[ocr]"
```

The small PP-OCRv6 tier is intended for mobile and desktop use. Its unified
model supports 50 languages, including Japanese, Simplified Chinese,
Traditional Chinese, and English.

## Bundled artifacts

| File | Purpose | SHA-256 |
|---|---|---|
| `PP-OCRv6_det_small.rten` | Multilingual text detection | `57c5778be784d7aace438a4ea1926864a7b5fd3d49c8e577799f8a83e8dcb022` |
| `PP-OCRv6_rec_small.rten` | Multilingual text recognition | `b6eef947901c5ff0bf6ee62d8b46f58b1423666f0ae0c87b2f42127502925f8f` |
| `ppocrv6_dict.txt` | Recognition character dictionary | `b5f2bfe2bdd9448429e3e82b51c789775d9b42f2403d082b00662eb77e401c5d` |

The installed package also includes these values in `SHA256SUMS`.

The source ONNX artifacts and dictionary are the PP-OCRv6 small files listed in
[RapidOCR's pinned model manifest](https://github.com/RapidAI/RapidOCR/blob/095232a4c94f7f0e6600ba5bba1177010ad696d4/python/rapidocr/default_models.yaml)
and downloaded from its versioned v3.9.2 model repository. They originate from
[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR); RapidOCR converted and
distributes the ONNX forms.

| Source | URL | SHA-256 |
|---|---|---|
| Detector ONNX | [PP-OCRv6_det_small.onnx](https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv6/det/PP-OCRv6_det_small.onnx) | `090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f` |
| Recognizer ONNX | [PP-OCRv6_rec_small.onnx](https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv6/rec/PP-OCRv6_rec_small.onnx) | `6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884` |
| Dictionary | [ppocrv6_dict.txt](https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/paddle/PP-OCRv6/rec/PP-OCRv6_rec_small/ppocrv6_dict.txt) | `b5f2bfe2bdd9448429e3e82b51c789775d9b42f2403d082b00662eb77e401c5d` |

## Reproducing the RTen conversion

After downloading and verifying the source files above, reproduce the bundled
model data from the repository root with:

```bash
uvx --from rten-convert==0.22.0 rten-convert \
  --no-infer-shapes PP-OCRv6_det_small.onnx PP-OCRv6_det_small.rten
uvx --from rten-convert==0.22.0 rten-convert \
  --no-infer-shapes PP-OCRv6_rec_small.onnx PP-OCRv6_rec_small.rten
```

`rten-convert` embeds each source ONNX SHA-256 in the RTen file. Conversion is
deterministic: repeating the commands produces the artifact hashes in the first
table. Numerical outputs were also checked against the ONNX models before
packaging. The character dictionary remains a separate input because RTen's
conversion format does not preserve the ONNX recognizer's custom character
metadata.

Model copyright is held by Baidu. PaddleOCR and RapidOCR are licensed under
Apache License 2.0. See `LICENSE` and `NOTICE`.
