# pylopdf native OCR results

- Run at: 2026-07-25 13:10 UTC
- Environment: Windows-11-10.0.26200-SP0 / Python 3.14.6 / CPU AMD64 Family 23 Model 113 Stepping 0, AuthenticAMD
- Versions: pylopdf 0.10.0, pylopdf-ocr-models 0.1.0
- Fixtures (sources and licenses in `tests/assets/real_world/README.md`):
  - `mhlw-digital`: `tests/assets/real_world/mhlw-doc.pdf` - embedded text retained as independent ground truth
  - `bunka-scan`: `tests/assets/real_world/bunka-kokugo-series-019-p4.pdf` - image-only archival scan with manually verified ground truth
- Controls: tile size 1408, overlap 192, threads 4, max concurrent 1, minimum confidence 0.5
- Reproduce: `uv sync --all-extras && uv run python bench/ocr.py`
- Model artifacts: 57c5778be784d7aace438a4ea1926864a7b5fd3d49c8e577799f8a83e8dcb022 PP-OCRv6_det_small.rten; b6eef947901c5ff0bf6ee62d8b46f58b1423666f0ae0c87b2f42127502925f8f PP-OCRv6_rec_small.rten; b5f2bfe2bdd9448429e3e82b51c789775d9b42f2403d082b00662eb77e401c5d ppocrv6_dict.txt

| Case | DPI | Words | Strict expected / actual | Strict edits | Strict CER | NFKC expected / actual | NFKC edits | NFKC CER | Elapsed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| mhlw-digital | 150 | 47 | 1188 / 1182 | 45 | 3.788% | 1188 / 1182 | 10 | 0.842% | 5.50s |
| mhlw-digital | 300 | 48 | 1188 / 1181 | 44 | 3.704% | 1188 / 1181 | 10 | 0.842% | 13.87s |
| bunka-scan | 150 | 19 | 384 / 383 | 7 | 1.823% | 384 / 383 | 6 | 1.562% | 2.05s |
| bunka-scan | 300 | 23 | 384 / 385 | 5 | 1.302% | 384 / 385 | 4 | 1.042% | 5.37s |

Strict CER removes whitespace only. NFKC CER additionally folds compatibility
forms such as full-width Latin characters. Elapsed time includes page rendering,
tiled detection, recognition, ordering, and Python result materialization after
the engine has been loaded. It is one run, not a throughput benchmark.

## Bounded concurrent workload

The 2 selected documents run through one shared engine at 150 dpi. The timer starts after model loading. Equality compares exact ordered OCR text with the sequential accuracy pass.

| `max_concurrent` | Calls | Words | Exact reference match | Elapsed |
|---:|---:|---:|:---:|---:|
| 1 | 2 | 66 | yes | 6.31s |
| 2 | 2 | 66 | yes | 6.75s |

This is a bounded field check, not a throughput claim: simultaneous calls own
separate raster and inference buffers, so peak memory grows with the admission
limit even when elapsed time does not improve.
