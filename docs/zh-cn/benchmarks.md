---
title: 性能基准
description: 可复现的pylopdf文本提取、合并与渲染基准，同时公开优势与劣势。
---

# 性能基准

pylopdf会**同时公开优势与劣势**。以下数据只是一个环境和语料库的快照，并非普遍排名。
请用它判断自己的工作负载应该测量什么。

!!! info "最近一次运行"
    **2026-07-24 12:59 UTC** · Windows 11 · Python 3.14.6 · AMD64<br>
    pylopdf 0.9.0 · pymupdf 1.28.0 · pypdf 6.14.2 · pdfplumber 0.11.10<br>
    预热1次，测量5次；表中为中位数，单位毫秒。

## 概览 { #overview }

| 工作负载 | 最新语料库的结果 |
|---|---|
| 合并7个真实PDF | pylopdf **40.6 ms**，pymupdf 127.1 ms，pypdf 366.3 ms |
| 以2×渲染第一页 | 7个文件均由pylopdf领先 |
| 以2×渲染12页 | `render_pages()`从400.8 ms（1 worker）降至83.6 ms（8 workers），**加速4.80倍** |
| 提取全部文本 | 4个文件由pylopdf领先，3个由pymupdf领先 |
| 提取一致性代理指标 | 因阅读顺序策略不同，相似度从0.292到1.000 |

## 文本提取 { #text-extraction }

提取所有页面，单位毫秒，越小越快。

| 文件 | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **138.1** | 179.5 | 848.4 | 9850.9 |
| f1040.pdf | **16.8** | 33.4 | 176.3 | 572.9 |
| mhlw-doc.pdf | 18.4 | **11.3** | 109.3 | 195.3 |
| patent-us223898.pdf | 29.5 | **6.8** | 81.4 | 512.9 |
| pdf20-simple.pdf | **0.3** | 1.1 | 1.8 | 2.2 |
| usrguide.pdf | 144.9 | **50.7** | 665.2 | 1756.4 |
| wdl6812-manuscript.pdf | **0.3** | 0.7 | 1.4 | 2.2 |

## 提取内容 { #extraction-content }

这只是代理指标，不是正确率。文本经空白归一化后与pymupdf比较。表单和OCR层的相似度
较低，可能只是阅读顺序或空白策略不同；即使字符数相同也会发生这种情况。

| 文件 | pylopdf字符数 | pymupdf字符数 | 相似度 |
|---|---:|---:|---:|
| bill-hr815.pdf | 300559 | 300559 | 1.000 |
| f1040.pdf | 10156 | 10156 | 0.680 |
| mhlw-doc.pdf | 1264 | 1251 | 0.961 |
| patent-us223898.pdf | 11207 | 11218 | 0.292 |
| pdf20-simple.pdf | 11 | 11 | 1.000 |
| usrguide.pdf | 55624 | 55560 | 0.996 |
| wdl6812-manuscript.pdf | 0 | 0 | 1.000 |

## 合并 { #merge }

| 任务 | pylopdf | pymupdf | pypdf |
|---|---:|---:|---:|
| 合并语料库全部7个文件 | **40.6** | 127.1 | 366.3 |

## 渲染 { #rendering }

第一页输出为2× PNG，单位毫秒，越小越快。

| 文件 | pylopdf | pymupdf |
|---|---:|---:|
| bill-hr815.pdf | **38.2** | 86.2 |
| f1040.pdf | **53.1** | 94.7 |
| mhlw-doc.pdf | **35.7** | 70.1 |
| patent-us223898.pdf | **36.4** | 69.0 |
| pdf20-simple.pdf | **9.0** | 18.9 |
| usrguide.pdf | **31.8** | 56.6 |
| wdl6812-manuscript.pdf | **45.5** | 87.0 |

## 并行渲染 { #parallel-rendering }

将`usrguide.pdf`前12页输出为2× PNG，单位毫秒，越小越快。
批处理保持输入顺序，并使用同一个不可变文档快照。

| Workers | 时间 | 相对1 worker加速 |
|---:|---:|---:|
| 1 | 400.8 | 1.00倍 |
| 2 | 200.5 | 2.00倍 |
| 4 | 118.5 | 3.38倍 |
| 8 | 83.6 | 4.80倍 |

实际并发度同时受指定worker数和约512 MB的估算实时渲染内存限制。

## free-threaded提取 { #free-threaded-extraction }

在Windows 11的free-threaded CPython 3.14.6上，对两个互相独立的
`bill-hr815.pdf`执行全页文本提取。预热一次后，交替先运行的模式，取七组配对运行的
中位数：

| 模式 | Workers | 时间 | 加速比 |
|---|---:|---:|---:|
| 串行 | 1 | 341.8 ms | 1.00倍 |
| 并行 | 2 | 195.9 ms | 1.74倍 |

每次运行中两个副本的输出都完全一致，并且解释器确认导入后GIL仍保持禁用。

## 复现 { #reproduce }

语料库位于`tests/assets/real_world`，文件来源与许可证记录在同一目录。

```bash
uv sync --all-extras --group bench
uv run python bench/run.py
# 使用free-threaded CPython 3.14解释器：
python3.14t bench/free_threaded.py
```

生成的原始报告提交在
[`bench/results/latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/latest.md)。
引用数据时，请同时提供运行环境与语料库。
