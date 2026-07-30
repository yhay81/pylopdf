# pylopdf-fonts-zh-cn

A data-only package containing Simplified Chinese fallback and generation
fonts for [pylopdf](https://github.com/yhay81/pylopdf). It renders
non-embedded Adobe-GB1 fonts and provides locale-matching Simplified Chinese
glyph forms for generated Han text.

Install it through the main package extra:

```bash
pip install "pylopdf[zh-cn]"
```

## Bundled fonts

| File | Typeface | Source SHA-256 |
|---|---|---|
| `NotoSansSC-Regular.otf` | Noto Sans SC | `faa6c9df652116dde789d351359f3d7e5d2285a2b2a1f04a2d7244df706d5ea9` |
| `NotoSerifSC-Regular.otf` | Noto Serif SC | `e8f396decc1f0963a016a989c3d8852e863d1350996f573860a80767c83a1cd3` |

The files come from
[`notofonts/noto-cjk`](https://github.com/notofonts/noto-cjk) revision
`f8d157532fbfaeda587e826d4cd5b21a49186f7c`, under `Sans/SubsetOTF/SC`
and `Serif/SubsetOTF/SC`. Both are licensed under the SIL Open Font License
1.1. See `LICENSE`.
