---
title: 並行実行とフリースレッド版Python
description: 複数のスレッドからpylopdfを使う方法、ページを並列にレンダリングする方法、フリースレッド版CPython 3.14tと3.15tでの実行について。
---

# 並行実行とフリースレッド版Python

pylopdfは、開く・保存・レンダリング・抽出・結合・圧縮などの重い処理の間はGILを解放するので、通常のCPythonでもスレッドを並列に実行できます。フリースレッド版CPython 3.14tと3.15tには専用のwheelがあり、GILを無効にしたまま動作します。

## サポート範囲 { #support-contract }

| 使い方 | サポート |
|---|---|
| 異なるスレッドが異なる`Document`オブジェクトを扱う | 対応 |
| 複数のスレッドが同じ`Document`を同時に呼び出す・編集する | 非対応。ロックを使うか、スレッドごとにファイルを開いてください |
| 1つの文書で`Document.render_pages(workers=...)` | 対応。1つの文書を並列にレンダリングするための、サポートされた方法です |
| 複数のスレッドが同じ`Pixmap`を読む | 対応。`Pixmap`は変更できません |
| フリースレッド版CPython 3.14tと3.15t | 対応。GILを無効にした状態でテストしています |

`Page`はその`Document`に属し、同じ規則に従います。pylopdfが1つの文書への同時呼び出しを拒否することはありますが、それをロックの代わりとして当てにしないでください。

## 多数のファイルを処理する { #many-files }

各ワーカーにそれぞれの文書を持たせます。

```python
from concurrent.futures import ThreadPoolExecutor

import pylopdf


def extract(path: str) -> str:
    with pylopdf.open(path) as document:
        return document.to_markdown()


with ThreadPoolExecutor() as pool:
    results = list(pool.map(extract, paths))
```

## 1つの文書の多数のページをレンダリングする { #render-pages }

1つの文書を複数のスレッドから呼び出す代わりに、`render_pages()`を使います。

```python
png_pages = document.render_pages(dpi=144, workers=4)
```

要求したページの順にPNGのbytesを返します。ページが大きいときはワーカー数を減らして、見積もった作業メモリを約512 MB以下に保ちます。

## 繰り返しのレンダリング { #repeated-rendering }

1つの文書のページを、その文書を開いたスレッドで何度もレンダリングすると、以前の呼び出しで解析したフォントとグリフの輪郭を再利用します。出力は新しく開いた文書からの出力と同じです。このキャッシュは最大64ページ分で、文書を編集するか閉じると解放されます。

## PixmapとNumPy { #pixmap-buffers }

`Pixmap`は画素を読み取り専用のバッファとして公開するので、NumPyはコピーせずに使えます。

```python
import numpy as np

pix = page.get_pixmap()
pixels = np.frombuffer(pix, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
```

`pix.samples`は代わりに独立した`bytes`のコピーを返します。
