# WebAssembly互換性

pylopdfは、Pyodide 0.28.3とCloudflare Python Workersが使うPython 3.13 ABI向けに、
静的なPyEmscripten wheelをビルドします。native wheelと同じRust製PDFエンジンを含み、
JavaScript製PDF実装やwasm-bindgen shimには依存しません。

!!! note "リリース状況"

    WebAssemblyビルドは`main`に実装済みで、v0.10.0より後のパッケージリリースから
    公開される予定です。v0.10.0自体にはnative wheelだけが含まれます。

## 配布状況

| 環境 | 状況 | 詳細 |
|---|---|---|
| Cloudflare Python Workers | リリースゲートあり | CIは`workers-py` 1.15.0でPEP 783 wheelを解決し、Wrangler 4.114.0でbundleをdry-runします。タグリリースではPyPIから再取得して同じ検証に通るまでGitHub Releaseを作りません。 |
| Node.js上のPyodide 0.28.3 | 互換性ゲートあり | CIは完全に固定したruntimeへローカルビルドの旧タグwheelを入れ、共有互換スイートを実行します。 |
| PyPIからbrowserへ直接install | 現時点では非対応 | Pyodide 0.28.3の`micropip`は、PyPIが要求するPEP 783の`pyemscripten_*`タグより古い実装です。binaryは互換ですが、安定した公開install経路には新しいfrontend toolingが必要です。 |
| その他のPyodide / Python-Wasm | 未検証 | wheelタグや対応範囲を広げる前にplatformとABIの実測が必要です。 |

公開物のタグは`cp310-abi3-pyemscripten_2025_0_wasm32`です。builderは同じbinaryを
runtime固有の`pyodide_2025_0_wasm32`タグで実行してから、PEP 783向けに決定的に
再タグ付けします。PyPI、provenance attestation、release SBOMへ入るのは
PyEmscriptenタグの成果物だけです。

## テスト済みAPI

native/Wasm共有スイートは、現在次を検証します。

- host filesystemを使わない`bytes`入力、page count、PDF 2.0、AES-256暗号化PDF
- plain text、words、dict、検索、文書Markdown、埋め込み日本語、縦書きCJK推定、
  複数段組、画像のみのpage、回転page
- 罫線表と保守的なborderless表、Markdown統合、vector drawing抽出
- 空文書作成、Standard 14とsubset埋め込みOpenType text、textbox、render、
  `Pixmap`、serialization、仮想filesystemへのsave、merge、並べ替え、複製、select
- `PdfError`、`PasswordError`、`EncryptedDocumentError`、
  `DocumentClosedError`、`StalePageError`と、破損入力後のruntime再利用
- `render_pages(workers=4)`の入力順維持と`workers=1`とのbyte一致

fixtureにはPDF 2.0、埋め込みCJKを持つ日本政府文書、IRS Form 1040、90度回転した
米国上院表、画像のみの日本語scan、合成した縦書き文書を含みます。commitするPDFは
すべて1 MB未満で、再配布可能なlicenseをcorpus READMEに記録しています。

同じスイートをnative wheelとPyodideで1回ずつ実行し、論理結果の完全一致を要求します。
主要文字列と構造の明示的な検証に加えて、抽出全文とMarkdownのhashも比較するため、
緩いsmoke testでは見逃すnative/Wasm差分も検出できます。

## runtime差と制限

- このEmscripten buildにはrayon worker poolがありません。
  `render_pages(workers=...)`は通常の引数を受け取りますが直列に実行します。
  native buildは上限付きrayon並列を維持します。
- pathはbrowserやWorker hostではなくruntimeの仮想filesystemを指します。
  application境界では`Document(stream=data)`と`tobytes()`を推奨します。
- render上限はnativeと同じです。`clip=`は返却pixelを減らしますが、hayroは内部で
  page全体をrasterizeします。
- native OCRと別配布のOCR model packageは、現在のWebAssembly互換契約に含みません。
- 外部CJK fallback fontの自動検出は未保証です。埋め込みCJK PDFは検証済みで、
  applicationからfont bytesを明示的に渡すことはできます。
- 現在のゲートはCloudflare bundle生成までで、認証済みproduction環境へのlive deployではありません。

resource limitと敵対的入力の検証は、この機能互換matrixとは別に追跡します。
native Pythonで通ることを理由に、Wasmでより大きなmemory budgetを仮定しないでください。
