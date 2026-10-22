---
title: 生态系统方案
description: 经过测试的pylopdf与Typst、pyHanko、veraPDF及OCR引擎的协作方案。
---

# 生态系统方案

pylopdf不实现排版、PDF/A输出或数字签名。这些工作已有成熟的库做得很好，下面的方案演示如何将它们与pylopdf结合使用。每个方案都由[`tests/test_interop.py`](https://github.com/yhay81/pylopdf/blob/main/tests/test_interop.py)中的集成测试覆盖。

## 用typst排版新文档 { #typesetting }

用[typst](https://typst.app/)编写文档，用[typst-py](https://pypi.org/project/typst/)编译，然后在pylopdf中打开结果：

```python
import typst
import pylopdf

pdf_bytes = typst.compile("report.typ")   # typst负责排版
doc = pylopdf.open(pdf_bytes)      # pylopdf负责编辑、合并和提取
```

## 用typst输出PDF/A { #new-pdfa }

typst可以输出PDF/A（第1至4部分）和PDF/UA-1文件：

```python
pdf_a: bytes = typst.compile("report.typ", pdf_standards="a-2b")
pylopdf.open(pdf_a).get_pdfa_claim()   # (2, "B")
```

`Document.get_pdfa_claim()`只读取文件声明的PDF/A级别。要检查现有PDF是否真正符合标准，请使用标准验证工具[veraPDF](https://verapdf.org/)（运行于Java）。

## 用typst设计水印和页眉 { #cjk-watermarks }

对于简单的文字，`insert_text()`就足够了；安装`pylopdf[jp]`（或`[ko]`、`[zh-cn]`、`[zh-tw]`）后，它也能写入CJK文字。当印章需要真正的版面设计时，可以在typst中将其设计为一个页面，再用`show_pdf_page()`把该页面绘制到文档的每一页上：

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

印章仍然是真实的文本：之后`page.get_text()`能够找到`社外秘`。

## 用pyHanko进行数字签名 { #signatures }

[pyHanko](https://pypi.org/project/pyHanko/)（MIT）可创建PAdES签名。它把签名追加到文件末尾，因此pylopdf写出的字节保持不变：

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

请把签名作为最后一步：之后再用pylopdf保存会重写文件，使签名失效。`set_form_field()`拒绝填写签名字段。

## 使用其他OCR引擎 { #ocr }

pylopdf有[内置OCR](ocr.md)。如果想改用Tesseract或云服务等其他引擎，请将其识别出的单词以`(x0, y0, x1, y1, text)`的形式传给`Page.insert_ocr_text_layer(words)`。pylopdf会把它们添加为不可见的文本层，使扫描件可被搜索，并让`to_markdown()`能够读取，同时几乎不增加文件大小。

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
