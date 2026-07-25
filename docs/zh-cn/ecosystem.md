---
title: 生态系统方案
description: 经过集成测试的pylopdf与Typst、pyHanko、veraPDF及OCR引擎协作方案。
---

# 生态系统方案

pylopdf有意**不重复实现**排版、PDF/A生成或数字签名。每个领域都与成熟的库组合解决，
以下方案均由
[`tests/test_interop.py`](https://github.com/yhay81/pylopdf/blob/main/tests/test_interop.py)
中的集成测试保护。

## 排版与新文档 — typst { #typesetting }

使用[typst](https://typst.app/)（通过
[typst-py](https://pypi.org/project/typst/)）排版报告，然后将字节直接交给pylopdf：

```python
import typst
import pylopdf

pdf_bytes = typst.compile("report.typ")   # 排版：typst
doc = pylopdf.open(stream=pdf_bytes)      # 编辑、提取、合并：pylopdf
```

## 新文档的PDF/A — typst { #new-pdfa }

typst的PDF后端krilla支持经过验证的PDF/A-1b…4与PDF/UA-1导出：

```python
pdf_a: bytes = typst.compile("report.typ", pdf_standards="a-2b")
pylopdf.open(stream=pdf_a).get_pdfa_claim()   # (2, "B")
```

转换或验证*已有*PDF是另一个问题。[veraPDF](https://verapdf.org/)（Java）是事实上的
验证标准。`Document.get_pdfa_claim()`只读取文档的自我声明。

## 排版复杂CJK水印与页眉 — typst × show_pdf_page { #cjk-watermarks }

pylopdf可用`insert_text(fontfile=...)`直接绘制本地化中文；`pylopdf[cjk]`的自动
选择使用JP字形，适合日文／汉字但不替代SC font。如水印需要更复杂的整页排版，可用
typst排版（字体会以子集嵌入），再以矢量形式
写入每一页：

```python
from pylopdf_fonts_cjk import sans_path  # pip install pylopdf[cjk]

stamp_typ = """
#set page(width: 595pt, height: 842pt, fill: none)
#set text(font: "Noto Sans JP", size: 48pt, fill: rgb(255, 0, 0, 40%))
#align(center + horizon)[机密]
"""
stamp = pylopdf.open(stream=typst.compile(stamp_typ.encode(), font_paths=[str(sans_path().parent)]))
for page in doc:
    page.show_pdf_page((0, 0, page.rect.width, page.rect.height), stamp)
```

写入后水印仍是可提取文本，`page.get_text()`可以找到“机密”。

## 数字签名（PAdES）— pyHanko { #signatures }

[pyHanko](https://pypi.org/project/pyHanko/)（MIT）通过增量更新签名，因此pylopdf生成的
字节会原样保留为签名文件的前缀。集成测试会逐字节验证这一点：

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

pylopdf自己的表单API会有意拒绝填写签名字段，并引导到本方案。

## OCR — 使用任意引擎 { #ocr }

`Page.insert_ocr_text_layer(words)`可接收任何OCR结果：云API、Tesseract，或其他能够返回
`(x0, y0, x1, y1, text)`的引擎。它会将结果写成不可见文本层，使扫描PDF可搜索，
且因不嵌入字体，文件大小几乎不增加。之后`to_markdown()`也可用于扫描PDF。
