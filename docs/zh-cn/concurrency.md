---
title: 并发与自由线程Python
description: 如何在多个线程中使用pylopdf、并行渲染页面，以及在自由线程CPython 3.14t和3.15t上运行。
---

# 并发与自由线程Python

pylopdf在打开、保存、渲染、提取、合并和压缩等重型操作期间会释放GIL，因此在常规CPython上，多个线程可以并行运行。自由线程的CPython 3.14t和3.15t有各自专用的wheel，导入后GIL保持禁用。

## 支持范围 { #support-contract }

| 用法 | 是否支持 |
|---|---|
| 不同线程处理不同的`Document`对象 | 支持 |
| 多个线程同时调用或编辑同一个`Document` | 不支持：请使用锁，或在每个线程中各自打开文件 |
| 对一个文档使用`Document.render(workers=...)` | 支持；这是并行渲染单个文档的受支持方式 |
| 多个线程读取同一个`Pixmap` | 支持；`Pixmap`不可修改 |
| 自由线程CPython 3.14t和3.15t | 支持，并在禁用GIL的状态下测试 |

`Page`属于其`Document`，遵循同样的规则。pylopdf可能会拒绝对同一文档的同时调用，但不要把这当作锁来依赖。

## 处理大量文件 { #many-files }

让每个worker拥有各自的文档：

```python
from concurrent.futures import ThreadPoolExecutor

import pylopdf


def extract(path: str) -> str:
    with pylopdf.open(path) as document:
        return document.markdown()


paths = ["a.pdf", "b.pdf", "c.pdf"]
with ThreadPoolExecutor() as pool:
    results = list(pool.map(extract, paths))
```

## 渲染一个文档的多个页面 { #render-pages }

请使用`doc.render()`，而不要从多个线程调用同一个文档：

```python
with pylopdf.open("report.pdf") as document:
    png_pages = document.render(dpi=144, workers=4)
```

它按请求页面的顺序返回PNG bytes；页面较大时会减少worker数量，使估算的工作内存保持在约512 MB以内。

## 重复渲染 { #repeated-rendering }

在打开文档的线程上反复渲染该文档的页面时，会复用之前调用中解析过的字体和字形轮廓。输出与新打开的文档完全相同。该缓存最多覆盖64个页面，编辑或关闭文档时会被释放。

## Pixmap与NumPy { #pixmap-buffers }

`Pixmap`以只读缓冲区的形式公开其像素，因此NumPy无需复制即可使用：

```python
import numpy as np

pix = page.pixmap()
pixels = np.frombuffer(pix, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
```

而`pix.samples`会返回一份独立的`bytes`副本。
