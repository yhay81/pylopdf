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

pylopdfはRust製で実行時依存もありませんが、悪意あるPDF入力の解析には本質的な
リスクがあります。

!!! warning "展開サイズの上限を明示する"
    `pylopdf.open()`へ`max_decompressed_size=`を渡してください。レンダラが遅延展開する
    ページ内容も含め、読み取り可能なすべてのストリームをDocument返却前に検査します。

```python
import pylopdf

with pylopdf.open("upload.pdf", max_decompressed_size=128 * 1024 * 1024) as doc:
    preview = doc[0].get_pixmap(dpi=144)
```

- 画像ストリームは展開後のRGBAサイズで制限されます。
- 出力を安全に上限計算できないフィルタチェーンは、制限有効時に拒否されます。
- レンダリングは1ページ64メガピクセルまでです。
- 埋め込みJavaScriptは設計上非対応で、実行されません。
- 可能なら、信頼できないファイルの一括処理はsandboxやcontainer内で行ってください。

## 依存関係の監査 { #dependency-auditing }

CIはpushごとに`cargo audit`を実行し、Rust依存ツリーをRustSecの脆弱性データベースと
照合します。

リポジトリ上の正本は
[`SECURITY.md`](https://github.com/yhay81/pylopdf/blob/main/SECURITY.md)です。
