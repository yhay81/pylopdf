---
title: 并发与free-threaded Python
description: Document、Page、Pixmap、并行渲染与CPython 3.14t和3.15t所支持的线程边界。
---

# 并发与free-threaded Python

pylopdf既为带GIL的CPython 3.11–3.15发布每个平台一个`abi3` wheel，也为
free-threaded CPython按Python版本发布专用wheel：`cp314-cp314t`自v0.10起提供，
`cp315-cp315t`自v0.15起提供。导入free-threaded wheel不会重新启用GIL。

## 支持约定 { #support-contract }

| 用法 | 支持情况 |
|---|---|
| 带GIL的CPython 3.11–3.15 | 由`abi3-py311` wheel支持 |
| free-threaded CPython 3.14t和3.15t | 由`cp314-cp314t`和`cp315-cp315t` wheel支持，并在GIL禁用状态下测试 |
| 对不同`Document`对象执行并发操作 | 支持；加载、保存、渲染、提取、合并和压缩等重型操作会释放GIL |
| 对同一个`Document`并发调用或编辑 | 不支持；请用锁串行访问，或使用互相独立的Document |
| 对一个Document调用`Document.render_pages(workers=...)` | 支持；这是同一Document内有界并行渲染的边界 |
| 并发读取`Pixmap` | 支持；`Pixmap`不可变 |

`Page`是其父`Document`的视图，因此遵守同一Document规则。外部同时访问可能被PyO3的
运行时借用检查拒绝，但不能把这种检查当作同步机制。

## 选择正确的边界 { #choose-the-right-boundary }

处理互相独立的文件时，让每个worker拥有独立的Document：

```python
from concurrent.futures import ThreadPoolExecutor

import pylopdf


def extract(path: str) -> str:
    with pylopdf.open(path) as document:
        return document.to_markdown()


with ThreadPoolExecutor() as pool:
    results = list(pool.map(extract, paths))
```

处理一个Document中的多个页面时，请使用`render_pages()`，不要从外部线程并发调用
同一个Document：

```python
png_pages = document.render_pages(dpi=144, workers=4)
```

`render_pages()`使用同一个不可变渲染器快照，保留请求的页面顺序和重复项，并将估算的
并发工作内存限制在约512 MB。

## 重复渲染 { #repeated-rendering }

在打开Document的线程上重复调用`Page.get_pixmap()`、`Document.get_page_pixmap()`
和串行`render_pages()`时，会复用先前调用已解析的font、颜色空间和
glyph轮廓，输出与新打开的Document完全相同。每个线程最多保留一个这样的cache，调用
之间最多保留64个不同页面的内容。编辑Document、更改后备font或关闭Document都会使
cache失效；若在打开它的线程上执行这些操作，或在该线程上释放Document，cache会立即
释放。从其他线程失效的cache会在其所属线程下次渲染或退出时释放。来自其他线程的调用
和并行`render_pages()`的worker使用调用级cache。

## Pixmap缓冲区 { #pixmap-buffers }

所有wheel都通过只读、一维、零复制的buffer公开不可变的RGB或RGBA像素数据：

```python
view = memoryview(page.get_pixmap())
assert view.readonly and view.format == "B"
```

view会使Pixmap保持存活，因此NumPy可以通过`np.frombuffer(pixmap, dtype=np.uint8)`
无复制地读取像素。需要独立的`bytes`副本时仍可使用`pixmap.samples`。v0.15起
要求Python 3.11及以上，其stable ABI包含`Py_buffer`，因此abi3 wheel也公开同一
buffer；v0.14.0及更早版本只在free-threaded wheel上公开该buffer。
