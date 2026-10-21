---
title: 並行実行とfree-threaded Python
description: Document、Page、Pixmap、並列レンダリング、CPython 3.14tと3.15tでサポートするスレッド境界。
---

# 並行実行とfree-threaded Python

pylopdfは、GILありCPython 3.11–3.15向けのプラットフォーム別`abi3` wheelに加え、free-threaded CPython向けにPythonのバージョンごとのwheel（`cp314-cp314t`と`cp315-cp315t`）を公開します。free-threaded wheelをimportしてもGILは再有効化されません。

## サポート契約 { #support-contract }

| 利用方法 | サポート |
|---|---|
| GILありCPython 3.11–3.15 | `abi3-py311` wheelでサポート |
| free-threaded CPython 3.14tと3.15t | `cp314-cp314t`と`cp315-cp315t` wheelでサポートし、GIL無効の状態でテスト |
| 異なる`Document`に対する並行操作 | サポート。load、save、render、抽出、merge、圧縮の重い処理はGILを解放 |
| 同じ`Document`に対する並行呼び出し・編集 | 非サポート。ロックで直列化するか、独立したDocumentを使用 |
| 1つのDocumentでの`Document.render_pages(workers=...)` | サポート。同一Document内での境界付き並列レンダリング手段 |
| `Pixmap`の並行読み取り | サポート。`Pixmap`は不変 |

`Page`は親`Document`へのビューなので、同一Documentの規則に従います。外部から同時アクセスするとPyO3の実行時borrow検査に拒否される場合がありますが、この検査を同期手段として使わないでください。

## 適切な境界を選ぶ { #choose-the-right-boundary }

独立したファイルには、workerごとに独立したDocumentを持たせます。

```python
from concurrent.futures import ThreadPoolExecutor

import pylopdf


def extract(path: str) -> str:
    with pylopdf.open(path) as document:
        return document.to_markdown()


paths = ["a.pdf", "b.pdf", "c.pdf"]
with ThreadPoolExecutor() as pool:
    results = list(pool.map(extract, paths))
```

1つのDocumentの多数のページを処理する場合は、外部スレッドから同じDocumentを呼び出さず、`render_pages()`を使います。

```python
with pylopdf.open("report.pdf") as document:
    png_pages = document.render_pages(dpi=144, workers=4)
```

`render_pages()`は1つの不変なレンダラsnapshotを使い、指定したページ順と重複を保ち、並行処理の推定作業メモリを約512 MBまでに抑えます。

## 繰り返しレンダリング { #repeated-rendering }

Documentを開いたスレッドで`Page.get_pixmap()`、`Document.get_page_pixmap()`、直列の`render_pages()`を繰り返し呼び出すと、以前の呼び出しで解析したfont、色空間、glyph outlineを再利用します。出力は新しく開いたDocumentと同一です。各スレッドが保持するこのcacheは最大1つで、呼び出し間に保持するのは最大64の異なるページ分です。Documentの編集、代替fontの変更、closeによってcacheは無効になり、開いたスレッドで行った場合やそこでDocumentを破棄した場合は即座に解放されます。他のスレッドから無効にしたcacheは、そのスレッドが次にレンダリングするか終了したときに解放されます。他のスレッドからの呼び出しと並列`render_pages()`のworkerは、呼び出し単位のcacheを使います。

## Pixmapバッファ { #pixmap-buffers }

すべてのwheelが、不変のRGBまたはRGBA画素ストレージをread-only・1次元・ゼロコピーのbufferとして公開します。

```python
view = memoryview(page.get_pixmap())
assert view.readonly and view.format == "B"
```

viewはPixmapを生かし続けるため、NumPyは`np.frombuffer(pixmap, dtype=np.uint8)`で画素をコピーせずに読めます。独立した`bytes`のコピーが必要な場合は、引き続き`pixmap.samples`を使えます。abi3 wheelもfree-threaded wheelも、同じbufferを公開します。
