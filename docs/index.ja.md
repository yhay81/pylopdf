# pylopdf

Rust 製の PDF 編集・抽出・レンダリングライブラリ。編集は
[lopdf](https://github.com/J-F-Liu/lopdf)、レンダリングは
[hayro](https://github.com/LaurenzV/hayro)（typst が採用する純 Rust PDF レンダラ）が
担います。**MIT ライセンス・実行時依存ゼロ・約 3.5 MB の軽量 wheel。**

```bash
pip install pylopdf
```

```python
import pylopdf

doc = pylopdf.open("input.pdf")
text = doc.get_page_text(0)
png = doc.render_page(0, dpi=300)
doc.save("out.pdf", garbage=True, deflate=True)
```

## pylopdf を選ぶ理由

|  | pylopdf | pymupdf | pypdf | pypdfium2 |
|---|---|---|---|---|
| ライセンス | **MIT** | AGPL / 商用 | BSD | Apache/BSD |
| wheel サイズ | **約 3.5 MB** | 約 40 MB+ | 軽量（純 Python） | 約 8 MB |
| 編集（結合・分割・回転・しおり） | ✅ | ✅ | ✅ | 限定的 |
| レンダリング（PNG / SVG） | ✅ | ✅ | ❌ | ✅（PNG） |
| 位置付きテキスト抽出・検索 | ✅ | ✅ | 部分的 | ✅ |
| Markdown 変換（RAG 向け） | ✅ 内蔵 | 別パッケージ（AGPL） | ❌ | ❌ |
| 暗号化（AES-256） | ✅ 読み書き | ✅ | ✅ | ❌ |
| CJK フォント fallback | ✅（`[cjk]` extra） | ✅ | — | 手動 |
| 実装 | **純 Rust** | C | Python | C++ (PDFium) |

- AWS Lambda などサイズ制約のある環境にそのまま載る
- AGPL を避けたい商用プロジェクトで使える
- abi3 対応: Python 3.10〜3.14 を単一 wheel でサポート
- pymupdf に近い操作感の API — [移行ガイド](migration.md)を参照
- [再現可能なベンチマーク](https://github.com/yhay81/pylopdf/blob/main/bench/results/latest.md)
  を勝ち負け両方掲載で公開

## 設計原則

pylopdf は「編集・抽出・レンダリングの軽量コア」に集中し、隣接領域は実績ある
ライブラリとの連携で解決します — 組版と PDF/A は typst、電子署名は pyHanko、
PDF/A 検証は veraPDF。すべてのレシピは統合テストで保証しています。
詳しくは[エコシステム連携](ecosystem.md)へ。

## リンク

- [PyPI](https://pypi.org/project/pylopdf/)
- [GitHub](https://github.com/yhay81/pylopdf)
- [変更履歴](https://github.com/yhay81/pylopdf/blob/main/CHANGELOG.md)
- [セキュリティポリシー](https://github.com/yhay81/pylopdf/blob/main/SECURITY.md)
