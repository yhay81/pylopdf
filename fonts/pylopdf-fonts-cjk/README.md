# pylopdf-fonts-cjk

[pylopdf](https://github.com/yhay81/pylopdf) 用の CJK（日本語）fallback フォントを同梱する
データ専用パッケージ。フォントが埋め込まれていない日本語 PDF をレンダリングするために使う。

通常は直接インストールせず、extra 経由で入れる:

```bash
pip install pylopdf[cjk]
```

インストールされていれば pylopdf が自動検出し、非埋め込み CID フォント
（例: MS-Mincho / Ryumin-Light / MS-Gothic の参照だけがある PDF）の描画に
明朝系 → Noto Serif JP、それ以外 → Noto Sans JP を割り当てる。

## 同梱フォント

| ファイル | 書体 | 取得元 |
|---|---|---|
| NotoSansJP-Regular.otf | Noto Sans JP（ゴシック） | [notofonts/noto-cjk](https://github.com/notofonts/noto-cjk) Sans/SubsetOTF/JP |
| NotoSerifJP-Regular.otf | Noto Serif JP（明朝） | 同 Serif/SubsetOTF/JP |

ライセンスはいずれも SIL Open Font License 1.1（LICENSE を参照）。
