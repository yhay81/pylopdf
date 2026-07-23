# pylopdf-fonts-cjk

A data-only package containing CJK fallback fonts for
[pylopdf](https://github.com/yhay81/pylopdf). It renders PDFs that reference
Japanese fonts without embedding the font programs.

Install it through the main package extra:

```bash
pip install "pylopdf[cjk]"
```

When installed, pylopdf discovers it automatically for non-embedded CID fonts,
such as PDFs that only reference MS-Mincho, Ryumin-Light, or MS-Gothic. Serif
font names map to Noto Serif JP; other names map to Noto Sans JP.

## Bundled fonts

| File | Typeface | Source |
|---|---|---|
| `NotoSansJP-Regular.otf` | Noto Sans JP | [notofonts/noto-cjk](https://github.com/notofonts/noto-cjk), `Sans/SubsetOTF/JP` |
| `NotoSerifJP-Regular.otf` | Noto Serif JP | [notofonts/noto-cjk](https://github.com/notofonts/noto-cjk), `Serif/SubsetOTF/JP` |

Both fonts are licensed under the SIL Open Font License 1.1. See `LICENSE`.
