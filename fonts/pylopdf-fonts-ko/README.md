# pylopdf-fonts-ko

A data-only package containing Korean fallback and generation fonts for
[pylopdf](https://github.com/yhay81/pylopdf). It renders non-embedded
Adobe-Korea1 fonts and lets generated Hangul text select a locale-matching
subset-embedded font automatically.

Install it through the main package extra:

```bash
pip install "pylopdf[ko]"
```

## Bundled fonts

| File | Typeface | Source SHA-256 |
|---|---|---|
| `NotoSansKR-Regular.otf` | Noto Sans KR | `69975a0ac8472717870aefeab0a4d52739308d90856b9955313b2ad5e0148d68` |
| `NotoSerifKR-Regular.otf` | Noto Serif KR | `5ea012e15cb7eacc1f680aee1703f3b164791b1443ea3e52b65080cca5d179cf` |

The files come from
[`notofonts/noto-cjk`](https://github.com/notofonts/noto-cjk) revision
`f8d157532fbfaeda587e826d4cd5b21a49186f7c`, under `Sans/SubsetOTF/KR`
and `Serif/SubsetOTF/KR`. Both are licensed under the SIL Open Font License
1.1. See `LICENSE`.
