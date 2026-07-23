# pylopdf

PDF editing, extraction and rendering for Python, powered by Rust —
[lopdf](https://github.com/J-F-Liu/lopdf) for editing and
[hayro](https://github.com/LaurenzV/hayro) (the pure-Rust PDF renderer adopted
by typst) for rendering. **MIT licensed, zero runtime dependencies, ~3.5 MB
wheels.**

```bash
pip install pylopdf
```

```python
import pylopdf

doc = pylopdf.open("input.pdf")
text = doc.get_page_text(0)
png = doc.render_page(0, dpi=300)
doc.save("out.pdf", garbage=True, deflate=True)
```

## Why pylopdf?

|  | pylopdf | pymupdf | pypdf | pypdfium2 |
|---|---|---|---|---|
| License | **MIT** | AGPL / commercial | BSD | Apache/BSD |
| Wheel size | **~3.5 MB** | ~40 MB+ | small (pure Python) | ~8 MB |
| Editing (merge / split / rotate / TOC) | ✅ | ✅ | ✅ | limited |
| Rendering (PNG / SVG) | ✅ | ✅ | ❌ | ✅ (PNG) |
| Positioned text extraction & search | ✅ | ✅ | partial | ✅ |
| Markdown conversion (RAG) | ✅ built in | separate package (AGPL) | ❌ | ❌ |
| Encryption (AES-256) | ✅ read & write | ✅ | ✅ | ❌ |
| CJK font fallback | ✅ (`[cjk]` extra) | ✅ | — | manual |
| Implementation | **pure Rust** | C | Python | C++ (PDFium) |

- Fits size-constrained environments such as AWS Lambda
- Safe for commercial projects that need to avoid the AGPL
- One abi3 wheel covers Python 3.10–3.14
- API modeled after pymupdf — see the [migration guide](migration.md)
- Reproducible [benchmarks](https://github.com/yhay81/pylopdf/blob/main/bench/results/latest.md)
  published with wins *and* losses

## Design principles

pylopdf stays a lightweight core for editing, extraction and rendering.
Adjacent concerns are solved by pairing with established libraries —
typesetting and PDF/A via typst, digital signatures via pyHanko, PDF/A
validation via veraPDF. Every recipe is guarded by integration tests. See
[Ecosystem recipes](ecosystem.md).

## Links

- [PyPI](https://pypi.org/project/pylopdf/)
- [GitHub](https://github.com/yhay81/pylopdf)
- [Changelog](https://github.com/yhay81/pylopdf/blob/main/CHANGELOG.md)
- [Security policy](https://github.com/yhay81/pylopdf/blob/main/SECURITY.md)
