---
title: はじめる
description: pylopdfをインストールし、編集・レンダリング・抽出・描き込みの基本的な流れを学びます。
---

# はじめる

## インストール { #installation }

```bash
pip install pylopdf
```

用途に必要な言語・文字体系のフォントだけを導入します。

```bash
pip install "pylopdf[jp]"     # 日本語のフォールバックと生成
pip install "pylopdf[ko]"     # 韓国語
pip install "pylopdf[zh-cn]"  # 簡体字中国語
pip install "pylopdf[zh-tw]"  # 繁体字中国語
pip install "pylopdf[ar]"     # アラビア語の生成（he、hi、thも選択可）
```

CJKの4つのextraは、埋め込まれていないAdobe CJKフォントのレンダリングと、サブセット埋め込みによる生成の両方に対応します。アラビア語、ヘブライ語、デーヴァナーガリー、タイ語のパッケージは生成用の提供元です。必要な地域の字形に合わせて`jp`、`ko`、`zh-cn`、`zh-tw`を明示的に選択してください。CJK共通のextraはありません。

ビルド済みwheelは、Linux（x86_64、ARM64）、macOS（Apple silicon、Intel）、Windows（x86_64）のCPython 3.11–3.15と、free-threadedの3.14tと3.15tに対応します。free-threadedでの利用は[並行実行](concurrency.md)、PyodideとCloudflare Python Workersは[WebAssembly](wasm.md)を参照してください。

## 開く・調べる・保存する { #open-inspect-save }

```python
import pylopdf

with pylopdf.open("input.pdf") as doc:     # pylopdf.open(stream=pdf_bytes) でも可
    print(doc.page_count)                  # len(doc) でも同じ
    print(doc.metadata["title"])
    doc.set_metadata({"title": "Report", "author": "Alice"})

    doc.save("out.pdf")
    data = doc.tobytes()                   # 同じPDFをbytesで取得
    doc.save("small.pdf", garbage=True, deflate=True, use_objstms=True)
    doc.save(
        "locked.pdf",
        encryption=pylopdf.PDF_ENCRYPT_AES_256,
        user_pw="secret",
        permissions=pylopdf.PDF_PERM_PRINT,
    )
```

- **暗号化されたファイル**は`password=`を指定して開くか、開いた後に`doc.authenticate()`を呼び出します。
- **メタデータだけを読む場合:** `pylopdf.peek_metadata(path)`は文書全体を解析せずにメタデータとページ数を読むため、大量のファイルの走査に向いています。`max_file_size=`を指定すると、大きすぎる入力を解析前に拒否します。
- **破損したファイル:** 壊れた相互参照データを復旧した場合、pylopdfは`PylopdfWarning`を発生させ、`doc.is_repaired`（`peek_metadata`では`repaired`）を設定します。保存すると正常な相互参照データが書き出されます。

