---
title: 并发与free-threaded Python
description: Document、Page、Pixmap、并行渲染与CPython 3.14t所支持的线程边界。
---

# 并发与free-threaded Python

从v0.10开始，pylopdf既为带GIL的CPython 3.10–3.14发布每个平台一个`abi3` wheel，
也为free-threaded CPython 3.14发布专用的`cp314-cp314t` wheel。导入后者不会重新启用GIL。

## 支持约定 { #support-contract }

| 用法 | 支持情况 |
|---|---|
| 带GIL的CPython 3.10–3.14 | 由`abi3-py310` wheel支持 |
| free-threaded CPython 3.14t | 由`cp314-cp314t` wheel支持，并在GIL禁用状态下测试 |
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
png_pages = document.render_pages(scale=2, workers=4)
```

`render_pages()`使用同一个不可变渲染器快照，保留请求的页面顺序和重复项，并将估算的
并发工作内存限制在约512 MB。

## Pixmap缓冲区 { #pixmap-buffers }

free-threaded wheel通过只读、一维、零复制的buffer公开不可变RGBA8数据：

```python
view = memoryview(page.get_pixmap())
assert view.readonly and view.format == "B"
```

`Py_buffer`在Python 3.11才进入stable ABI，因此`abi3-py310` wheel无法公开该接口。
在该wheel上请使用`pixmap.samples`；它是复制一次的`bytes`值。这样既保持Python 3.10
兼容性，也不会削弱buffer的生命周期和不可变性保证。
