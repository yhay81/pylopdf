# pylopdf-fonts-zh-tw

A data-only package containing Traditional Chinese fallback and generation
fonts for [pylopdf](https://github.com/yhay81/pylopdf). It renders
non-embedded Adobe-CNS1 fonts and provides Taiwan Traditional Chinese glyph
forms for generated Han and Bopomofo text.

Install it through the main package extra:

```bash
pip install "pylopdf[zh-tw]"
```

## Bundled fonts

| File | Typeface | Source SHA-256 |
|---|---|---|
| `NotoSansTC-Regular.otf` | Noto Sans TC | `5bab0cb3c1cf89dde07c4a95a4054b195afbcfe784d69d75c340780712237537` |
| `NotoSerifTC-Regular.otf` | Noto Serif TC | `63515eb0622d589a2f4062e8757c598b3da3bffd053f69abd181c210158ef10c` |

The files come from
[`notofonts/noto-cjk`](https://github.com/notofonts/noto-cjk) revision
`f8d157532fbfaeda587e826d4cd5b21a49186f7c`, under `Sans/SubsetOTF/TC`
and `Serif/SubsetOTF/TC`. Both are licensed under the SIL Open Font License
1.1. See `LICENSE`.