!!! tip "信頼できない入力"

    自分で管理していないファイルを開くときは`limits=pylopdf.DocumentLimits.web()`を指定してください。ファイルサイズ、構造、展開、解釈済みテキストを制限し、メモリを使い果たす代わりに`LimitError`を送出します。重い処理の前には`doc.complexity`を確認してください。各上限は[セキュリティ](security.md#untrusted-pdfs)を参照してください。

## ページ・テキスト・検索 { #pages-text-search }

```python
with pylopdf.open("input.pdf") as doc:
    for page in doc:                       # doc[0]、doc[-1] でも可。0始まり
        print(page.number, page.rect)

    page = doc[0]
    text = page.get_text()                 # プレーンテキスト
    words = page.get_text("words")         # (x0, y0, x1, y1, 語, ブロック, 行, 語番号)
    layout = page.get_text("dict")         # blocks → lines → spans（bbox・size・font付き）
    hits = page.search_for("total")        # 大文字小文字を区別しない。list[Rect]
```

座標はすべて左上原点の**表示空間**です。検索結果、レイアウト、描き込み、レンダリングは、回転したページも含めて同じ座標系を共有します。

## レンダリング { #rendering }

```python
with pylopdf.open("input.pdf") as doc:
    doc[0].get_pixmap(dpi=144).save("page-1.png")
    png = doc[0].get_pixmap(dpi=300).tobytes()    # PNGのbytes
    pix = doc[0].get_pixmap(dpi=144)               # NumPyやPillow向けのRGB画素
    pngs = doc.render_pages(dpi=144, workers=4)    # 全ページを並列に
    svg = doc[0].get_svg_image()
```

## 編集 { #editing }

```python
with pylopdf.open("input.pdf") as doc:
    doc.select([2, 0, 1])                  # 残して並べ替え。同じ番号を繰り返すと複製
    doc.delete_pages([1])                  # 編集のたびにページ番号は振り直される
    doc.new_page()                         # 末尾に空白のA4ページ
    doc.fullcopy_page(0, to=1)             # ページ0の独立したコピーを位置1へ
    doc[0].set_rotation(90)
    doc.set_outline([(1, "Chapter 1", 0), (2, "Section 1.1", 1)])
    doc.save("edited.pdf")

with pylopdf.Document() as merged, pylopdf.open("a.pdf") as a, pylopdf.open("b.pdf") as b:
    merged.insert_pdf(a)                   # a.pdfの全ページ
    merged.insert_pdf(b, from_page=0, to_page=2)  # b.pdfの1–3ページ目
    merged.save("merged.pdf")
```

## 描き込みと注釈 { #drawing-annotations }

画像、重ね合わせ、注釈:

```python
with pylopdf.open("input.pdf") as doc, pylopdf.open("letterhead.pdf") as letterhead:
    page = doc[0]
    page.insert_image((72, 72, 200, 200), filename="logo.png")  # JPEGはそのまま埋め込む
    page.show_pdf_page(page.rect, letterhead)                    # 別のPDFのページ0をベクタのまま重ねる
    page.draw_line((36, 340), (400, 340), width=0.5, dashes="[3 2] 0")

    hits = page.search_for("important")
    if hits:
        page.add_highlight_annot(hits)
    for rect in page.search_for("Example")[:1]:
        page.insert_link({"kind": pylopdf.LINK_URI, "from": rect, "uri": "https://example.com/"})
    doc.save("annotated.pdf")
```

テキストとフォント。フォントを指定しない場合、pylopdfはラテン文字にHelveticaを使い、それ以外の文字体系には`pylopdf[jp]`などの導入済み言語extraを使います。

```python
with pylopdf.open("input.pdf") as doc:
    page = doc[0]
    page.insert_text((40, 40), "CONFIDENTIAL", fontsize=18, color=(1, 0, 0))
    page.insert_text((40, 70), "社外秘", fontsize=18)                        # pylopdf[jp]が必要
    page.insert_text((40, 100), "简体中文", fontsize=18, font_language="zh-CN")  # pylopdf[zh-cn]が必要

    bold = pylopdf.Font(fontfile="NotoSansJP-Bold.otf")    # 任意のOpenTypeファイル
    width = bold.text_length("見出し", fontsize=16)          # 描画したときの正確な幅
    page.insert_text((page.rect.width - 40 - width, 140), "見出し", font=bold, fontsize=16)

    semibold = pylopdf.Font(fontfile="NotoSansCJKjp-VF.otf", weight=600)  # 可変フォントは軸上の任意の太さを選べる
    page.insert_text((40, 170), "見出し", font=semibold, fontsize=16)
    doc.save("text.pdf")
```

複数の図形とテキストを1つの描画にまとめ、各フォントを1度だけ埋め込みます。

```python
with pylopdf.open("input.pdf") as doc:
    with doc[0].new_shape() as shape:
        shape.draw_rect((36, 220, 400, 320), radius=0.1)
        shape.finish(color=(0.2, 0.4, 0.6), fill=(0.95, 0.97, 1), width=1)
        shape.insert_text((48, 250), "Report", fontsize=16)
        shape.insert_textbox((48, 260, 390, 310), "Body paragraph.", fontsize=11)
    doc.save("shapes.pdf")
```

## スキャンPDF・フォーム・Markdown { #scans-forms-markdown }

```python
with pylopdf.open("scan.pdf") as doc:
    ocr_words = [(72, 90, 140, 104, "Invoice"), (146, 90, 190, 104, "#1024")]
    doc[0].insert_ocr_text_layer(ocr_words)   # 任意のOCR出力: (x0, y0, x1, y1, text, ...)

with pylopdf.open("form.pdf") as doc:
    doc.set_form_field("customer", "Alice")   # 値とその外観を書き込む
    doc.save("filled.pdf")

with pylopdf.open("input.pdf") as doc:
    md = doc.to_markdown()                    # RAG向けに見出し・リスト・表を出力
```

自前のOCR結果を渡す代わりにpylopdf内で文字を認識するには、[オフラインOCR](ocr.md)を参照してください。

続きは、組版・PDF/A・電子署名の[エコシステム連携](ecosystem.md)へ。pymupdfから移行する場合は[移行ガイド](migration.md)を参照してください。
