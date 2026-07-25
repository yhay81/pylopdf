# WebAssembly互換性

pylopdfは、Pyodide 0.28.3とCloudflare Python Workersが使うPython 3.13 ABI向けに、
static PyEmscripten wheelをbuildします。native wheelと同じRust製PDF engineを含み、
JavaScript製PDF実装やwasm-bindgen shimには置き換えていません。

!!! note "リリース状況"

    WebAssembly wheelはpylopdf 0.11で初めて配布します。v0.10.0はnative wheelのみです。
    v0.11公開前は、このページに記載した公開install commandが解決しないのが正常です。

## 検証済み環境

| Component | 固定versionまたは契約 |
|---|---|
| Python runtime | CPython 3.13.2 |
| Pyodide | 0.28.3 |
| Emscripten toolchain | 4.0.9 |
| Wheel platform | PyEmscripten `2025_0`、`wasm32` |
| Build/smoke用Node.js | 固定Emscripten SDKの20.18.0 |
| Cloudflare SDK | `workers-py` 1.15.0 |
| Cloudflare bundler | Wrangler 4.114.0 |
| Worker compatibility date | 2026-07-26 |

公開artifactは`cp310-abi3-pyemscripten_2025_0_wasm32`です。builderは同じbinaryを
runtime固有の`pyodide_2025_0_wasm32` tagで先に実行し、PEP 783公開用に決定的に
retagします。PyPI、provenance attestation、release SBOMへ入るのは
PyEmscripten tagのartifactだけです。

| 環境 | 状況 | 詳細 |
|---|---|---|
| Cloudflare Python Workers | 対応・release gateあり | CIが固定SDKでPEP 783 wheelを解決し、Wrangler bundleをdry-runします。tag releaseではPyPIから同じ検証を繰り返してからGitHub Releaseを作ります。 |
| Node.js上のPyodide 0.28.3 | runtime互換gateあり | CIがruntime tagのlocal wheelを固定runtimeへinstallし、共有互換suiteをすべて実行します。 |
| PyPIからbrowserへ直接install | Pyodide 0.28.3では非対応 | 同版の`micropip`はPyPIが要求するPEP 783 `pyemscripten_*` tagより古い実装です。binaryは互換ですが、このfrontend install経路は互換ではありません。 |
| その他のPyodide / Python-Wasm | 未検証 | wheel tagや対応範囲を広げる前にplatformとABIを検証します。 |

## Cloudflare Worker

