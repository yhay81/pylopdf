---
title: はじめる
description: pylopdfをインストールし、編集・描画・抽出・書き込みの基本的な使い方を学びます。
---

# はじめる

!!! note "プロジェクトの状況"

    pylopdfは1.0前で、次のリリースでは[ロードマップ](https://github.com/yhay81/pylopdf/blob/main/ROADMAP.md)のとおりAPIを作り直します。何が変わりうるかは[安定性ポリシー](stability.md)で説明しています。このサイトは`main`ブランチに追従しているため、まだPyPIに公開されていない変更を説明していることがあります。各リリースの内容は[変更履歴](https://github.com/yhay81/pylopdf/blob/main/CHANGELOG.md)を参照してください。

## インストール { #installation }

```bash
pip install pylopdf
```

pylopdfはPython 3.11以降が必要で、必須のPython依存はありません。ビルド済みwheelはLinux（x86_64、ARM64）、macOS（Apple silicon、Intel）、Windows（x86_64）に対応し、フリースレッド版のPython 3.14tと3.15tも含みます。WebAssembly版はPyodideとCloudflare Python Workersで動きます。[WebAssembly](wasm.md)を参照してください。

オプションのextraでフォントとOCRモデルを追加できます。必要なものだけをインストールしてください。

| Extra | 追加されるもの |
|---|---|
| `jp`, `ko`, `zh-cn`, `zh-tw` | 日本語・韓国語・簡体字中国語・繁体字中国語のフォント。フォントを埋め込んでいないPDFの表示と、テキストの書き込みに使います |
| `ar`, `he`, `hi`, `th` | アラビア語・ヘブライ語・ヒンディー語（デーヴァナーガリー）・タイ語のフォント。テキストの書き込みに使います |
| `ocr` | 組み込みの[オフラインOCR](ocr.md)エンジン用のモデル |
| `migrate` | [pymupdfからの移行ツール](migration.md#codemod) |

```bash
pip install "pylopdf[jp,ocr]"
```

中国語・日本語・韓国語のextraが分かれているのは、地域ごとに一部の文字の字形が異なるためです。文書に合ったものを選んでください。

## 開く・調べる・保存する { #open-inspect-save }

```python
import pylopdf

with pylopdf.open("input.pdf") as doc:     # pylopdf.open(pdf_bytes) でも可
    print(len(doc.pages))
    print(doc.metadata["title"])
    doc.set_metadata({"title": "Report", "author": "Alice"})

    doc.save("out.pdf")
    data = doc.tobytes()                   # 同じPDFをbytesで
    doc.save("small.pdf", garbage=3, deflate=True, use_objstms=True)
    doc.save(
        "locked.pdf",
        encryption=pylopdf.PDF_ENCRYPT_AES_256,
        user_pw="secret",
        permissions=pylopdf.PDF_PERM_PRINT,
    )
```

- **暗号化されたファイル:** 開くときに`password=`を渡すか、開いた後で`doc.authenticate(password)`を呼びます。
- **メタデータだけ読む:** `pylopdf.probe(path)`は文書全体を読み込まずにページ数、バージョン、メタデータを読みます。大量のファイルを走査するときに便利です。
- **壊れたファイル:** 開くために壊れたファイルを修復した場合、pylopdfは`PylopdfWarning`を出し、`doc.is_repaired`を設定します。保存すると正常なファイルが書き出されます。

!!! tip "信頼できない入手元のファイル"

    自分で管理していないファイルは`limits=pylopdf.DocumentLimits.web()`を付けて開いてください。巨大な入力や悪意のある入力でメモリを使い果たす代わりに、pylopdfが`LimitError`を送出します。[セキュリティ](security.md#untrusted-pdfs)を参照してください。

## ページ・テキスト・検索 { #pages-text-search }

```python
with pylopdf.open("input.pdf") as doc:
    for page in doc.pages:                 # doc.pages[0]、doc.pages[-1] でも可。インデックスは0始まり
        print(page.index, page.rect)

    page = doc.pages[0]
    text = page.get_text()                 # プレーンテキスト
    words = page.get_text("words")         # (x0, y0, x1, y1, 語, ブロック, 行, 語番号)
    layout = page.get_text("dict")         # blocks → lines → spans（bbox・size・font付き）
    hits = page.search_for("total")        # 大文字小文字を区別しない。Rectのリスト
    tables = page.find_tables()
```

座標の単位はポイント（1/72インチ）で、表示されたページの左上が原点です。検索結果、抽出したテキスト、描き込み、レンダリングはすべて同じ座標を使い、回転したページでも一致します。

## レンダリング { #rendering }

```python
with pylopdf.open("input.pdf") as doc:
    doc.pages[0].get_pixmap(dpi=144).save("page-1.png")
    png = doc.pages[0].get_pixmap(dpi=300).tobytes()  # PNGのbytes
    pix = doc.pages[0].get_pixmap(dpi=144)            # NumPyやPillow向けのRGB画素
    pngs = doc.render_pages(dpi=144, workers=4)       # 全ページを並列に
    svg = doc.pages[0].get_svg_image()
```

## ページの編集 { #editing }

```python
with pylopdf.open("input.pdf") as doc:
    doc.pages.select([2, 0, 1])            # 残して並べ替え。同じインデックスを繰り返すと複製
    del doc.pages[1]
    doc.pages.new()                        # 末尾に白紙のA4ページ
    doc.pages.insert(1, doc.pages[0])      # ページ0のコピーをインデックス1に
    doc.pages.move(0, -1)                  # 最初のページを末尾へ移動
    doc.pages[0].set_rotation(90)
    doc.set_outline([(1, "Chapter 1", 0), (2, "Section 1.1", 1)])
    doc.save("edited.pdf")

with pylopdf.Document() as merged, pylopdf.open("a.pdf") as a, pylopdf.open("b.pdf") as b:
    merged.pages.extend(a.pages)           # a.pdfの全ページ
    merged.pages.extend(b.pages[:3])       # b.pdfの最初の3ページ
    merged.save("merged.pdf")
```

ページを追加・削除・並べ替えると、ページのインデックスがずれます。そうした変更の前に取得した`Page`オブジェクトは`StalePageError`を送出するので、`doc.pages[i]`で取得し直してください。

## 描き込みと注釈 { #drawing-annotations }

画像、重ね合わせ、線、注釈:

```python
with pylopdf.open("input.pdf") as doc, pylopdf.open("letterhead.pdf") as letterhead:
    page = doc.pages[0]
    page.insert_image((72, 72, 200, 200), filename="logo.png")
    page.show_pdf_page(page.rect, letterhead)       # 別のPDFのページ0をベクタのまま
    page.draw_line((36, 340), (400, 340), width=0.5, dashes="[3 2] 0")

    hits = page.search_for("important")
    if hits:
        page.add_highlight_annot(hits)
    for rect in page.search_for("Example")[:1]:
        page.insert_link({"kind": pylopdf.LINK_URI, "from": rect, "uri": "https://example.com/"})
    doc.save("annotated.pdf")
```

テキストとフォント。フォントを指定しない場合、pylopdfはラテン文字をHelveticaで書き、それ以外の文字は`pylopdf[jp]`のようなインストール済みextraのフォントで書きます。

```python
with pylopdf.open("input.pdf") as doc:
    page = doc.pages[0]
    page.insert_text((40, 40), "CONFIDENTIAL", fontsize=18, color=(1, 0, 0))
    page.insert_text((40, 70), "社外秘", fontsize=18)                          # pylopdf[jp]が必要
    page.insert_text((40, 100), "简体中文", fontsize=18, font_language="zh-CN")  # pylopdf[zh-cn]が必要

    bold = pylopdf.Font(fontfile="NotoSansJP-Bold.otf")       # 任意のOpenTypeファイル
    width = bold.text_length("見出し", fontsize=16)             # 描いたときの幅
    page.insert_text((page.rect.width - 40 - width, 140), "見出し", font=bold, fontsize=16)
    doc.save("text.pdf")
```

図形、テキスト、画像を順番どおりにまとめて描くには、キャンバスを使います。各フォントと画像は1回だけ格納されます。

```python
from pylopdf import Color

with pylopdf.open("input.pdf") as doc:
    with doc.pages[0].draw() as canvas:
        canvas.rect((36, 220, 400, 320), radius=6, fill=Color(0.95, 0.97, 1), stroke=Color(0.2, 0.4, 0.6))
        canvas.text("Report", (48, 250), size=16)
        canvas.textbox((48, 260, 390, 310), "Body paragraph.", size=11)
        with canvas.opacity(0.3):
            canvas.image((300, 230, 390, 300), "logo.png")
    doc.save("canvas.pdf")
```

タグ付きPDF/UAファイルのような新しい文書を書き出すには、[`pylopdf.create()`](api.md#create)を使います。そのページも同じキャンバスで描きます。

## スキャン・フォーム・Markdown { #scans-forms-markdown }

```python
with pylopdf.open("scan.pdf") as doc:
    doc.pages[0].apply_ocr()                        # pylopdf[ocr]が必要。検索可能な不可視テキストを追加
    doc.save("searchable.pdf")

with pylopdf.open("form.pdf") as doc:
    print(doc.get_form_fields())
    doc.set_form_field("customer", "Alice")   # 値とその見た目を書き込む
    doc.save("filled.pdf")

with pylopdf.open("input.pdf") as doc:
    md = doc.to_markdown()                    # 見出し、リスト、表
```

OCRの詳細は、別のOCRエンジンで認識したテキストを追加する方法も含めて[オフラインOCR](ocr.md)で説明しています。

## まだ対応していないもの { #not-supported }

墨消し（redaction）、注釈の編集・削除、フォームフィールドの作成、増分保存、検証済みのPDF/AとPDF/UAの出力は予定しています。内容は[ロードマップ](https://github.com/yhay81/pylopdf/blob/main/ROADMAP.md)にあります。pylopdfが開けるのはPDFファイルだけです。

HTMLとCSSによる組版と電子署名は、エコシステムのツールに任せます。pylopdfをtypstやpyHankoと組み合わせる方法を[エコシステム連携](ecosystem.md)で紹介しています。

## 次のステップ { #next-steps }

- すべてのクラスとメソッドは[APIリファレンス](api.md)へ
- 既存のコードを移植する場合は[pymupdfからの移行](migration.md)へ
- バグ報告や機能の要望は[コントリビュートとサポート](contributing.md)へ
