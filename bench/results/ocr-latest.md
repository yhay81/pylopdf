# pylopdf native OCR results

- Run at: 2026-07-25 07:29 UTC
- Environment: Windows-11-10.0.26200-SP0 / Python 3.14.6 / CPU AMD64 Family 23 Model 113 Stepping 0, AuthenticAMD
- Versions: pylopdf 0.10.0, pylopdf-ocr-models 0.1.0
- Fixture: `tests/assets/real_world/mhlw-doc.pdf` (source and license in `tests/assets/real_world/README.md`)
- Controls: tile size 1408, overlap 192, threads 4, minimum confidence 0.5
- Reproduce: `uv sync --all-extras && uv run python bench/ocr.py`
- Model artifacts: 57c5778be784d7aace438a4ea1926864a7b5fd3d49c8e577799f8a83e8dcb022 PP-OCRv6_det_small.rten; b6eef947901c5ff0bf6ee62d8b46f58b1423666f0ae0c87b2f42127502925f8f PP-OCRv6_rec_small.rten; b5f2bfe2bdd9448429e3e82b51c789775d9b42f2403d082b00662eb77e401c5d ppocrv6_dict.txt

| DPI | Words | Strict expected / actual | Strict edits | Strict CER | NFKC expected / actual | NFKC edits | NFKC CER | Elapsed |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 150 | 47 | 1188 / 1182 | 45 | 3.788% | 1188 / 1182 | 10 | 0.842% | 5.71s |
| 300 | 48 | 1188 / 1181 | 44 | 3.704% | 1188 / 1181 | 10 | 0.842% | 11.93s |

Strict CER removes whitespace only. NFKC CER additionally folds compatibility
forms such as full-width Latin characters. Elapsed time includes page rendering,
tiled detection, recognition, ordering, and Python result materialization after
the engine has been loaded. It is one run, not a throughput benchmark.
