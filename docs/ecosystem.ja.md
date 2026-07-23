# エコシステム連携

pylopdf は組版・PDF/A 生成・電子署名を**あえて再実装しません**。それぞれ実績ある
ライブラリとの連携で解決し、以下のレシピはすべて
[`tests/test_interop.py`](https://github.com/yhay81/pylopdf/blob/main/tests/test_interop.py)
の統合テストで保証しています。

## 組版・新規文書の生成 = typst

レポートや帳票は [typst](https://typst.app/)（[typst-py](https://pypi.org/project/typst/)
経由）で組版し、bytes をそのまま pylopdf へ:

```python
import typst
import pylopdf

pdf_bytes = typst.compile("report.typ")   # 組版は typst
doc = pylopdf.open(stream=pdf_bytes)      # 編集・抽出・結合は pylopdf
```

## 新規文書の PDF/A = typst

typst の PDF バックエンド（krilla）は検証付きの PDF/A-1b〜4・PDF/UA-1 出力に
対応しています:

```python
pdf_a: bytes = typst.compile("report.typ", pdf_standards="a-2b")
pylopdf.open(stream=pdf_a).get_pdfa_claim()   # (2, "B")
```

既存 PDF の PDF/A 変換・検証は別問題で、検証は [veraPDF](https://verapdf.org/)
（Java）が事実上の標準です。`Document.get_pdfa_claim()` は自己宣言の読み取りだけを
行います。

## 日本語の透かし・ヘッダ / フッタ = typst × show_pdf_page

標準 14 フォントでは日本語を描けません。代わりに typst で 1 ページの透かしを
組み（フォントはサブセット埋め込みされる）、全ページへベクタのまま焼き込みます:

```python
from pylopdf_fonts_cjk import sans_path  # pip install pylopdf[cjk]

stamp_typ = """
#set page(width: 595pt, height: 842pt, fill: none)
#set text(font: "Noto Sans JP", size: 48pt, fill: rgb(255, 0, 0, 40%))
#align(center + horizon)[社外秘]
"""
stamp = pylopdf.open(stream=typst.compile(stamp_typ.encode(), font_paths=[str(sans_path().parent)]))
for page in doc:
    page.show_pdf_page((0, 0, page.rect.width, page.rect.height), stamp)
```

焼き込み後もテキストはベクタのまま — `page.get_text()` で「社外秘」が抽出できます。

## 電子署名（PAdES）= pyHanko

[pyHanko](https://pypi.org/project/pyHanko/)（MIT）は増分更新で署名するため、
pylopdf の出力バイト列は署名後も先頭に無加工で残ります（統合テストでバイト単位に
検証済み）:

```python
import io
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import signers

signer = signers.SimpleSigner.load("key.pem", "cert.pem")
out = signers.sign_pdf(
    IncrementalPdfFileWriter(io.BytesIO(doc.tobytes())),
    signers.PdfSignatureMetadata(field_name="Signature1"),
    signer=signer,
)
signed_pdf: bytes = out.getvalue()
```

pylopdf 自身のフォーム API は署名フィールドへの記入を意図的に拒否し、ここへ
誘導します。

## OCR = 好きなエンジンを持ち込む

`Page.insert_ocr_text_layer(words)` は、クラウド API でも Tesseract でも
`(x0, y0, x1, y1, テキスト)` を返すあらゆる OCR の結果を不可視テキスト層として
書き込み、スキャン PDF を searchable にします（フォント非埋め込みでサイズ増は
ほぼゼロ）。その後は `to_markdown()` もスキャン PDF に対して機能します。
