---
title: 性能基准
description: 可复现的pylopdf文本提取、合并与渲染基准，同时公开优势与劣势。
---

# 性能基准

pylopdf会**同时公开优势与劣势**。以下数据只是一个环境和语料库的快照，并非普遍排名。
请用它判断自己的工作负载应该测量什么。

!!! info "最近一次运行"
    **2026-07-26 08:18 UTC** · Windows 11 · Python 3.14.6 · AMD64<br>
    pylopdf 0.11.0 · pymupdf 1.28.0 · pypdf 6.14.2 · pdfplumber 0.11.10<br>
    预热1次，测量5次；表中为中位数，单位毫秒。

## 概览 { #overview }

| 工作负载 | 最新语料库的结果 |
|---|---|
| 合并10个真实PDF | pylopdf **36.6 ms**，pymupdf 131.8 ms，pypdf 426.3 ms |
| 以2×渲染第一页 | 10个文件均由pylopdf领先 |
| 以2×渲染12页 | `render_pages()`从317.4 ms（1 worker）降至81.5 ms（8 workers），**加速3.89倍** |
| 提取全部文本 | 4个文件由pylopdf领先，6个由pymupdf领先 |
| 提取一致性代理指标 | 因阅读顺序策略不同，相似度从0.121到1.000 |

## 文本提取 { #text-extraction }

提取所有页面，单位毫秒，越小越快。

| 文件 | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | 191.6 | **183.2** | 689.7 | 9997.1 |
| bunka-kokugo-series-019-p4.pdf | 1.9 | **0.5** | 1.0 | 1.8 |
| f1040.pdf | **26.7** | 66.9 | 230.3 | 704.7 |
| mhlw-doc.pdf | 18.4 | **11.5** | 114.3 | 263.3 |
| nics-background-checks-2015-11.pdf | 16.1 | **10.8** | 177.5 | 524.7 |
| patent-us223898.pdf | 33.3 | **6.3** | 79.2 | 493.7 |
| pdf20-simple.pdf | **0.3** | 1.2 | 1.7 | 2.4 |
| senate-expenditures.pdf | **6.6** | 7.2 | 132.2 | 374.1 |
| usrguide.pdf | 163.0 | **54.5** | 673.6 | 2050.7 |
| wdl6812-manuscript.pdf | **0.3** | 0.8 | 1.3 | 2.3 |

## 提取内容 { #extraction-content }

这只是代理指标，不是正确率。文本经空白归一化后与pymupdf比较。表单和OCR层的相似度
较低，可能只是阅读顺序或空白策略不同；即使字符数相同也会发生这种情况。

| 文件 | pylopdf字符数 | pymupdf字符数 | 相似度 |
|---|---:|---:|---:|
| bill-hr815.pdf | 300559 | 300559 | 1.000 |
| bunka-kokugo-series-019-p4.pdf | 0 | 0 | 1.000 |
| f1040.pdf | 10156 | 10156 | 0.683 |
| mhlw-doc.pdf | 1264 | 1251 | 0.961 |
| nics-background-checks-2015-11.pdf | 5650 | 5650 | 0.121 |
| patent-us223898.pdf | 11218 | 11218 | 0.320 |
| pdf20-simple.pdf | 11 | 11 | 1.000 |
| senate-expenditures.pdf | 4516 | 4516 | 0.443 |
| usrguide.pdf | 55624 | 55560 | 0.996 |
| wdl6812-manuscript.pdf | 0 | 0 | 1.000 |

## 合并 { #merge }

| 任务 | pylopdf | pymupdf | pypdf |
|---|---:|---:|---:|
| 合并语料库全部10个文件 | **36.6** | 131.8 | 426.3 |

## 渲染 { #rendering }

第一页输出为2× PNG，单位毫秒，越小越快。

| 文件 | pylopdf | pymupdf |
|---|---:|---:|
| bill-hr815.pdf | **41.1** | 88.9 |
| bunka-kokugo-series-019-p4.pdf | **48.2** | 110.5 |
| f1040.pdf | **49.5** | 97.1 |
| mhlw-doc.pdf | **35.5** | 71.2 |
| nics-background-checks-2015-11.pdf | **54.2** | 72.6 |
| patent-us223898.pdf | **32.3** | 68.8 |
| pdf20-simple.pdf | **8.0** | 19.9 |
| senate-expenditures.pdf | **55.2** | 56.8 |
| usrguide.pdf | **28.3** | 54.9 |
| wdl6812-manuscript.pdf | **42.9** | 83.3 |

## 并行渲染 { #parallel-rendering }

将`usrguide.pdf`前12页输出为2× PNG，单位毫秒，越小越快。
批处理保持输入顺序，并使用同一个不可变文档快照。

| Workers | 时间 | 相对1 worker加速 |
|---:|---:|---:|
| 1 | 317.4 | 1.00倍 |
| 2 | 179.6 | 1.77倍 |
| 4 | 99.1 | 3.20倍 |
| 8 | 81.5 | 3.89倍 |

实际并发度同时受指定worker数和约512 MB的估算实时渲染内存限制。

## free-threaded提取 { #free-threaded-extraction }

在Windows 11的free-threaded CPython 3.14.6上，对两个互相独立的
`bill-hr815.pdf`执行全页文本提取。预热一次后，交替先运行的模式，取七组配对运行的
中位数：

| 模式 | Workers | 时间 | 加速比 |
|---|---:|---:|---:|
| 串行 | 1 | 280.3 ms | 1.00倍 |
| 并行 | 2 | 160.8 ms | 1.74倍 |

每次运行中两个副本的输出都完全一致，并且解释器确认导入后GIL仍保持禁用。

## 复现 { #reproduce }

语料库位于`tests/assets/real_world`，文件来源与许可证记录在同一目录。

```bash
uv sync --all-extras --group bench
uv run python bench/run.py
uv run python tools/pyodide_compat.py --root . --benchmark-only \
  --benchmark-output .tmp/limits-benchmark.json
# 使用free-threaded CPython 3.14解释器：
python3.14t bench/free_threaded.py
```

生成的原始报告提交在
[`bench/results/latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/latest.md)。
native／Pyodide资源策略基准另行提交在
[`bench/results/limits-latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/limits-latest.md)。
第二条command测量有界open/extract和受控拒绝。CI还会在Pyodide中运行相同case并
记录Wasm linear memory增长；这些时间和memory值只表示趋势，不是native/Wasm
性能结论。
引用数据时，请同时提供运行环境与语料库。
