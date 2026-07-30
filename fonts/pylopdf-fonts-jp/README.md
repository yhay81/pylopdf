# pylopdf-fonts-jp

A data-only package containing Japanese fallback and generation fonts for
[pylopdf](https://github.com/yhay81/pylopdf). It renders PDFs that reference
Japanese fonts without embedding the font programs and lets `insert_text` /
`insert_textbox` auto-select a subset-embedded JP font for Japanese or Han text.

Install it through the main package extra:

```bash
pip install "pylopdf[jp]"
```

When installed, pylopdf discovers it automatically for non-embedded CID fonts,
such as PDFs that only reference MS-Mincho, Ryumin-Light, or MS-Gothic. Serif
font names map to Noto Serif JP; other names map to Noto Sans JP.

These are the upstream JP subset fonts. They do not contain Hangul, and their
Han glyph forms follow Japanese typography. Pass an explicit locale-matching
font for Korean or Chinese production output. Automatic text generation selects
one complete font run; it is not per-glyph fallback.

## Bundled fonts

| File | Typeface | Source SHA-256 |
|---|---|---|
| `NotoSansJP-Regular.otf` | Noto Sans JP | `dff723ba59d57d136764a04b9b2d03205544f7cd785a711442d6d2d085ac5073` |
| `NotoSerifJP-Regular.otf` | Noto Serif JP | `2c9a12dbd4f2408c4610c7ee84a108b62d7236c3775baed618c64d9cb44b2f04` |

The files come from
[`notofonts/noto-cjk`](https://github.com/notofonts/noto-cjk), under
`Sans/SubsetOTF/JP` and `Serif/SubsetOTF/JP`. Both are licensed under the
SIL Open Font License 1.1. See `LICENSE`.

The distribution replaces the legacy `pylopdf-fonts-cjk` name. pylopdf still
detects that package when it is already installed, and the `pylopdf[cjk]`
extra remains as a compatibility alias for `pylopdf[jp]`.
