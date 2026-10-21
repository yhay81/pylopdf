---
title: WebAssembly
description: Cloudflare Python WorkersとPyodideでpylopdfを動かす方法と、テスト済みのバージョン、ネイティブのPythonと異なる機能。
---

# WebAssembly

pylopdfは、Cloudflare Python WorkersとPyodideで動くWebAssembly版のwheelを公開しています。ネイティブのwheelと同じRustのコードを含むので、APIも結果も同じです。JavaScriptのPDFライブラリは使っていません。

## 対応環境 { #supported-environments }

| 環境 | 状況 |
|---|---|
| Cloudflare Python Workers | 対応。リリースのたびに、公開したwheelからWorkerをビルドして起動するテストを行います |
| Pyodide 0.28.3 | wheelのタグを付け替えれば対応（後述）。互換性テストスイートをすべて実行しています |
| Pyodide 0.28.3の`micropip`によるPyPIからのインストール | 非対応。この`micropip`はwheelの`pyemscripten`プラットフォームタグを認識しません |
| その他のPyodideやWebAssembly版Pythonのバージョン | 未テスト |

wheelはPython 3.13と`pyemscripten_2025_0_wasm32`プラットフォームを対象とします。Pyodide 0.28.3とCloudflareの`workers-py` 1.15.0が使うランタイムです。ブラウザやWorkerの中でソースからビルドする代替手段はありません。

## Cloudflare Worker { #cloudflare-worker }

リポジトリには、PDFを受け取ってページ数と先頭ページのテキストを返す[テスト済みのWorkerの例](https://github.com/yhay81/pylopdf/tree/main/examples/cloudflare-worker)があります。

```bash
git clone https://github.com/yhay81/pylopdf.git
cd pylopdf/examples/cloudflare-worker
uv sync
uv run pywrangler dev
```

別のターミナルから:

```bash
curl http://localhost:8787/health

curl --request POST \
  --header "content-type: application/pdf" \
  --data-binary @document.pdf \
  http://localhost:8787
```

デプロイは`uv run pywrangler deploy`で行います。pylopdfを含むWorkerはWorkers Freeプランの圧縮後3 MBの上限を超えるため、Workers Paidプランが必要です。

Workerの128 MiBのメモリはPython、JavaScript、リクエスト自体で共有されるため、この例では入力を4 MiBに制限し、`DocumentLimits.web()`より厳しい[`DocumentLimits`](security.md#untrusted-pdfs)を設定しています。pylopdfはリクエストの本文をすべて読み込むので、自分のコードがさらにメモリを必要とするなら上限をもっと下げてください。

## Pyodide { #pyodide }

Pyodide 0.28.3の`micropip`は公開wheelの`pyemscripten`タグを受け付けませんが、中のバイナリはPyodide 0.28.3で動作します。CIは`pyodide_2025_0_wasm32`タグを付けた同じバイナリで全テストを実行しています。wheelをダウンロードし、`wheel`ツールでこのタグに付け替えてください。このツールはwheel内のメタデータも更新します（ファイル名の変更だけでは不十分です）。

```bash
pip download pylopdf --no-deps --only-binary=:all: \
  --platform pyemscripten_2025_0_wasm32 --python-version 3.13 -d wheels
pip install wheel
wheel tags --platform-tag pyodide_2025_0_wasm32 --remove wheels/pylopdf-*.whl
```

付け替えたwheelをPyodideから到達できるURLで配信し、`micropip`でインストールします。

```javascript
const pyodide = await loadPyodide();
await pyodide.loadPackage("micropip");
await pyodide.runPythonAsync(`
import micropip
await micropip.install(
    "https://example.com/pylopdf-0.15.0-cp311-abi3-pyodide_2025_0_wasm32.whl"
)
`);
```

`tools/build_pyodide.sh`でソースからビルドしたwheel（[CONTRIBUTING.md](https://github.com/yhay81/pylopdf/blob/main/CONTRIBUTING.md#pyodide-0283-build)を参照）も同じ`pyemscripten`タグを持つため、同じ手順が必要です。

ファイルパスはPyodideの仮想ファイルシステムを指すので、ブラウザのデータはbytesで渡します。

```python
import pylopdf

with pylopdf.open(stream=pdf_bytes, limits=pylopdf.DocumentLimits.web()) as doc:
    text = doc.get_page_text(0) if doc.page_count else ""
    output = doc.tobytes()
```

WebAssemblyのランタイムかどうかは`sys.platform == "emscripten"`で判定できます。

## ネイティブのPythonとの違い { #differences }

抽出、レンダリング、編集、テキストの生成、フォーム、暗号化など、ほかのすべてはネイティブのPythonと同じように動きます。

| 機能 | WebAssemblyでは |
|---|---|
| `render_pages(workers=...)` | 受け付けますが、ページは1枚ずつ順にレンダリングします |
| 組み込みOCR（`OcrEngine`、`get_text_ocr`、`apply_ocr`） | 使えません。`OcrError`を送出します。OCRはほかの場所で実行し、その結果を`Page.insert_ocr_text_layer()`で追加してください |
| フォントのextra（`pylopdf[jp]`など） | 未テスト。フォントのデータを`fontbuffer=`や`set_fallback_font()`で明示的に渡してください |

大きなページを高解像度でレンダリングするには、PDFファイル自体が小さくても画像全体をメモリに置く必要があります。また、資源の上限は長時間かかる呼び出しを止めません。ネイティブのPythonと同じく、時間制限はホスト側で強制してください。

## サイズとテスト { #size-and-testing }

各リリースでは、同じ互換性テストスイートをネイティブとPyodideの中で実行し、結果が一致することを求めます。テストは、bytesからの読み込み、暗号化されたファイル、テキスト、単語、検索、Markdown、日本語の縦書き、段組みのページ、表、ベクタ図形、レンダリング、標準フォントと埋め込みフォントによるテキストの生成、編集、保存、エラー処理をカバーします。サイズ、起動時間、メモリ使用量はCIで計測し、[`bench/results/wasm-latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/wasm-latest.md)で公開しています。

Pyodide、Emscripten、`workers-py`、Wranglerの新しいバージョンには、完全なスイートが通ってから対応します。
