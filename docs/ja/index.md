---
title: pyropdf への移行案内
description: pylopdf はアーカイブされました。Python での PDF の読み取り・描画・編集・署名には後継の pyropdf へ移行してください。
hide:
  - navigation
---

<p class="living-eyebrow">Archived project · 最終リリース 0.16.1</p>

# pylopdf から pyropdf への移行 { #migration-notice }

`pylopdf` はアーカイブされ、新機能の追加・不具合修正・セキュリティ更新を終了しました。開発は Rust の基盤から再設計した後継ライブラリ **[pyropdf](https://github.com/yhay81/pyropdf)** に引き継がれています。

<div class="living-actions">
  <a class="living-button living-button--primary" href="https://pyropdf.haya.works/ja/">pyropdf ドキュメント（日本語）&nbsp; →</a>
  <a class="living-button" href="https://github.com/yhay81/pyropdf">GitHub (yhay81/pyropdf)</a>
  <a class="living-button" href="https://pypi.org/project/pyropdf/">PyPI (pyropdf)</a>
</div>

<div class="living-install" aria-label="インストールコマンド">
  <span class="living-install__prompt" aria-hidden="true">$</span>
  <code tabindex="0">pip install pyropdf</code>
  <button
    class="living-install__copy"
    type="button"
    data-copy-command="pip install pyropdf"
    data-copied-label="コピー済み"
    aria-label="インストールコマンドをコピー"
  ><span data-copy-label>コピー</span></button>
</div>

## なぜ pyropdf に置き換わるのか { #why }

`pylopdf` は `lopdf` の編集用グラフ、シリアライズ後のバイト列を読む `hayro`、`krilla` が生成する PDF という 3 つのオブジェクトモデルを併用し、編集のたびに整合を取っていました。そのため、1 箇所の編集でも `hayro` のスナップショット破棄と再シリアライズが走り、トランザクションは文書全体を複製し、`lopdf` が実数を 32 ビット浮動小数点数として保持するため保存時に精度が変化していました。

`pyropdf` は `pylopdf` のコンセプトと Python API の設計を引き継ぎつつ、Rust の**単一の版付きオブジェクトストア**の上に土台から作り直したライブラリです（[設計と計測データ](https://github.com/yhay81/pyropdf/blob/main/DESIGN.md)）。

- **変更した分だけの編集コスト。** すべてのエンジンが 1 つのストア上の現在の版を読むため、編集のたびに文書全体を書き直す必要がありません。
- **版管理と Undo/Redo。** すべての編集は不変の版を作り、失敗したトランザクションはコピーなしで元の版に戻ります。`doc.history` で Undo と Redo も行えます。
- **無変更オブジェクトの保持と変更レポート。** 触っていないオブジェクトは元のバイト列を保ち、`incremental=True` の増分保存は変更オブジェクトだけを追記して既存の電子署名を維持します。保存前に `doc.changes` で署名や PDF/A・PDF/UA 適合宣言への影響を確認できます。
- **機能の拡充。** 電子署名と検証、コンテンツのサニタイズ、色変換、キャンバスでのバリアブルフォント・カラーフォント描画、Arlington PDF Model から生成した型付きオブジェクトなどを備えています。

## 移行手順 { #migrate }

パッケージを入れ替え、`import` を変更します。

```bash
pip uninstall pylopdf
pip install pyropdf
```

```python
import pyropdf

with pyropdf.open("input.pdf") as doc:
    page = doc.pages[0]
    text = page.text()
    hits = page.search("請求書")
    page.pixmap(dpi=150).save("page.png")

    with doc.transaction("annotate"):
        page.annotations.add(pyropdf.Highlight(hits))
    doc.save("output.pdf", incremental=True)
```

- **`pylopdf` 0.16 からの移行:** 文書・ページ・テキスト抽出・レンダリング・キャンバス・注釈・フォーム・しおり・添付ファイル・レイヤーの大半の呼び出しは、`import pylopdf` を `import pyropdf` に変えるだけでそのまま動きます。下記の差分を確認してください。
- **`pylopdf` 0.15 以前または `pymupdf` からの移行:** 機械的な書き換えを先に行いたい場合は、`pylopdf[migrate]==0.16.1` の `python -m pylopdf.migrate src/` で 0.16 形式に変換してから、`import pylopdf` を `import pyropdf` に置き換えてください。

## pylopdf 0.16 との主な違い { #differences }

| 項目 | `pylopdf` 0.16 | `pyropdf` |
|---|---|---|
| import と警告クラス | `import pylopdf`、`PylopdfWarning` | `import pyropdf`、`PyropdfWarning` |
| extra | `ocr`, `jp`, `ko`, `zh-cn`, `zh-tw`, `ar`, `he`, `hi`, `th`, `migrate` | `ocr`, `jp`, `ko`, `zh-cn`, `zh-tw`（同じデータパッケージをそのまま利用）。その他の文字体系のフォントは `pyropdf.Font.from_file(...)` で指定 |
| ページボックス | `page.boxes.media`, `.crop`, `.bleed`, `.trim`, `.art`（PDF ユーザー空間） | `page.media_box`, `page.crop_box`, `page.bleed_box`, `page.trim_box`, `page.art_box`（表示座標系） |
| 新規文書の作成 | `pylopdf.Document()` または `pylopdf.create()` | `doc = pyropdf.Document()` と `doc.pages.new(...)` / `doc.pages.new_from_image(...)`、`page.draw()` に統一 |
| `doc.save()` の既定値 | 既定でストリーム圧縮と未使用オブジェクト削除を実行（`compress=True, prune=True`） | 既定では無変更オブジェクトをそのまま保持（`compress=False, prune=False`）。サイズ削減時は `compress=True, prune=True`、追記時は `incremental=True` を指定 |
| 信頼できない入力 | ネスト検査 + `DocumentLimits.web()` | 上流エンジンの境界付き構築が入るまでは、[状態と安全性](https://pyropdf.haya.works/ja/status/)および[ワーカープロセスでの制限手順](https://pyropdf.haya.works/ja/limits/#reading-a-pdf-in-a-worker)に従ってください |
| WebAssembly | Pyodide 0.28.3 向け wheel を配布 | [pyropdf のロードマップ](https://github.com/yhay81/pyropdf/issues/1)で対応予定。既存の `pylopdf` 0.16 の wheel は引き続き PyPI と GitHub Releases から取得可能 |

## リンク { #links }

- **pyropdf ドキュメント:** [日本語](https://pyropdf.haya.works/ja/) · [English](https://pyropdf.haya.works/) · [`llms.txt`](https://pyropdf.haya.works/ja/llms.txt)
- **pyropdf リポジトリ・Issue:** <https://github.com/yhay81/pyropdf>
- **pyropdf (PyPI):** <https://pypi.org/project/pyropdf/>
- **pyropdf 基盤設計書:** <https://github.com/yhay81/pyropdf/blob/main/DESIGN.md>
- **アーカイブ済み pylopdf リポジトリ・リリース:** <https://github.com/yhay81/pylopdf> · [PyPI (`pylopdf`)](https://pypi.org/project/pylopdf/) · [変更履歴](https://github.com/yhay81/pylopdf/blob/main/CHANGELOG.md)
