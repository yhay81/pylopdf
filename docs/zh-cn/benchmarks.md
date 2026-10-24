---
title: 性能基准
description: 可复现的pylopdf文本提取、合并、光栅化与PNG编码基准，同时公开优势与劣势。
---

# 性能基准

这些基准在14个真实PDF上将pylopdf与pymupdf、pypdf和pdfplumber进行比较，既展示pylopdf更快的情况，也展示它更慢的情况。数据来自一台机器和一个语料库，因此请把它当作判断应在自己的文档上测量什么的参考，而不是排名。

!!! info "最近一次运行"
    **2026-10-24 00:29 UTC** · macOS 27.0.1 · Python 3.14.8 · Apple M2 Pro（arm64）<br>从Rendering里程碑分支（hayro 0.8.0）使用thin LTO release profile构建的pylopdf · pymupdf 2.0 (MuPDF 1.28.5) · pypdf 6.21.1 · pdfplumber 0.11.10<br>预热1次，测量7次；表中为中位数，单位毫秒。

## 概览 { #overview }

| 工作负载 | 最新语料库的结果 |
|---|---|
| 合并14个真实PDF | pylopdf **19.7 ms**，pymupdf 136.0 ms，pypdf 120.7 ms |
| 第一页输出为2× PNG（完整流程） | 14个文件均由pylopdf领先，合计**143.5 ms**对514.5 ms |
| 仅光栅化 | cold时14个文件中有10个由pylopdf领先（2个持平），warm时有5个；合计cold为**103.4** / 186.1 ms，warm为87.8 / **81.0** ms（pylopdf / pymupdf） |
| PNG编码 | pylopdf编码快约8倍（**38.6 ms**对319.0 ms），但输出大55%（5,132 KiB对**3,318 KiB**） |
| 以2×渲染12页 | `doc.render()`从1个worker的109.4 ms降至8个worker的33.1 ms，**加速3.30倍**；pymupdf串行处理需332.7 ms |
| 提取全部文本 | 14个文件均由pylopdf领先，合计**117.1 ms**对229.7 ms |
| 提取一致性代理指标 | 在两个库都能提取出文本的文件上，相似度为0.443到1.000；pylopdf不会从Type 3 fixture中提取文本 |
| Linux与Windows CI runner | 基于pylopdf 0.14.0测量；pylopdf的提取优势更小，MuPDF在cold光栅化中也更快；参见[其他平台](#other-platforms) |

## 文本提取 { #text-extraction }

提取所有页面，单位毫秒，越小越快。

| 文件 | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **59.4** | 127.5 | 376.9 | 4865.3 |
| bunka-kokugo-series-019-p4.pdf | **0.05** | 0.29 | 0.44 | 0.52 |
| f1040.pdf | **17.3** | 34.6 | 156.1 | 414.1 |
| mhlw-doc.pdf | **9.6** | 9.9 | 49.9 | 76.9 |
| nics-background-checks-2015-11.pdf | **3.4** | 5.6 | 56.8 | 131.1 |
| patent-us223898.pdf | **4.7** | 6.1 | 46.6 | 168.3 |
| pdf20-simple.pdf | **0.05** | 0.48 | 0.69 | 0.84 |
| pdfium-jpx-lzw.pdf | **0.02** | 0.28 | 0.25 | 0.34 |
| pdfium-links-highlights-annots.pdf | **0.12** | 0.74 | 0.45 | 1.4 |
| pdfium-smask-blend.pdf | **0.04** | 0.31 | 0.29 | 0.55 |
| pdfium-type3.pdf | **0.03** | 0.32 | 0.39 | 0.50 |
| senate-expenditures.pdf | **2.4** | 4.7 | 52.0 | 114.2 |
| usrguide.pdf | **19.7** | 38.1 | 312.9 | 821.2 |
| wdl6812-manuscript.pdf | **0.21** | 0.68 | 0.77 | 0.97 |
| **合计** | **117.1** | 229.7 | 1054.4 | 6596.2 |

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
| 合并语料库全部14个文件 | **19.7** | 136.0 | 120.7 |

## 渲染 { #rendering }

第一页按2×渲染，单位毫秒，越小越快。两个库都使用默认设置：白色背景的不透明RGB。

### 完整流程 { #rendering-end-to-end }

每次运行都会打开新文档、光栅化页面并编码为PNG。

| 文件 | pylopdf | pymupdf |
|---|---:|---:|
| bill-hr815.pdf | **13.2** | 45.4 |
| bunka-kokugo-series-019-p4.pdf | **15.8** | 78.5 |
| f1040.pdf | **18.5** | 62.1 |
| mhlw-doc.pdf | **13.3** | 41.9 |
| nics-background-checks-2015-11.pdf | **16.7** | 44.0 |
| patent-us223898.pdf | **10.4** | 43.6 |
| pdf20-simple.pdf | **1.6** | 9.2 |
| pdfium-jpx-lzw.pdf | **7.3** | 30.3 |
| pdfium-links-highlights-annots.pdf | **2.8** | 16.7 |
| pdfium-smask-blend.pdf | **0.34** | 2.6 |
| pdfium-type3.pdf | **0.24** | 1.2 |
| senate-expenditures.pdf | **15.5** | 32.9 |
| usrguide.pdf | **12.5** | 39.1 |
| wdl6812-manuscript.pdf | **15.3** | 66.8 |
| **合计** | **143.5** | 514.5 |

这一领先主要来自PNG编码，而非光栅化。下面两个表格将同样的工作拆开来看。

### 光栅化 { #rasterization }

cold运行每次重复都会打开新文档；warm运行则对已打开文档的同一页再次光栅化。比文档存活更久的缓存（例如MuPDF进程级的字形缓存）在cold运行中同样有效。pylopdf在打开文档的线程上为每个文档保留一个hayro渲染缓存，因此字体、字形轮廓和图像只解析一次；MuPDF还会保留已渲染的字形位图，所以在文字密集的页面上warm时仍由MuPDF领先（`usrguide.pdf`为4.5 ms对0.74 ms），而扫描和图像密集的页面由pylopdf领先。能弥补这一差距的字形缓存正在上游讨论范围（[LaurenzV/hayro#1613](https://github.com/LaurenzV/hayro/issues/1613)），而省去覆盖整页的CropBox裁剪的[LaurenzV/hayro#1612](https://github.com/LaurenzV/hayro/pull/1612)仅在hayro中就使warm合计缩短9%。

| 文件 | pylopdf cold | pymupdf cold | pylopdf warm | pymupdf warm |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **7.8** | **7.8** | 6.4 | **3.2** |
| bunka-kokugo-series-019-p4.pdf | **12.3** | 51.7 | **12.0** | 15.0 |
| f1040.pdf | **13.1** | 28.3 | **8.8** | 16.8 |
| mhlw-doc.pdf | **9.7** | 12.1 | 5.4 | **3.0** |
| nics-background-checks-2015-11.pdf | 11.3 | **8.0** | 9.8 | **4.8** |
| patent-us223898.pdf | **7.0** | 22.2 | **7.1** | 11.7 |
| pdf20-simple.pdf | **0.65** | 0.82 | 0.60 | **0.28** |
| pdfium-jpx-lzw.pdf | **6.0** | 14.9 | **5.8** | 9.9 |
| pdfium-links-highlights-annots.pdf | **1.4** | **1.4** | 1.2 | **0.56** |
| pdfium-smask-blend.pdf | **0.23** | 0.56 | **0.19** | 0.32 |
| pdfium-type3.pdf | **0.14** | 0.30 | 0.10 | **0.04** |
| senate-expenditures.pdf | 12.1 | **5.5** | 11.3 | **3.6** |
| usrguide.pdf | **8.8** | 9.6 | 4.5 | **0.74** |
| wdl6812-manuscript.pdf | **12.9** | 23.0 | 14.6 | **11.0** |
| **合计** | **103.4** | 186.1 | 87.8 | **81.0** |

### PNG编码 { #png-encoding }

两个编码器都接收已经渲染好的光栅图像。pylopdf使用快速的fdeflate压缩，以文件大小换取速度；pymupdf使用zlib的默认级别。如果文件大小比编码时间更重要，请对pylopdf的输出重新压缩。

| 文件 | pylopdf ms | pymupdf ms | pylopdf KiB | pymupdf KiB |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **5.8** | 36.8 | 535.8 | **276.1** |
| bunka-kokugo-series-019-p4.pdf | **3.5** | 25.9 | 420.7 | **249.4** |
| f1040.pdf | **5.0** | 34.0 | 816.8 | **360.9** |
| mhlw-doc.pdf | **3.8** | 30.0 | 581.9 | **301.4** |
| nics-background-checks-2015-11.pdf | **5.0** | 35.0 | 689.9 | **343.0** |
| patent-us223898.pdf | **3.2** | 21.1 | 232.4 | **154.6** |
| pdf20-simple.pdf | **0.85** | 8.1 | 27.2 | **14.6** |
| pdfium-jpx-lzw.pdf | **1.1** | 15.3 | 31.8 | **9.4** |
| pdfium-links-highlights-annots.pdf | **1.2** | 15.0 | 54.9 | **21.5** |
| pdfium-smask-blend.pdf | **0.10** | 2.0 | 3.4 | **2.6** |
| pdfium-type3.pdf | **0.08** | 0.90 | 3.0 | **1.1** |
| senate-expenditures.pdf | **3.4** | 27.8 | 578.7 | **253.8** |
| usrguide.pdf | **2.8** | 22.6 | 253.3 | **138.0** |
| wdl6812-manuscript.pdf | **2.8** | 44.6 | **901.7** | 1191.3 |
| **合计** | **38.6** | 319.0 | 5131.6 | **3317.7** |

## 并行渲染 { #parallel-rendering }

将`usrguide.pdf`的前12页输出为2× PNG，单位毫秒，越小越快。批处理保持输入顺序，并使用同一个不可变的文档快照。

| Workers | 时间 | 相对1个worker的加速 |
|---:|---:|---:|
| 1 | 109.4 | 1.00x |
| 2 | 63.3 | 1.73x |
| 4 | 45.5 | 2.41x |
| 8 | 33.1 | 3.30x |

实际并发度同时受所请求的worker数和约512 MB的估算渲染内存限制。作为参考，pymupdf在一个新打开的文档上串行渲染并编码同样的12页耗时332.7 ms。

## 其他平台 { #other-platforms }

这些运行测量的是hayro 0.7上的pylopdf 0.14.0，早于保留渲染缓存的版本；上面的macOS快照更新。

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
