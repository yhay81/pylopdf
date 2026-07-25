# オフラインOCR

pylopdfは、スキャンページをローカルで認識し、非表示の検索可能なテキスト層を追加できます。任意のモデルパッケージをインストールします。

```bash
pip install "pylopdf[ocr]"
```

コア拡張は、純RustのRTenランタイムでPP-OCRv6 smallを実行します。実行時にシステム実行ファイル、共有ライブラリ、ネットワーク通信、ONNXパーサーは不要です。独立してバージョン管理されるモデルwheelは約26.6 MBで、日本語、簡体字・繁体字中国語、英語を含む50言語に対応します。

## 編集せずに認識する

`Page.get_text_ocr()`は、文書を変更せず位置付きの単語を返します。

```python
import pylopdf

with pylopdf.open("scan.pdf") as doc:
    words = doc[0].get_text_ocr()
    for word in words:
        print(word["bbox"], word["text"], word["confidence"])
```

各`OcrWord`の`Rect`は、描画や抽出と同じ、回転を解決した左上原点の表示座標です。`confidence`は認識結果を順位付けする決定論的な指標であり、校正済み確率ではありません。

## スキャンを検索可能にする

1つのエンジンを読み込み、ページ間で再利用します。

```python
import pylopdf

engine = pylopdf.OcrEngine(threads=4, max_concurrent=1)
with pylopdf.open("scan.pdf") as doc:
    for page in doc:
        page.apply_ocr(engine=engine)
    doc.save("searchable.pdf", garbage=3, deflate=True, object_streams=True)
```

`apply_ocr()`は描画ピクセルと既存のページ内容を保持します。既定では抽出可能なテキストがあるページをスキップするため、パイプラインを再実行しても非表示層は重複しません。混在ページでは、スキャン領域を表示座標の`clip=(x0, y0, x1, y1)`で指定します。その領域と交差する既存テキストだけがスキップ判定の対象です。交差するテキストがあっても追記する場合に限り`skip_existing=False`を指定します。

## リソース制御

既定値は300 dpi、1,408ピクセルの検出タイル、192ピクセルの重なり、最大4本のRTenワーカースレッド、エンジンごとに同時実行する完全な認識呼び出し1件です。重なり付きタイルにより、ページ全体の検出メモリを制限しながら境界の重複検出を統合します。ある300 dpiのA4実測では既定構成のピークは約419 MiBでしたが、文書、プラットフォーム、アロケーターによって変化します。

メモリを抑える場合は、`threads`と`tile_size`を下げます。`max_concurrent`は、同時に存在するラスターデータと推論バッファーを測定した後にだけ引き上げてください。

```python
engine = pylopdf.OcrEngine(threads=2, max_concurrent=1)
words = page.get_text_ocr(
    engine=engine,
    tile_size=1280,
    overlap=192,
    min_confidence=0.6,
)
```

`clip`はOCR検出器への入力と認識処理を減らしますが、hayro 0.7は切り出し前にページ全体を描画します。返される矩形はページ全体の表示座標のままです。

`OcrEngine`は不変で、異なる文書間で再利用できます。既定の`max_concurrent=1`は、free-threaded Pythonからの呼び出しも含め、描画から認識完了までを直列化するため、共有したエンジンが実測済みの呼び出し単位メモリを意図せず倍増させません。最大16まで引き上げられますが、対象workloadを測定した場合に限ってください。許可された各呼び出しは個別のラスターデータと推論バッファーを持ちます。同じ`Document`への外部スレッドからの同時呼び出しや編集は、pylopdfの並行処理契約の対象外です。

## 実測した精度ゲート

追跡・再配布可能な厚労省fixtureから1,188文字の正解データを抽出し、ネイティブpipelineを測定しました。

| DPI | 厳密CER | NFKC CER | 処理時間 |
|---:|---:|---:|---:|
| 150 | 3.788% | 0.842% | 5.71秒 |
| 300 | 3.704% | 0.842% | 11.93秒 |

RapidOCR v6参照値のNFKC CERはそれぞれ0.926%と0.758%で、pylopdfの150 dpiでの勝ちと300 dpiでの負けをともに残しています。厳密CERは空白だけを除外し、NFKC CERはさらに全角英数字などの互換文字を正規化します。処理時間はハードウェアに依存します。完全なレポートは`uv run python bench/ocr.py`で再現できます。

## モデルとレイアウトの境界

パスを省略すると、`OcrEngine`は`pylopdf[ocr]`がインストールした検証済みモデルセットを検出します。上級者は、互換性のあるRTen形式のPP-OCR検出器、認識器、辞書を明示できます。

最初のネイティブエンジンが返す単語矩形は軸平行です。任意角度の傾き補正、横向きページの自動検出、ルビ・割注・混在方向の組版解釈にはまだ対応していません。横向きのスキャンはOCRの前にページ回転を明示してください。PP-OCRv6モデルの出所、元データと成果物のハッシュ、変換手順、Apache-2.0表記は`pylopdf-ocr-models`配布物に含まれます。
