---
title: エコシステム連携
description: pylopdfをTypst、pyHanko、veraPDF、OCRエンジンとつなぐ、テスト済みのレシピ。
---

# エコシステム連携

pylopdfは組版、PDF/Aの出力、電子署名を実装しません。これらはすでに実績あるライブラリがうまくこなしており、以下のレシピではそれらをpylopdfと組み合わせる方法を紹介します。各レシピは[`tests/test_interop.py`](https://github.com/yhay81/pylopdf/blob/main/tests/test_interop.py)の統合テストでカバーしています。

## typstで新しい文書を組版する { #typesetting }

文書を[typst](https://typst.app/)で書き、[typst-py](https://pypi.org/project/typst/)でコンパイルして、その結果をpylopdfで開きます。

```python
import typst
import pylopdf

pdf_bytes = typst.compile("report.typ")   # レイアウトはtypst
doc = pylopdf.open(pdf_bytes)      # 編集・結合・抽出はpylopdf
```

## typstでPDF/Aを出力する { #new-pdfa }

typstはPDF/A（パート1〜4）とPDF/UA-1のファイルを書き出せます。

```python
pdf_a: bytes = typst.compile("report.typ", pdf_standards="a-2b")
pylopdf.open(pdf_a).get_pdfa_claim()   # (2, "B")
```

`Document.get_pdfa_claim()`は、ファイルがどのPDF/Aレベルを宣言しているかを読むだけです。既存のPDFが本当に適合しているかを確認するには、標準的な検証ツールである[veraPDF](https://verapdf.org/)（Javaで動作）を使ってください。

## typstでデザインした透かしやヘッダー { #cjk-watermarks }

単純なテキストなら`insert_text()`で十分です。`pylopdf[jp]`（または`[ko]`、`[zh-cn]`、`[zh-tw]`）をインストールしていれば、CJKのテキストも書けます。スタンプに本格的なレイアウトが必要なときは、typstでページとしてデザインし、そのページを`show_pdf_page()`で文書の全ページに描きます。

```python
from pylopdf_fonts_jp import sans_path  # pip install pylopdf[jp]

stamp_typ = """
#set page(width: 595pt, height: 842pt, fill: none)
#set text(font: "Noto Sans JP", size: 48pt, fill: rgb(255, 0, 0, 40%))
#align(center + horizon)[社外秘]
"""
stamp = pylopdf.open(typst.compile(stamp_typ.encode(), font_paths=[str(sans_path().parent)]))
for page in doc.pages:
    page.show_pdf_page((0, 0, page.rect.width, page.rect.height), stamp)
```

スタンプは本物のテキストのままなので、後から`page.text()`で`社外秘`が見つかります。

## pyHankoで電子署名する { #signatures }

[pyHanko](https://pypi.org/project/pyHanko/)（MIT）はPAdES署名を作成します。署名をファイルの末尾に追記するので、pylopdfが書き出したバイト列は変わりません。

```python
import io
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import signers

signer = signers.SimpleSigner.load("key.pem", "cert.pem")
out = signers.sign_pdf(
    IncrementalPdfFileWriter(io.BytesIO(doc.to_bytes())),
    signers.PdfSignatureMetadata(field_name="Signature1"),
    signer=signer,
)
signed_pdf: bytes = out.getvalue()
```

署名は最後の手順にしてください。その後にpylopdfで保存するとファイルが書き直され、署名が無効になります。`set_form_field()`は署名フィールドへの記入を拒否します。

## ほかのエンジンでOCRする { #ocr }

pylopdfには[組み込みのOCR](ocr.md)があります。代わりにTesseractやクラウドサービスなど別のエンジンを使う場合は、その単語を`(x0, y0, x1, y1, text)`として`Page.insert_ocr_text_layer(words)`に渡してください。pylopdfはそれを不可視のテキスト層として追加します。これでスキャンが検索可能になり、`markdown()`でも読み取れるようになり、ファイルサイズもほとんど増えません。

```python
import pylopdf

words = [  # from your OCR engine, in page coordinates
    (72, 90, 140, 104, "Invoice"),
    (146, 90, 190, 104, "#1024"),
]
with pylopdf.open("scan.pdf") as doc:
    doc.pages[0].insert_ocr_text_layer(words)
    doc.save("searchable.pdf")
```
