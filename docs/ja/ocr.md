---
title: オフラインOCR
description: pylopdfに組み込まれたOCRエンジンでスキャンPDFのテキストをローカルで認識するか、ほかのOCRエンジンのテキストを追加します。
---

# オフラインOCR

pylopdfは、スキャンしたページのテキストを認識して不可視のテキスト層として追加できます。これでPDFが検索可能になり、`get_text()`や`to_markdown()`でも読み取れるようになります。モデルパッケージをインストールしてください。

```bash
pip install "pylopdf[ocr]"
```

認識はpylopdf自身のエンジンでローカルに実行され、PP-OCRv6 smallモデルを使います。ネットワーク接続も、Tesseractのようなシステムのプログラムも、ほかのPythonパッケージも不要です。モデルパッケージは約27 MBで、英語、日本語、簡体字・繁体字中国語を含む50言語に対応します。

[WebAssembly](wasm.md)版ではOCRを使えません。

## 編集せずに認識する { #recognize }

`Page.get_text_ocr()`は見つけた単語を返し、文書は変更しません。

```python
import pylopdf

with pylopdf.open("scan.pdf") as doc:
    for word in doc.pages[0].get_text_ocr():
        print(word["bbox"], word["text"], word["confidence"])
```

各`OcrWord`の`bbox`は、テキスト抽出やレンダリングと同じページ座標です。`confidence`は順位付けや絞り込みに役立ちますが、校正された確率ではありません。

## スキャンを検索可能にする { #searchable }

エンジンを1つ作り、すべてのページで再利用します。

```python
import pylopdf

engine = pylopdf.OcrEngine()
with pylopdf.open("scan.pdf") as doc:
    for page in doc.pages:
        page.apply_ocr(engine=engine)
    doc.save("searchable.pdf", dedupe=True, object_streams=True)
```

`apply_ocr()`はページの見た目を変えません。すでにテキストがあるページは読み飛ばすので、同じスクリプトを2回実行してもテキストが二重に追加されることはありません。入力したテキストとスキャン画像が混在するページでは、スキャンした領域を囲む`clip=(x0, y0, x1, y1)`を渡してください。その領域内のテキストだけが既存のテキストとみなされます。それでもOCRテキストを追加したい場合は`skip_existing=False`を渡します。

## ほかのOCRエンジンを使う { #other-engines }

`Page.insert_ocr_text_layer(words)`は、任意のOCRエンジンやクラウドサービスの単語を追加します。各単語はページ座標の`(x0, y0, x1, y1, text)`で、`text`の後の追加項目は無視されます。

```python
words = [(72, 90, 140, 104, "Invoice"), (146, 90, 190, 104, "#1024")]
doc.pages[0].insert_ocr_text_layer(words)
```

このテキスト層はフォントを埋め込まないので、ファイルサイズはほとんど増えません。

## 回転したスキャン { #rotation }

ページが横向きや上下逆さまにスキャンされている場合は、`rotation=90`、`180`、`270`を渡すと、認識の前に画像を時計回りに回転します。

```python
words = page.get_text_ocr(rotation=270)
page.apply_ocr(rotation=270)
```

ページ自体は回転せず、返される矩形もページの座標のままです。不可視テキストは認識した読みの方向に従うので、保存後も検索と抽出が機能します。pylopdfは回転を自動では検出せず、わずかに傾いたスキャンの補正もしません。

## メモリと速度 { #resources }

既定では、ページを300 dpiでレンダリングし、1,408ピクセルの重なり合うタイルに分けて認識します。この設定でのA4ページ1枚のピークは、私たちの計測で約420 MiBでした。メモリを抑えるには、`threads`と`tile_size`を下げてください。

```python
engine = pylopdf.OcrEngine(threads=2)
words = page.get_text_ocr(engine=engine, tile_size=1280, min_confidence=0.6)
```

`clip=`も時間とメモリを節約します。選んだ領域だけを認識するためです。

`OcrEngine`はスレッドや文書の間で共有できます。既定では一度に1件だけ認識する（`max_concurrent=1`）ので、共有してもメモリ使用量は倍増しません。`max_concurrent`は16まで上げられますが、先に計測してください。私たちのテストでは、2件同時でも速くなりませんでした。`Document`自体は引き続き一度に1つのスレッドから使う必要があります。[並行実行](concurrency.md)を参照してください。

## 精度 { #accuracy }

正解テキストがわかっている2件の日本語テスト文書での文字誤り率（CER。低いほど良い）です。「厳密」は空白だけを無視し、「NFKC」はさらに全角と半角の形を同一とみなします。

| 文書 | DPI | 厳密CER | NFKC CER | 時間 |
|---|---:|---:|---:|---:|
| 官公庁の報告書（デジタル、1,188文字） | 150 | 3.788% | 0.842% | 5.50秒 |
| 官公庁の報告書（デジタル、1,188文字） | 300 | 3.704% | 0.842% | 13.87秒 |
| 古い資料のスキャン（384文字） | 150 | 1.823% | 1.562% | 2.05秒 |
| 古い資料のスキャン（384文字） | 300 | 1.302% | 1.042% | 5.37秒 |

比較として、RapidOCR v6の参照値は官公庁の報告書で150 dpiと300 dpiのNFKC CERがそれぞれ0.926%と0.758%でした。pylopdfは150 dpiではより正確で、300 dpiではより不正確でした。時間はハードウェアに依存します。リポジトリで`uv run python bench/ocr.py`を実行するとレポートを再現できます。

## 制限事項 { #limitations }

- 単語の矩形は軸に平行な長方形です。
- ルビ（ふりがな）、割注、縦書きと横書きが混在するテキストは解釈しません。
- 独自のモデルはRTen形式に変換したPP-OCRモデルである必要があります。それらのパスを`OcrEngine(detector_path, recognizer_path, dictionary_path)`に渡してください。

モデルの出所、ハッシュ、Apache-2.0ライセンスは`pylopdf-ocr-models`パッケージに記載しています。
