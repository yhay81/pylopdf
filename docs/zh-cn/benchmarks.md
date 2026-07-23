---
title: 性能基准
description: 可复现的pylopdf文本提取、合并与渲染基准，同时公开优势与劣势。
---

# 性能基准

pylopdf会**同时公开优势与劣势**。以下数据只是一个环境和语料库的快照，并非普遍排名。
请用它判断自己的工作负载应该测量什么。

!!! info "最近一次运行"
    **2026-07-23 04:47 UTC** · Windows 11 · Python 3.14.6 · AMD64<br>
    pylopdf 0.9.0 · pymupdf 1.28.0 · pypdf 6.14.2 · pdfplumber 0.11.10<br>
    预热1次，测量5次；表中为中位数，单位毫秒。

## 概览 { #overview }

| 工作负载 | 最新语料库的结果 |
|---|---|
| 合并7个真实PDF | pylopdf **30.1 ms**，pymupdf 122.2 ms，pypdf 325.3 ms |
| 以2×渲染第一页 | 7个文件均由pylopdf领先 |
| 提取全部文本 | 4个文件由pylopdf领先，3个由pymupdf领先 |
| 提取一致性代理指标 | 因阅读顺序策略不同，相似度从0.292到1.000 |

## 文本提取 { #text-extraction }

提取所有页面，单位毫秒，越小越快。

| 文件 | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **131.6** | 150.7 | 631.4 | 8652.7 |
| f1040.pdf | **16.0** | 32.9 | 155.6 | 506.2 |
| mhlw-doc.pdf | 11.8 | **10.3** | 84.2 | 175.7 |
| patent-us223898.pdf | 26.3 | **6.0** | 83.4 | 390.2 |
| pdf20-simple.pdf | **0.3** | 0.8 | 1.2 | 1.9 |
| usrguide.pdf | 108.2 | **42.7** | 579.3 | 1673.5 |
| wdl6812-manuscript.pdf | **0.4** | 1.0 | 1.4 | 2.6 |

## 提取内容 { #extraction-content }

这只是代理指标，不是正确率。文本经空白归一化后与pymupdf比较。表单和OCR层的相似度
较低，可能只是阅读顺序或空白策略不同；即使字符数相同也会发生这种情况。

| 文件 | pylopdf字符数 | pymupdf字符数 | 相似度 |
|---|---:|---:|---:|
| bill-hr815.pdf | 300559 | 300559 | 1.000 |
| f1040.pdf | 10158 | 10156 | 0.680 |
| mhlw-doc.pdf | 1264 | 1251 | 0.961 |
| patent-us223898.pdf | 11207 | 11218 | 0.292 |
| pdf20-simple.pdf | 11 | 11 | 1.000 |
| usrguide.pdf | 55624 | 55560 | 0.996 |
| wdl6812-manuscript.pdf | 0 | 0 | 1.000 |

## 合并 { #merge }

| 任务 | pylopdf | pymupdf | pypdf |
|---|---:|---:|---:|
| 合并语料库全部7个文件 | **30.1** | 122.2 | 325.3 |

## 渲染 { #rendering }

第一页输出为2× PNG，单位毫秒，越小越快。

| 文件 | pylopdf | pymupdf |
|---|---:|---:|
| bill-hr815.pdf | **40.8** | 84.0 |
| f1040.pdf | **49.9** | 92.1 |
| mhlw-doc.pdf | **33.8** | 68.7 |
| patent-us223898.pdf | **34.7** | 64.1 |
| pdf20-simple.pdf | **9.0** | 18.9 |
| usrguide.pdf | **30.7** | 54.6 |
| wdl6812-manuscript.pdf | **43.4** | 83.8 |

## 复现 { #reproduce }

语料库位于`tests/assets/real_world`，文件来源与许可证记录在同一目录。

```bash
uv sync --all-extras --group bench
uv run python bench/run.py
```

生成的原始报告提交在
[`bench/results/latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/latest.md)。
引用数据时，请同时提供运行环境与语料库。