repositoryには
[検証済みの抽出Worker](https://github.com/yhay81/pylopdf/tree/main/examples/cloudflare-worker)
があります。上限を設定したPDF bodyを受け、page数と先頭pageのtextを返します。

```bash
git clone https://github.com/yhay81/pylopdf.git
cd pylopdf/examples/cloudflare-worker
uv sync
uv run pywrangler dev
```

別terminalから送信します。

```bash
curl --request POST \
  --header "content-type: application/pdf" \
  --data-binary @document.pdf \
  http://localhost:8787
```

選択したCloudflare planの上限とcompatibility dateを確認してから
`uv run pywrangler deploy`を使います。CIはこのexample自体をcopyし、公開版pylopdfの
requirementだけを直前にbuildしたwheelへ差し替え、`workers-py`で解決して
`wrangler deploy --dry-run`を実行します。

exampleは入力を4 MiBに制限し、structureと展開後dataには`DocumentLimits.web()`より
厳しいbudgetを設定します。pylopdfの入力はpathまたは完全なbytesなので、request bodyは
全体をbufferします。Cloudflareの128 MiB isolate budgetにはPython、JavaScript、
WebAssembly linear memory、request bufferも含まれるため、周辺処理がmemoryを使う場合は
file budgetをさらに下げてください。

## Pyodideから直接使う

Pyodide 0.28.3での開発時は`tools/build_pyodide.sh`でbuildし、runtimeから見えるURLに
置いたruntime tagのwheelをinstallします。

```javascript
const pyodide = await loadPyodide();
await pyodide.loadPackage("micropip");
await pyodide.runPythonAsync(`
import micropip
await micropip.install(
    "https://example.invalid/pylopdf-0.11.0-"
    "cp310-abi3-pyodide_2025_0_wasm32.whl"
)
`);
```

URLは例です。release artifactはPyPIとCloudflare向けのPEP 783
`pyemscripten_2025_0_wasm32` tagを使い、Pyodide 0.28.3の古い`micropip`はその
公開tagを受理しません。wheel名だけを変更して、内部の`WHEEL` metadataを残す運用は
しないでください。

browserやWorker内でsdistへfallbackする経路はsupportしません。extensionのbuildには
固定したRust、Emscripten、Pyodide cross environment、retag verifierが必要です。
将来のABIに一致するwheelがなければ、runtime package installerへsdistをbuildさせず、
そのABIは未対応として扱ってください。

runtimeは`sys.platform == "emscripten"`で判定できます。pathはbrowser fileへ直接
つながるものではなく、virtual filesystem上のpathです。

```python
import sys
import pylopdf

assert sys.platform == "emscripten"
with pylopdf.Document(stream=pdf_bytes, limits=pylopdf.DocumentLimits.web()) as doc:
    text = doc.get_page_text(0) if doc.page_count else ""
    output = doc.tobytes()
```

## 検証済みAPI

native/Wasm共有suiteは、現在次を検証します。

- host filesystemを使わない`bytes`入力、page数、PDF 2.0、AES-256暗号化入力
- plain text、word、dict、検索、document Markdown、埋め込み日本語、推定vertical CJK、
  持続的なmulti-column順序、画像だけのpage、回転page
- borderedおよび保守的なborderless table、Markdown統合、vector drawing抽出
- 空document生成、Standard 14とsubset埋め込みOpenType text、textbox layout、
  rendering、`Pixmap`、serialization、virtual filesystemへの保存、merge、reorder、
  duplicate、select
- `PdfError`、stable resource codeを持つ`LimitError`、`PasswordError`、
  `EncryptedDocumentError`、`DocumentClosedError`、`StalePageError`と、
  malformed input後のruntime再利用
- `render_pages(workers=4)`の入力順序と`workers=1`とのbyte一致

fixtureにはPDF 2.0、CJKを埋め込んだ日本政府文書、IRS Form 1040、回転した米国上院表、
画像だけの日本語scan、生成した縦書き文書が含まれます。repository内のPDFはすべて
1 MiB未満で、再配布可能なlicenseをcorpus READMEに記録しています。

同じsuiteをnative wheelとPyodideで1回ずつ実行し、論理結果の完全一致を要求します。
明示的なstructure・期待textと、抽出全文・Markdown hashの両方を検証します。

## 機能と依存関係

Wasm wheelは、部分的に互換な複数variantへ分割せず、1 artifactを維持します。

| 機能 | Rust component | Wasmでの状態 |
|---|---|---|
| PDF structure、編集、暗号化 | lopdf | 含む |
| text、table、vector path | hayro syntax/interpreterとCMap | 含む |
| PNG raster rendering | hayro、Vello、PNG encoding | 含む |
| SVG rendering | hayro SVG backend | 含む |
| 生成textとform appearance | krilla、HarfRust、read-fonts、UAX line breaking | 含む |
| 画像とJPEG圧縮 | Flate、zune-jpeg、jpeg-encoder | 含む |
| 同一document内の並列render | rayon | nativeのみ。Wasmはserial実行 |
| PP-OCRv6 inference | RTenと外部model wheel | nativeのみ。Wasm binaryから除外 |
| CJK fallback自動探索 | 外部CJK font wheelとhost path | Wasm互換契約外 |

capability判定を決定的にするため`OcrEngine()`自体は存在しますが、Emscriptenでは
`OcrError`を送出し、Wasm外でOCRして`Page.insert_ocr_text_layer()`を使うよう案内します。
未使用のRTen inference runtimeを除いても、PDFの抽出・render・生成や、外部OCR textの
挿入は削除されません。

## 実測したdeployment範囲

固定CI artifactは3.834 MiBのwheelと10.404 MiBの展開後Wasm extensionです。検証した
Worker bundleは圧縮後3.882 MiB、展開後10.844 MiBでした。そのためCloudflare Workers
Freeの圧縮後3 MB上限は超えますが、paid planの圧縮後10 MBと共通の展開後64 MB上限には
収まります。pylopdfはpaid planへのdeploy経路をsupportし、機能を減らした別distributionは
配布しません。

Node/Pyodide harnessではForm 1040の初回open・extractが116.267 ms、5回反復のmedianが
26.893 msでした。Wasm linear memoryはinstall後40.375 MiB、互換性・resource suite完了後
70.625 MiBです。これらは再現可能なCI trendであり、Cloudflareのrequest latencyやisolate
resident memoryの測定ではありません。詳細は
[size・startup report](https://github.com/yhay81/pylopdf/blob/main/bench/results/wasm-latest.md)
を参照してください。

## Runtime上の制約

- このEmscripten buildにはrayon worker poolがありません。
  `render_pages(workers=...)`は通常の引数を受けますがserial実行します。
- `clip=`は返却pixelを減らしますが、hayroは内部でpage全体をrasterizeします。
- 現在のrendererは完全なraster outputをbufferするため、大きなpageや高DPIではPDF file
  sizeが小さくてもmemoryを大きく使います。
- native OCRと別配布のOCR model packageはWebAssembly互換契約外です。
- 外部CJK fallback fontの自動探索は対象外です。埋め込みCJKは検証し、applicationから
  font bytesを明示的に渡すことはできます。
- 現在のgateはCloudflare bundle生成までを検証し、認証済みproduction deployや
  workload固有latencyを保証しません。

同じmatrixがnativeとWasmの両方で`DocumentLimits`、`doc.complexity`、Web budget内の
vector/scan入力、file/page/textのstable rejection codeを検証します。定期native
Atheris fuzzingはさらに大きな生成hostile corpusを加えます。CPU deadlineはhost側の
責任です。nativeで通るPDFにWasmでより大きいmemory budgetがあるとは推定せず、両方で
明示的なpolicyを使ってください。

## Supportとrelease policy

Wasm wheelを配布する各pylopdf releaseは、次をすべて通します。

1. 再現可能buildとwheel metadata/import verifier
2. native/Pyodide共有の論理互換suite
3. untrusted inputの拒否とresource trend検査
4. wheel、Wasm section、startup/workload、linear memoryの測定
5. local wheelからのdependency解決とCloudflare Wrangler dry-run
6. GitHub Release確定前のPyPI artifactからの同じ解決・dry-run

runtime更新は互換と仮定せず、新しい検証matrixとして扱います。固定versionは対応する
pylopdf minor releaseでsupportし、新しいPyodide、PyEmscripten、Emscripten、
`workers-py`、Wranglerは完全なgateを通ってから対応範囲へ入れます。測定値とregressionは
[`bench/results/wasm-latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/wasm-latest.md)
へ併記します。
