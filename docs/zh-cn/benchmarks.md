---
title: 性能基准
description: 可复现的pylopdf文本提取、合并、光栅化与PNG编码基准，同时公开优势与劣势。
---

# 性能基准

pylopdf会**同时公开优势与劣势**。以下数据只是一个环境和语料库的快照，并非普遍排名。
请用它判断自己的工作负载应该测量什么。

!!! info "最近一次运行"
    **2026-10-17 15:14 UTC** · macOS 27.0.1 · Python 3.14.8 · Apple M2 Pro（arm64）<br>
    使用thin LTO release profile构建的pylopdf 0.14.0 · pymupdf 1.28.0 · pypdf 6.15.0 · pdfplumber 0.11.10<br>
    预热1次，测量7次；表中为中位数，单位毫秒。

## 概览 { #overview }

| 工作负载 | 最新语料库的结果 |
|---|---|
| 合并14个真实PDF | pylopdf **32.8 ms**，pymupdf 116.2 ms，pypdf 145.4 ms |
| 第一页输出为2× PNG（完整流程） | 14个文件均由pylopdf领先，合计**216.1 ms**对591.3 ms |
| 仅光栅化 | 14个文件中，cold时有9个、warm时有13个由pymupdf领先；warm合计为160.8 ms对**81.2 ms** |
| PNG编码 | pylopdf快约9倍（**45.4 ms**对407.4 ms），但输出大40%（4,645 KiB对**3,318 KiB**） |
| 以2×渲染12页 | `render_pages()`从1 worker的165.3 ms降至8 workers的34.9 ms，**加速4.74倍**；pymupdf串行处理需423.1 ms |
| 提取全部文本 | 14个文件中13个由pylopdf领先，其余1个由pymupdf领先 |
| 提取一致性代理指标 | 在两个库都提取出文本的文件上，相似度为0.443到1.000；pylopdf不会从Type 3 fixture中提取文本 |
| Linux与Windows CI runner | pylopdf的提取优势更小，MuPDF在cold光栅化中也更快；见[其他平台](#other-platforms) |

## 文本提取 { #text-extraction }

提取所有页面，单位毫秒，越小越快。

| 文件 | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **70.6** | 291.7 | 404.8 | 4531.3 |
| bunka-kokugo-series-019-p4.pdf | **0.19** | 0.25 | 0.30 | 0.47 |
| f1040.pdf | **8.8** | 30.0 | 69.4 | 223.6 |
| mhlw-doc.pdf | **7.9** | 9.2 | 36.0 | 71.2 |
| nics-background-checks-2015-11.pdf | **4.9** | 7.5 | 52.0 | 119.5 |
| patent-us223898.pdf | 16.8 | **11.3** | 40.3 | 164.8 |
| pdf20-simple.pdf | **0.21** | 0.59 | 0.56 | 0.82 |
| pdfium-jpx-lzw.pdf | **0.11** | 0.26 | 0.16 | 0.31 |
| pdfium-links-highlights-annots.pdf | **0.29** | 0.75 | 0.32 | 1.3 |
| pdfium-smask-blend.pdf | **0.10** | 0.26 | 0.19 | 0.55 |
| pdfium-type3.pdf | **0.12** | 0.27 | 0.29 | 0.50 |
| senate-expenditures.pdf | **2.6** | 6.8 | 49.4 | 110.0 |
| usrguide.pdf | **27.6** | 63.1 | 282.3 | 785.5 |
| wdl6812-manuscript.pdf | **0.21** | 0.63 | 0.57 | 0.95 |
| **合计** | **140.4** | 422.5 | 936.5 | 6010.8 |

## 提取内容 { #extraction-content }

这只是代理指标，不是正确率。文本经空白归一化后与pymupdf比较。表单和旋转表格的相似度
较低，可能只是阅读顺序或空白策略不同；即使字符数相同也会发生这种情况。字符数为0的行是
纯图像PDF；`pdfium-type3.pdf`除外，hayro没有为其中的glyph报告Unicode映射，因此
pylopdf会跳过它们。

| 文件 | pylopdf字符数 | pymupdf字符数 | 相似度 |
|---|---:|---:|---:|
| bill-hr815.pdf | 300559 | 300559 | 1.000 |
| bunka-kokugo-series-019-p4.pdf | 0 | 0 | 1.000 |
| f1040.pdf | 10156 | 10156 | 0.683 |
| mhlw-doc.pdf | 1264 | 1251 | 0.961 |
| nics-background-checks-2015-11.pdf | 5650 | 5650 | 0.855 |
| patent-us223898.pdf | 11219 | 11218 | 0.581 |
| pdf20-simple.pdf | 11 | 11 | 1.000 |
| pdfium-jpx-lzw.pdf | 0 | 0 | 1.000 |
| pdfium-links-highlights-annots.pdf | 92 | 92 | 1.000 |
| pdfium-smask-blend.pdf | 0 | 0 | 1.000 |
| pdfium-type3.pdf | 0 | 5 | 0.000 |
| senate-expenditures.pdf | 4516 | 4516 | 0.443 |
| usrguide.pdf | 55624 | 55560 | 0.996 |
| wdl6812-manuscript.pdf | 0 | 0 | 1.000 |

## 合并 { #merge }

| 任务 | pylopdf | pymupdf | pypdf |
|---|---:|---:|---:|
| 合并语料库全部14个文件 | **32.8** | 116.2 | 145.4 |

## 渲染 { #rendering }

第一页按2×处理，单位毫秒，越小越快。两个库都使用默认设置：pylopdf生成透明背景的
straight-alpha RGBA8，pymupdf生成白色背景的RGB。

### 完整流程 { #rendering-end-to-end }

每次运行都会打开新文档、光栅化页面并编码为PNG。

| 文件 | pylopdf | pymupdf |
|---|---:|---:|
| bill-hr815.pdf | **19.5** | 58.0 |
| bunka-kokugo-series-019-p4.pdf | **21.0** | 84.9 |
| f1040.pdf | **25.2** | 67.5 |
| mhlw-doc.pdf | **18.8** | 50.4 |
| nics-background-checks-2015-11.pdf | **29.1** | 53.3 |
| patent-us223898.pdf | **15.7** | 52.2 |
| pdf20-simple.pdf | **3.4** | 13.1 |
| pdfium-jpx-lzw.pdf | **10.4** | 38.5 |
| pdfium-links-highlights-annots.pdf | **6.1** | 25.7 |
| pdfium-smask-blend.pdf | **0.92** | 3.5 |
| pdfium-type3.pdf | **0.66** | 1.9 |
| senate-expenditures.pdf | **29.6** | 39.7 |
| usrguide.pdf | **15.7** | 38.5 |
| wdl6812-manuscript.pdf | **20.2** | 64.1 |
| **合计** | **216.1** | 591.3 |

这一领先主要来自PNG编码，而非光栅化。下面两个表格拆分了相同的工作。

### 光栅化 { #rasterization }

cold每次重复都会打开新文档；warm则对已打开文档的同一页再次光栅化。MuPDF进程级
glyph cache等比文档存活更久的缓存，在cold运行中同样有效。MuPDF还会为已打开的文档
保留已加载的字体和已渲染的glyph，这在文字密集的页面上影响最大。pylopdf目前每次渲染
调用都会新建包含字体和glyph outline的hayro render cache，因此warm与cold时间接近。

| 文件 | pylopdf cold | pymupdf cold | pylopdf warm | pymupdf warm |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | 13.5 | **8.5** | 12.5 | **4.0** |
| bunka-kokugo-series-019-p4.pdf | **16.7** | 49.8 | 16.5 | **14.6** |
| f1040.pdf | **19.7** | 26.4 | 16.3 | **15.6** |
| mhlw-doc.pdf | 14.5 | **13.1** | 13.8 | **3.5** |
| nics-background-checks-2015-11.pdf | 23.1 | **7.4** | 22.6 | **5.6** |
| patent-us223898.pdf | **11.6** | 22.0 | 11.5 | **11.4** |
| pdf20-simple.pdf | 2.1 | **0.78** | 1.9 | **0.32** |
| pdfium-jpx-lzw.pdf | **8.8** | 14.9 | **8.6** | 9.7 |
| pdfium-links-highlights-annots.pdf | 4.4 | **2.1** | 4.0 | **1.2** |
| pdfium-smask-blend.pdf | 0.79 | **0.62** | 0.48 | **0.39** |
| pdfium-type3.pdf | 0.59 | **0.27** | 0.29 | **0.04** |
| senate-expenditures.pdf | 24.8 | **5.4** | 24.4 | **3.6** |
| usrguide.pdf | 12.6 | **8.1** | 11.4 | **0.71** |
| wdl6812-manuscript.pdf | **16.8** | 21.8 | 16.5 | **10.6** |
| **合计** | **169.9** | 181.1 | 160.8 | **81.2** |

### PNG编码 { #png-encoding }

两个编码器都接收已经渲染好的光栅图像。pylopdf对RGBA8输出使用快速fdeflate压缩，以
文件大小换取速度；pymupdf对RGB输出使用zlib默认级别。若文件大小比编码时间更重要，
请在外部重新压缩pylopdf的输出。

| 文件 | pylopdf ms | pymupdf ms | pylopdf KiB | pymupdf KiB |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **5.8** | 51.1 | 328.3 | **276.1** |
| bunka-kokugo-series-019-p4.pdf | **4.2** | 33.7 | 438.8 | **249.4** |
| f1040.pdf | **5.8** | 39.9 | 841.3 | **360.9** |
| mhlw-doc.pdf | **4.2** | 37.3 | 340.5 | **301.4** |
| nics-background-checks-2015-11.pdf | **6.0** | 46.1 | 737.1 | **343.0** |
| patent-us223898.pdf | **3.9** | 28.6 | 245.0 | **154.6** |
| pdf20-simple.pdf | **1.1** | 12.5 | 28.4 | **14.6** |
| pdfium-jpx-lzw.pdf | **1.4** | 23.7 | 39.9 | **9.4** |
| pdfium-links-highlights-annots.pdf | **1.7** | 23.9 | 53.5 | **21.5** |
| pdfium-smask-blend.pdf | **0.12** | 2.8 | 4.2 | **2.6** |
| pdfium-type3.pdf | **0.11** | 1.5 | 3.5 | **1.1** |
| senate-expenditures.pdf | **4.1** | 33.7 | 332.8 | **253.8** |
| usrguide.pdf | **3.4** | 30.5 | 188.8 | **138.0** |
| wdl6812-manuscript.pdf | **3.5** | 42.0 | **1062.9** | 1191.3 |
| **合计** | **45.4** | 407.4 | 4645.1 | **3317.7** |

## 并行渲染 { #parallel-rendering }

将`usrguide.pdf`前12页输出为2× PNG，单位毫秒，越小越快。
批处理保持输入顺序，并使用同一个不可变文档快照。

| Workers | 时间 | 相对1 worker加速 |
|---:|---:|---:|
| 1 | 165.3 | 1.00倍 |
| 2 | 89.8 | 1.84倍 |
| 4 | 48.6 | 3.40倍 |
| 8 | 34.9 | 4.74倍 |

实际并发度同时受指定worker数和约512 MB的估算实时渲染内存限制。作为参考，pymupdf在
一个新文档上串行渲染并编码同样的12页耗时423.1 ms。

## 其他平台 { #other-platforms }

上面的快照来自一台macOS arm64机器。2026-10-17，我们使用thin LTO的abi3 wheel，在每个
GitHub-hosted release runner上各运行一次同样的`bench/run.py`（Python 3.14.8，仅macOS
x86_64为3.14.7；预热1次，测量5次）。每个单元格为pylopdf / pymupdf的语料库合计（毫秒）。
共享runner的噪声比快照更大，请将较小的差异视为噪声。

| Runner | 文本提取 | 合并 | 完整流程 | 光栅化 cold | 光栅化 warm | PNG编码 |
|---|---:|---:|---:|---:|---:|---:|
| Linux x86_64, AMD EPYC 9V74, 4 vCPU | **166.4** / 208.2 | **29.7** / 97.7 | **322.1** / 628.2 | 280.8 / **221.0** | 266.3 / **102.2** | **39.0** / 401.7 |
| Linux aarch64, 4 vCPU | **189.0** / 267.8 | **37.8** / 111.3 | **359.3** / 744.4 | 278.2 / **220.9** | 265.1 / **99.8** | **78.9** / 526.5 |
| macOS arm64, Apple M1 (virtual), 3 vCPU | **140.8** / 509.8 | **32.2** / 131.1 | **225.1** / 611.0 | **175.8** / 187.3 | 164.3 / **83.3** | **46.6** / 424.3 |
| macOS x86_64, Intel Core i7-8700B, 4 vCPU | **282.1** / 1307.9 | **67.8** / 398.6 | **553.4** / 823.1 | 461.9 / **351.6** | 440.1 / **156.3** | **60.8** / 473.1 |
| Windows Server 2025 x86_64, Intel, 4 vCPU | **245.0** / 277.8 | **53.8** / 130.9 | **593.0** / 909.8 | 521.5 / **307.0** | 505.3 / **160.1** | **61.8** / 599.2 |

pymupdf的文本提取在两个macOS runner上都比在Linux和Windows上慢得多，因此pylopdf在Linux和
Windows上的提取优势小于macOS快照。warm时MuPDF在所有runner上光栅化更快，cold时除Apple M1
外也都更快；但由于PNG编码占pymupdf合计的大部分，完整流程仍由pylopdf领先。

## free-threaded提取 { #free-threaded-extraction }

在Windows 11的free-threaded CPython 3.14.6上，对两个互相独立的
`bill-hr815.pdf`执行全页文本提取。预热一次后，交替先运行的模式，取七组配对运行的
中位数：

| 模式 | Workers | 时间 | 加速比 |
|---|---:|---:|---:|
| 串行 | 1 | 400.8 ms | 1.00倍 |
| 并行 | 2 | 235.5 ms | 1.70倍 |

每次运行中两个副本的输出都完全一致，并且解释器确认导入后GIL仍保持禁用。

## 复现 { #reproduce }

语料库位于`tests/assets/real_world`，文件来源与许可证记录在同一目录。

```bash
uv sync --all-extras --group bench
uv run python bench/run.py
uv run python tools/pyodide_compat.py --root . --benchmark-only \
  --benchmark-output .tmp/limits-benchmark.json
# 在Windows上使用free-threaded CPython 3.14解释器：
py -3.14t bench/free_threaded.py
# 在POSIX上：
python3.14t bench/free_threaded.py
```

生成的原始报告提交在
[`bench/results/latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/latest.md)。
单独生成的free-threaded报告提交在
[`bench/results/free-threaded-latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/free-threaded-latest.md)；
重新运行`bench/run.py`不会覆盖CPython 3.14t证据。
native／Pyodide资源策略基准另行提交在
[`bench/results/limits-latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/limits-latest.md)。
第二条command测量有界open/extract和受控拒绝。CI还会在Pyodide中运行相同case并
记录Wasm linear memory增长；这些时间和memory值只表示趋势，不是native/Wasm
性能结论。
引用数据时，请同时提供运行环境与语料库。
