---
title: WebAssembly
description: 在Cloudflare Python Workers和Pyodide中运行pylopdf：已测试的版本，以及与原生Python不同的功能。
---

# WebAssembly

pylopdf发布了可在Cloudflare Python Workers和Pyodide中运行的WebAssembly wheel。它包含与原生wheel相同的Rust代码，因此API和结果都相同；它不使用JavaScript PDF库。

## 支持的环境 { #supported-environments }

| 环境 | 状态 |
|---|---|
| Cloudflare Python Workers | 支持。每个版本都会用发布的wheel构建并启动一个Worker来测试 |
| Pyodide 0.28.3 | 修改wheel的标签后即可支持（见下文）。完整的兼容性测试套件会在其中运行 |
| 用Pyodide 0.28.3的`micropip`从PyPI安装 | 不支持：该版本的`micropip`无法识别wheel的`pyemscripten`平台标签 |
| 其他Pyodide或WebAssembly Python版本 | 未测试 |

该wheel面向Python 3.13和`pyemscripten_2025_0_wasm32`平台，即Pyodide 0.28.3和Cloudflare `workers-py` 1.15.0所使用的运行时。在浏览器或Worker中无法回退到从源码构建。

## Cloudflare Worker { #cloudflare-worker }

仓库中包含一个[经过测试的示例Worker](https://github.com/yhay81/pylopdf/tree/main/examples/cloudflare-worker)，它接收PDF并返回页数和第一页的文本：

```bash
git clone https://github.com/yhay81/pylopdf.git
cd pylopdf/examples/cloudflare-worker
uv sync
uv run pywrangler dev
```

在另一个终端中运行：

```bash
curl http://localhost:8787/health

curl --request POST \
  --header "content-type: application/pdf" \
  --data-binary @document.pdf \
  http://localhost:8787
```

使用`uv run pywrangler deploy`部署。打包了pylopdf的Worker超过了Workers Free计划3 MB的压缩大小上限，因此需要Workers Paid计划。

该示例将输入限制为4 MiB，并设置了比`DocumentLimits.web()`更严格的[`DocumentLimits`](security.md#untrusted-pdfs)，因为Worker的128 MiB内存预算要由Python、JavaScript和请求本身共同使用。pylopdf会读取完整的请求体，因此如果你自己的代码需要更多内存，请进一步调低这些上限。

## Pyodide { #pyodide }

Pyodide 0.28.3的`micropip`不接受已发布wheel的`pyemscripten`标签，但其中的二进制文件可以在Pyodide 0.28.3中运行：CI会以`pyodide_2025_0_wasm32`标签对同一二进制文件运行完整的测试套件。请下载wheel，并用`wheel`工具将标签改为该标签。该工具也会更新wheel内部的元数据（仅重命名文件是不够的）：

```bash
pip download pylopdf --no-deps --only-binary=:all: \
  --platform pyemscripten_2025_0_wasm32 --python-version 3.13 -d wheels
pip install wheel
wheel tags --platform-tag pyodide_2025_0_wasm32 --remove wheels/pylopdf-*.whl
```

然后将改好标签的wheel放在Pyodide能够访问的URL上，用`micropip`安装：

```javascript
const pyodide = await loadPyodide();
await pyodide.loadPackage("micropip");
await pyodide.runPythonAsync(`
import micropip
await micropip.install(
    "https://example.com/pylopdf-0.15.0-cp311-abi3-pyodide_2025_0_wasm32.whl"
)
`);
```

使用`tools/build_pyodide.sh`从源码构建的wheel（参见[CONTRIBUTING.md](https://github.com/yhay81/pylopdf/blob/main/CONTRIBUTING.md#pyodide-0283-build)）同样带有`pyemscripten`标签，也需要执行相同的步骤。

文件路径指向Pyodide的虚拟文件系统，因此请以bytes形式传入浏览器中的数据：

```python
import pylopdf

with pylopdf.open(pdf_bytes, limits=pylopdf.DocumentLimits.web()) as doc:
    text = doc.pages[0].text() if doc.pages else ""
    output = doc.to_bytes()
```

可通过`sys.platform == "emscripten"`检测WebAssembly运行时。

## 与原生Python的差异 { #differences }

提取、渲染、编辑、文本生成、表单和加密等其他所有功能都与原生Python上一样工作。

| 功能 | 在WebAssembly中 |
|---|---|
| `render_pages(workers=...)` | 接受该参数，但页面会逐一渲染 |
| 内置OCR（`OcrEngine`、`get_text_ocr`、`apply_ocr`） | 不可用，会抛出`OcrError`。请在其他地方执行OCR，再用`Page.insert_ocr_text_layer()`添加结果 |
| 字体extra（`pylopdf[jp]`等） | 未测试。请用`fontbuffer=`或`set_fallback_font()`显式传入字体数据 |

即使PDF文件本身很小，以高分辨率渲染大页面也需要把整张图像放在内存中；而且资源上限无法终止长时间运行的调用：请像在原生Python上一样，在宿主环境中强制执行时间限制。

## 大小与测试 { #size-and-testing }

每个版本都会在原生环境和Pyodide中运行同一套兼容性测试，并要求结果完全一致。测试涵盖从bytes打开、加密文件、文本、单词、搜索、Markdown、日文竖排、多栏页面、表格、矢量绘图、渲染、以标准字体和嵌入字体生成的文本、编辑、保存以及错误处理。大小、启动时间和内存用量在CI中测量，并发布在[`bench/results/wasm-latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/wasm-latest.md)。

新的Pyodide、Emscripten、`workers-py`或Wrangler版本只有在完整测试套件通过后才会被支持。
