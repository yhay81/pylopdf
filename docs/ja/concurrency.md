---
title: 並行実行とfree-threaded Python
description: Document、Page、Pixmap、並列レンダリング、CPython 3.14tでサポートするスレッド境界。
---

# 並行実行とfree-threaded Python

v0.10以降のpylopdfは、GILありCPython 3.10–3.14向けのプラットフォーム別`abi3`
wheelに加え、free-threaded CPython 3.14向けの`cp314-cp314t` wheelを公開します。
後者をimportしてもGILは再有効化されません。

## サポート契約 { #support-contract }

| 利用方法 | サポート |
|---|---|
| GILありCPython 3.10–3.14 | `abi3-py310` wheelでサポート |
| free-threaded CPython 3.14t | `cp314-cp314t` wheelでサポートし、GIL無効の状態でテスト |
| 異なる`Document`に対する並行操作 | サポート。load、save、render、抽出、merge、圧縮の重い処理はGILを解放 |
| 同じ`Document`に対する並行呼び出し・編集 | 非サポート。ロックで直列化するか、独立したDocumentを使用 |
| 1つのDocumentでの`Document.render_pages(workers=...)` | サポート。同一Document内での境界付き並列レンダリング手段 |
| `Pixmap`の並行読み取り | サポート。`Pixmap`は不変 |

`Page`は親`Document`へのビューなので、同一Documentの規則に従います。外部から
同時アクセスするとPyO3の実行時borrow検査に拒否される場合がありますが、この検査を
同期手段として使わないでください。

## 適切な境界を選ぶ { #choose-the-right-boundary }

独立したファイルには、workerごとに独立したDocumentを持たせます。

```python
from concurrent.futures import ThreadPoolExecutor

import pylopdf


def extract(path: str) -> str:
    with pylopdf.open(path) as document:
        return document.to_markdown()


with ThreadPoolExecutor() as pool:
    results = list(pool.map(extract, paths))
```

1つのDocumentの多数のページを処理する場合は、外部スレッドから同じDocumentを
呼び出さず、`render_pages()`を使います。

```python
png_pages = document.render_pages(scale=2, workers=4)
```

`render_pages()`は1つの不変なレンダラsnapshotを使い、指定したページ順と重複を保ち、
並行処理の推定作業メモリを約512 MBまでに抑えます。

## Pixmapバッファ { #pixmap-buffers }

free-threaded wheelでは、不変のRGBA8ストレージをread-only・1次元・ゼロコピーの
bufferとして公開します。

```python
view = memoryview(page.get_pixmap())
assert view.readonly and view.format == "B"
```

`Py_buffer`はPython 3.11でstable ABIに加わったため、`abi3-py310` wheelからは
公開できません。そのwheelでは1回コピーする`bytes`値の`pixmap.samples`を使います。
これによりbufferの寿命と不変性を崩さずPython 3.10互換性を保ちます。
