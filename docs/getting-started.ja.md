# はじめる

## インストール

```bash
pip install pylopdf
```

フォント非埋め込みの日本語 PDF をレンダリングする場合は、CJK フォント付きで
（Noto Sans/Serif JP を同梱、レンダリング時に自動検出）:

```bash
pip install pylopdf[cjk]
```

## 開く・調べる・保存する

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
`pylopdf.peek_metadata(path)` は全体をパースせずメタデータとページ数だけを高速に
読みます（大量走査向け）。信頼できないファイルには `max_decompressed_size=` を
（解凍爆弾対策）。

## ページ・テキスト・検索

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

## レンダリング

```python
png = doc.render_page(0, dpi=300)                    # PNG（bytes）
pix = page.get_pixmap(scale=2)                       # NumPy / PIL 向け RGBA8 画素
svg = doc.render_page_svg(0)
```

## 編集

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

## 描き込みと注釈

```python
page.insert_image((72, 72, 200, 200), filename="logo.png")   # JPEG パススルー / PNG 透過
page.insert_image(page.search_for("承認印")[0], stream=hanko_png)
page.show_pdf_page(page.rect, letterhead)                    # 別 PDF をベクタのまま重ねる
page.insert_text((40, 40), "CONFIDENTIAL", fontsize=18, color=(1, 0, 0))
page.add_highlight_annot(page.search_for("重要"))            # 検索してマーカー
page.add_link_annot(page.search_for("Example")[0], "https://example.com/")
```

## スキャン PDF・フォーム・Markdown

```python
page.insert_ocr_text_layer(ocr_words)     # 外部 OCR の結果で searchable PDF 化
doc.set_form_field("customer", "山田 太郎")  # AcroForm 記入（NeedAppearances）
md = doc.to_markdown()                    # RAG 向け Markdown
```

続きは、組版・PDF/A・電子署名の[エコシステム連携](ecosystem.md)、
pymupdf 利用者は[移行ガイド](migration.md)へ。
