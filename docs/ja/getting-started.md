---
title: はじめる
description: pylopdfをインストールし、編集・描画・抽出・書き込みの基本を学びます。
---

# はじめる

## インストール { #installation }

```bash
pip install pylopdf
```

フォント非埋め込みの日本語PDFを描画する場合、または日本語・漢字の`insert_text` /
`insert_textbox`でJP fontを自動subsetする場合は、Noto Sans/Serif JP付きで:

```bash
pip install pylopdf[cjk]
```

release CIは各native platform wheelをarchitectureが一致するrunnerへinstallし、PDFの
生成・保存・再open・抽出・renderとimmutable Pixmap storageを検証します。5つの
`abi3-py310`と5つのCPython 3.14t artifactをすべて対象にし、sdistとPyEmscripten
wheelにはそれぞれのenvironment専用gateがあります。

## 開く・調べる・保存する { #open-inspect-save }

```python
import pylopdf

doc = pylopdf.open("input.pdf")           # pylopdf.open(stream=pdf_bytes) でも
print(doc.page_count)                     # len(doc) でも同じ
print(doc.metadata["title"])
doc.set_metadata({"title": "月次レポート", "author": "山田 太郎"})

doc.save("out.pdf")
data = doc.tobytes()
doc.save("small.pdf", garbage=True, deflate=True, object_streams=True)
doc.save("locked.pdf", user_pw="secret", permissions=pylopdf.Permissions.PRINT)
```

暗号化 PDF は `password=`（または開いた後の `doc.authenticate()`）で復号します。
`pylopdf.peek_metadata(path)` は通常、全体をパースせずメタデータとページ数だけを
高速に読みます（大量走査向け）。`max_file_size=`を指定するとpathまたはbyte inputを
parse前にサイズで拒否でき、互換性のため既定値は無制限です。最終classic
`startxref`の誤りを限定的に修復した場合は`repaired`と`doc.is_repaired`が`True`に
なり、`PylopdfWarning`も発生します。
保存するとxref dataは正規化されます。信頼できないファイルには
`limits=pylopdf.DocumentLimits.web()`を指定してください。ファイル、構造、展開、
rendering／extraction用PDF snapshot、解釈済みテキストを制限し、重い処理の前に
`doc.complexity`を確認できます。
制御された拒否は`LimitError`です。各上限は[セキュリティ](security.md#untrusted-pdfs)
を参照してください。

## ページ・テキスト・検索 { #pages-text-search }

```python
page = doc[0]                             # 0 始まり。負数は末尾から
for page in doc:
    print(page.number, page.rect)

text = page.get_text()                    # プレーンテキスト
words = page.get_text("words")            # (x0, y0, x1, y1, 語, ブロック, 行, 語番号)
layout = page.get_text("dict")            # blocks → lines → spans（bbox・size・font・flags）
hits = page.search_for("合計")            # 大文字小文字を区別しない。list[Rect]
```

座標はすべて左上原点の**表示空間**です — 検索結果・レイアウト・描き込み・
レンダリングが同じ座標系を共有し、回転ページでも一致します。

## レンダリング { #rendering }

```python
png = doc.render_page(0, dpi=300)                    # PNG（bytes）
pix = page.get_pixmap(scale=2)                       # NumPy / PIL 向け RGBA8 画素
batch = doc.render_pages([0, 1, 2], scale=2, workers=4)
svg = doc.render_page_svg(0)
```

## 編集 { #editing }

```python
doc.delete_pages([1, 2])
doc.select([2, 0])                                   # 抽出・並べ替え（重複指定は複製）
doc.new_page(); doc.copy_page(0, to=1)

merged = pylopdf.Document()
merged.insert_pdf(pylopdf.open("a.pdf"))
merged.insert_pdf(pylopdf.open("b.pdf"), from_page=0, to_page=2, start_at=0)

doc.set_toc([[1, "第 1 章", 1], [2, "1.1 節", 2]])
page.set_rotation(90)
```

## 描き込みと注釈 { #drawing-annotations }

```python
page.insert_image((72, 72, 200, 200), filename="logo.png")   # JPEG パススルー / PNG 透過
page.insert_image(page.search_for("承認印")[0], stream=hanko_png)
page.insert_image((300, 72, 500, 200), pixmap=thumbnail, rotate=90)  # RGBAを直接、時計回りに回転
page.show_pdf_page(page.rect, letterhead)                    # ベクタのまま重ねる。同じ文書も可
page.insert_text((40, 40), "CONFIDENTIAL", fontsize=18, color=(1, 0, 0))
page.insert_text((40, 70), "社外秘", fontsize=18)             # pylopdf[cjk]から自動subset
page.add_highlight_annot(page.search_for("重要"))            # 検索してマーカー
page.add_link_annot(page.search_for("Example")[0], "https://example.com/")
```

## スキャン PDF・フォーム・Markdown { #scans-forms-markdown }

```python
page.insert_ocr_text_layer(ocr_words)        # 外部 OCR の結果で searchable PDF 化
doc.set_form_field("customer", "山田 太郎")  # pylopdf[cjk]から自動subset
md = doc.to_markdown()                       # RAG 向け Markdown
```

続きは、組版・PDF/A・電子署名の[エコシステム連携](ecosystem.md)、
pymupdf 利用者は[移行ガイド](migration.md)へ。
