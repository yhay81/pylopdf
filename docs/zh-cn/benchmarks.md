---
title: 性能基准
description: 可复现的pylopdf文本提取、合并、光栅化与PNG编码基准，同时公开优势与劣势。
---

# 性能基准

这些基准在14个真实PDF上将pylopdf与pymupdf、pypdf和pdfplumber进行比较，既展示pylopdf更快的情况，也展示它更慢的情况。数据来自一台机器和一个语料库，因此请把它当作判断应在自己的文档上测量什么的参考，而不是排名。

!!! info "最近一次运行"
    **2026-10-17 17:29 UTC** · macOS 27.0.1 · Python 3.14.8 · Apple M2 Pro（arm64）<br>使用thin LTO release profile构建的pylopdf 0.14.0 · pymupdf 1.28.0 · pypdf 6.15.0 · pdfplumber 0.11.10<br>预热1次，测量7次；表中为中位数，单位毫秒。

## 概览 { #overview }

| 工作负载 | 最新语料库的结果 |
|---|---|
| 合并14个真实PDF | pylopdf **15.4 ms**，pymupdf 111.2 ms，pypdf 141.5 ms |
| 第一页输出为2× PNG（完整流程） | 14个文件均由pylopdf领先，合计**212.9 ms**对573.9 ms |
| 仅光栅化 | 14个文件中，cold时有9个、warm时有12个由pymupdf领先（warm另有1个持平）；warm合计为pylopdf 145.9 ms、pymupdf **78.8 ms** |
| PNG编码 | pylopdf编码快约9倍（**44.4 ms**对397.4 ms），但输出大40%（4,645 KiB对**3,318 KiB**） |
| 以2×渲染12页 | `render_pages()`从1个worker的163.7 ms降至8个worker的34.6 ms，**加速4.74倍**；pymupdf串行处理需425.3 ms |
| 提取全部文本 | 14个文件中12个由pylopdf领先，pymupdf和pypdf各领先1个 |
| 提取一致性代理指标 | 在两个库都能提取出文本的文件上，相似度为0.443到1.000；pylopdf不会从Type 3 fixture中提取文本 |
| Linux与Windows CI runner | pylopdf的提取优势更小，MuPDF在cold光栅化中也更快；参见[其他平台](#other-platforms) |

## 文本提取 { #text-extraction }

提取所有页面，单位毫秒，越小越快。

| 文件 | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **71.3** | 298.5 | 407.8 | 4295.0 |
| bunka-kokugo-series-019-p4.pdf | **0.17** | 0.25 | 0.30 | 0.45 |
| f1040.pdf | **8.9** | 28.2 | 68.6 | 201.8 |
| mhlw-doc.pdf | **8.0** | 9.1 | 34.0 | 70.1 |
| nics-background-checks-2015-11.pdf | **4.8** | 7.5 | 51.4 | 119.7 |
| patent-us223898.pdf | 16.9 | **11.3** | 39.6 | 165.3 |
| pdf20-simple.pdf | **0.17** | 0.62 | 0.54 | 0.80 |
| pdfium-jpx-lzw.pdf | **0.14** | 0.26 | 0.16 | 0.31 |
| pdfium-links-highlights-annots.pdf | 0.33 | 0.69 | **0.32** | 1.4 |
| pdfium-smask-blend.pdf | **0.07** | 0.27 | 0.18 | 0.51 |
| pdfium-type3.pdf | **0.08** | 0.29 | 0.29 | 0.48 |
| senate-expenditures.pdf | **2.8** | 6.6 | 49.9 | 110.3 |
| usrguide.pdf | **27.4** | 64.5 | 279.1 | 799.3 |
| wdl6812-manuscript.pdf | **0.15** | 0.58 | 0.53 | 0.93 |
| **合计** | **141.2** | 428.6 | 932.6 | 5766.4 |

## 提取内容 { #extraction-content }

这只是代理指标，不是正确率。文本经空白归一化后与pymupdf比较。表单和旋转表格的相似度较低，可能只是阅读顺序或空白处理方式不同，即使字符数相同也会如此。字符数为0的行是纯图像PDF；`pdfium-type3.pdf`除外：hayro没有为其中的字形报告Unicode映射，因此pylopdf会跳过它们。

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
| 合并语料库全部14个文件 | **15.4** | 111.2 | 141.5 |

## 渲染 { #rendering }

第一页按2×渲染，单位毫秒，越小越快。两个库都使用所测版本的默认设置：pylopdf 0.14.0生成透明背景的straight-alpha RGBA8，pymupdf生成白色背景的RGB。当前的pylopdf与pymupdf一样，默认生成白色背景的RGB。

### 完整流程 { #rendering-end-to-end }

每次运行都会打开新文档、光栅化页面并编码为PNG。

| 文件 | pylopdf | pymupdf |
|---|---:|---:|
| bill-hr815.pdf | **19.4** | 57.0 |
| bunka-kokugo-series-019-p4.pdf | **19.6** | 82.9 |
| f1040.pdf | **25.7** | 65.3 |
| mhlw-doc.pdf | **18.4** | 48.5 |
| nics-background-checks-2015-11.pdf | **28.8** | 50.6 |
| patent-us223898.pdf | **15.4** | 50.4 |
| pdf20-simple.pdf | **3.3** | 13.4 |
| pdfium-jpx-lzw.pdf | **10.0** | 37.3 |
| pdfium-links-highlights-annots.pdf | **6.0** | 26.1 |
| pdfium-smask-blend.pdf | **0.77** | 3.2 |
| pdfium-type3.pdf | **0.55** | 1.7 |
| senate-expenditures.pdf | **28.9** | 38.3 |
| usrguide.pdf | **15.9** | 37.0 |
| wdl6812-manuscript.pdf | **20.1** | 62.3 |
| **合计** | **212.9** | 573.9 |

这一领先主要来自PNG编码，而非光栅化。下面两个表格将同样的工作拆开来看。

### 光栅化 { #rasterization }

cold运行每次重复都会打开新文档；warm运行则对已打开文档的同一页再次光栅化。比文档存活更久的缓存（例如MuPDF进程级的字形缓存）在cold运行中同样有效。MuPDF还会为已打开的文档保留已加载的字体和已渲染的字形，这在文字密集的页面上影响最大。pylopdf会在打开文档的线程上复用已解析的字体和字形轮廓，但仍会重新光栅化每个字形并重新解码每个图像，因此warm运行带来的提升远小于MuPDF。

| 文件 | pylopdf cold | pymupdf cold | pylopdf warm | pymupdf warm |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | 12.9 | **8.3** | 11.5 | **4.0** |
| bunka-kokugo-series-019-p4.pdf | **15.2** | 49.6 | **14.4** | **14.4** |
| f1040.pdf | **19.3** | 26.1 | 15.8 | **15.6** |
| mhlw-doc.pdf | 14.0 | **12.5** | 9.8 | **3.3** |
| nics-background-checks-2015-11.pdf | 22.9 | **7.3** | 21.5 | **4.8** |
| patent-us223898.pdf | **11.3** | 21.9 | 11.0 | **10.9** |
| pdf20-simple.pdf | 2.1 | **0.71** | 2.0 | **0.26** |
| pdfium-jpx-lzw.pdf | **8.5** | 14.2 | **8.1** | 9.5 |
| pdfium-links-highlights-annots.pdf | 4.3 | **2.0** | 3.9 | **1.2** |
| pdfium-smask-blend.pdf | 0.60 | **0.57** | 0.47 | **0.36** |
| pdfium-type3.pdf | 0.42 | **0.25** | 0.26 | **0.03** |
| senate-expenditures.pdf | 24.4 | **5.0** | 23.1 | **3.3** |
| usrguide.pdf | 12.5 | **7.4** | 8.0 | **0.67** |
| wdl6812-manuscript.pdf | **16.4** | 21.1 | 16.2 | **10.7** |
| **合计** | **164.7** | 177.1 | 145.9 | **78.8** |

### PNG编码 { #png-encoding }

两个编码器都接收已经渲染好的光栅图像。pylopdf对RGBA8输出使用快速的fdeflate压缩，以文件大小换取速度；pymupdf对RGB输出使用zlib的默认级别。如果文件大小比编码时间更重要，请对pylopdf的输出重新压缩。

| 文件 | pylopdf ms | pymupdf ms | pylopdf KiB | pymupdf KiB |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **5.7** | 49.2 | 328.3 | **276.1** |
| bunka-kokugo-series-019-p4.pdf | **4.0** | 33.1 | 438.8 | **249.4** |
| f1040.pdf | **5.8** | 39.5 | 841.3 | **360.9** |
| mhlw-doc.pdf | **4.1** | 36.2 | 340.5 | **301.4** |
| nics-background-checks-2015-11.pdf | **5.8** | 43.0 | 737.1 | **343.0** |
| patent-us223898.pdf | **3.9** | 27.7 | 245.0 | **154.6** |
| pdf20-simple.pdf | **1.1** | 12.0 | 28.4 | **14.6** |
| pdfium-jpx-lzw.pdf | **1.3** | 23.1 | 39.9 | **9.4** |
| pdfium-links-highlights-annots.pdf | **1.6** | 23.8 | 53.5 | **21.5** |
| pdfium-smask-blend.pdf | **0.12** | 2.6 | 4.2 | **2.6** |
| pdfium-type3.pdf | **0.10** | 1.4 | 3.5 | **1.1** |
| senate-expenditures.pdf | **4.0** | 33.1 | 332.8 | **253.8** |
| usrguide.pdf | **3.3** | 29.5 | 188.8 | **138.0** |
| wdl6812-manuscript.pdf | **3.6** | 43.4 | **1062.9** | 1191.3 |
| **合计** | **44.4** | 397.4 | 4645.1 | **3317.7** |

## 并行渲染 { #parallel-rendering }

将`usrguide.pdf`的前12页输出为2× PNG，单位毫秒，越小越快。批处理保持输入顺序，并使用同一个不可变的文档快照。

| Workers | 时间 | 相对1个worker的加速 |
|---:|---:|---:|
| 1 | 163.7 | 1.00× |
| 2 | 88.4 | 1.85× |
| 4 | 47.4 | 3.45× |
| 8 | 34.6 | 4.74× |

实际并发度同时受所请求的worker数和约512 MB的估算渲染内存限制。作为参考，pymupdf在一个新打开的文档上串行渲染并编码同样的12页耗时425.3 ms。

## 其他平台 { #other-platforms }

上面的快照来自一台macOS arm64机器。2026-10-17，同一个`bench/run.py`使用thin LTO的abi3 wheel，在每个GitHub托管的release runner上各运行了一次（Python 3.14.8，macOS x86_64上为3.14.7；预热1次，测量5次）。每个单元格为pylopdf / pymupdf的语料库合计（毫秒）。共享runner的噪声比快照更大，请将较小的差异视为噪声。

| Runner | 文本提取 | 合并 | 完整流程 | 光栅化cold | 光栅化warm | PNG编码 |
|---|---:|---:|---:|---:|---:|---:|
| Linux x86_64, AMD EPYC 9V45, 4 vCPU | **119.7** / 146.8 | **14.3** / 73.8 | **234.8** / 459.9 | 198.1 / **167.1** | 182.9 / **72.9** | **33.4** / 284.7 |
| Linux aarch64, 4 vCPU | **186.0** / 270.3 | **27.5** / 111.7 | **372.1** / 720.7 | 289.0 / **226.1** | 264.2 / **101.3** | **81.4** / 505.7 |
| macOS arm64, Apple M1 (virtual), 3 vCPU | **171.3** / 601.1 | **22.4** / 179.7 | **290.4** / 772.1 | **220.2** / 251.7 | 204.3 / **110.3** | **57.9** / 539.1 |
| macOS x86_64, Intel Core i7-8700B, 4 vCPU | **254.7** / 1188.0 | **87.0** / 430.4 | **651.3** / 922.2 | 535.8 / **392.0** | 444.7 / **178.6** | **62.2** / 490.5 |
| Windows Server 2025 x86_64, Intel, 4 vCPU | **243.1** / 269.7 | **37.5** / 132.9 | **533.2** / 874.6 | 470.5 / **293.7** | 434.2 / **147.5** | **57.8** / 579.6 |

pymupdf的文本提取在两个macOS runner上都比在Linux和Windows上慢得多，因此pylopdf在Linux和Windows上的提取优势小于macOS快照中的优势。warm时MuPDF在所有runner上都光栅化得更快，cold时除Apple M1外也都更快。但由于PNG编码占了pymupdf合计时间的大部分，完整流程仍由pylopdf领先。

## 自由线程下的提取 { #free-threaded-extraction }

在Windows 11的自由线程CPython 3.14.6上，对两个互相独立的`bill-hr815.pdf`副本执行全页文本提取。预热一次后，交替先运行的模式，取七组配对运行的中位数：

| 模式 | Workers | 时间 | 加速比 |
|---|---:|---:|---:|
| 串行 | 1 | 400.8 ms | 1.00× |
| 并行 | 2 | 235.5 ms | 1.70× |

每次运行中两个副本的输出都完全一致，并且解释器报告导入后GIL仍保持禁用。

## 复现 { #reproduce }

语料库位于[`tests/assets/real_world`](https://github.com/yhay81/pylopdf/tree/main/tests/assets/real_world)，并附有每个文件的来源和许可证。在仓库的克隆中运行：

```bash
uv sync --all-extras --group bench
uv run python bench/run.py                 # 写入 bench/results/latest.md
python3.14t bench/free_threaded.py         # 需要自由线程的 CPython 3.14
```

最新的报告已提交为[`bench/results/latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/latest.md)和[`bench/results/free-threaded-latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/free-threaded-latest.md)。引用其中的数字时，请注明测量所用的环境和语料库。
