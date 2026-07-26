---
title: セキュリティ
description: サポート対象、脆弱性の非公開報告、信頼できないPDFをpylopdfで扱うときの指針。
---

# セキュリティ

セキュリティ修正の対象は、PyPIで公開されている最新リリースだけです。

## 脆弱性を報告する { #report-a-vulnerability }

[GitHub Security Advisories](https://github.com/yhay81/pylopdf/security/advisories/new)
から非公開で報告してください。公開Issueは作成しないでください。最初の応答は
1週間以内を目安にしています。

## 信頼できないPDFを扱う { #untrusted-pdfs }

pylopdfはRust製で必須Python依存もありませんが、悪意あるPDF入力の解析には
本質的なリスクがあります。

!!! warning "資源ポリシーを一式指定する"
    `pylopdf.open()`へ`limits=pylopdf.DocumentLimits.web()`を渡してください。
    メモリに制約のあるWeb workerやqueue workerでユーザー入力を扱うための、
    保守的な初期値です。

```python
import pylopdf

try:
    with pylopdf.open(
        "upload.pdf",
        limits=pylopdf.DocumentLimits.web(),
    ) as doc:
        facts = doc.complexity
        preview = doc[0].get_pixmap(dpi=144)
except pylopdf.LimitError as error:
    reject_upload(error.code)
```

Webプロファイルは現在、次の上限を独立に適用します。

| 資源 | 上限 |
|---|---:|
| 入力ファイル | 10 MiB |
| ページ数 | 200 |
| 間接オブジェクト数 | 100,000 |
| 画像のRGBA見積りを含む個々の展開ストリーム | 64 MiB |
| 個々のページ内容ストリーム | 10 MiB |
| ストリームの累積展開／見積りbyte | 128 MiB |
| 直接array／dictionaryの入れ子 | 64 |
| 解釈済みページ全体のUTF-8 glyph payload | 1 MiB |

ワークロードに合わせる場合は`DocumentLimits(...)`を直接作成します。`None`以外は
正の整数でなければなりません。従来の`max_decompressed_size=`はストリーム単位の
短縮指定として残りますが、`limits=`とは同時指定できません。

`LimitError`は`PdfError`のsubclassです。安定した`code`は`file_size`、
`page_count`、`object_count`、`object_depth`、`decompressed_size`、
`page_content_size`、`total_decompressed_size`、`text_size`、
`decompression_unverifiable`のいずれかです。同じ値を`error.args[0]`でも取得できます。
安全に上限計算できないfilter chainは、楽観的に展開せず拒否します。

`doc.complexity`はstreamを展開せずrendererも呼ばずに、ページ数、object数、
stream数、圧縮状態のstream byte数、直接objectの最大深度を返します。重い抽出へ
進む前のroutingに利用できます。構造・展開上限は開いたsourceを検査するため、
生成物が別のtrust boundaryを越えるときは同じポリシーで開き直してください。

寛容な読み込みが修復するのは1つの限定ケースだけです。同じ最終revisionに完全な
classic xref tableがあり、元の上限で全体parseが成功した場合に限り、誤った最終
`startxref`を置き換えます。object headerの走査、xref streamの修復、以前のrevision
への巻き戻しは行いません。修復時は`PylopdfWarning`が発生し、
`doc.is_repaired`（metadata probeでは`repaired`）が`True`になります。保存すると
xref dataを正規化します。

- レンダリングは1ページ64メガピクセルまでです。
- `Page.get_images()`は1ページで4,096配置、累積64,000,000 source画素、返却payload
  64 MiBを超える部分結果を拒否します。Flate-wrapped JPEG passthroughも残りbyte
  上限までしか展開しません。
- 埋め込みJavaScriptは設計上非対応で、実行されません。
- `render_pages()`には通常のメモリ上限制御があるため、application側で無制限の
  並列呼び出しを重ねないでください。
- CPU deadlineはWorker、process、container側で設定してください。資源上限は
  文書化したallocationと出力量を抑えますが、実行中のparserやinterpreterを
  wall-clock時間で中断する機能ではありません。
- 可能なら、信頼できないファイルの一括処理はsandboxやcontainer内で行ってください。
  nativeとPyodideのCIは同じhostile-input回帰契約を共有し、定期Atheris fuzzingは
  壊れたxref、cycle、深いobject、broken stream、圧縮bombをseedにします。

## 依存関係の監査 { #dependency-auditing }

CIはpushごとに`cargo audit`を実行し、Rust依存ツリーをRustSecの脆弱性データベースと
照合します。

リポジトリ上の正本は
[`SECURITY.md`](https://github.com/yhay81/pylopdf/blob/main/SECURITY.md)です。
