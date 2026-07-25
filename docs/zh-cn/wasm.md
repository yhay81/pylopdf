# WebAssembly 兼容性

pylopdf 为 Pyodide 0.28.3 和 Cloudflare Python Workers 使用的 Python 3.13 ABI
构建静态 PyEmscripten wheel。它包含与原生 wheel 相同的 Rust PDF 引擎，不使用
JavaScript PDF 实现或 wasm-bindgen shim。

!!! note "发布状态"

    WebAssembly wheel 将从 pylopdf 0.11 开始发布。v0.10.0 仅包含原生 wheel。
    在 v0.11 发布前，本文中的公开安装命令不会解析到可用版本。

## 已验证环境

| 组件 | 固定版本或契约 |
|---|---|
| Python runtime | CPython 3.13.2 |
| Pyodide | 0.28.3 |
| Emscripten toolchain | 4.0.9 |
| Wheel platform | PyEmscripten `2025_0`、`wasm32` |
| 构建与测试 Node.js | 固定 Emscripten SDK 中的 20.18.0 |
| Cloudflare SDK | `workers-py` 1.15.0 |
| Cloudflare bundler | Wrangler 4.114.0 |
| Worker compatibility date | 2026-07-26 |

发布的 artifact 使用 `cp310-abi3-pyemscripten_2025_0_wasm32`。构建器先以 runtime
原生的 `pyodide_2025_0_wasm32` tag 运行同一 binary，再确定性地 retag 为 PEP 783
发布格式。只有 PyEmscripten-tag artifact 会进入 PyPI、provenance attestation 和
release SBOM。

| 环境 | 状态 | 说明 |
|---|---|---|
| Cloudflare Python Workers | 支持并有发布 gate | CI 用固定 SDK 解析 PEP 783 wheel，并 dry-run Wrangler bundle。tag 发布会从 PyPI 重复验证后再创建 GitHub Release。 |
| Node.js 中的 Pyodide 0.28.3 | runtime 兼容 gate | CI 将 runtime-tagged 本地 wheel 安装进精确固定的 runtime，执行完整共享兼容 suite。 |
| 从 PyPI 直接安装到 browser | Pyodide 0.28.3 不支持 | 该版本 `micropip` 早于 PyPI 要求的 PEP 783 `pyemscripten_*` tag。binary 兼容，但 frontend 安装路径不兼容。 |
| 其他 Pyodide / Python-Wasm 版本 | 未测试 | 扩大 wheel tag 或支持范围前必须验证 platform 和 ABI。 |

## Cloudflare Worker

repository 包含一个
[经过测试的抽取 Worker](https://github.com/yhay81/pylopdf/tree/main/examples/cloudflare-worker)，
它接收有明确上限的 PDF body，并返回页数和第一页文本：

```bash
git clone https://github.com/yhay81/pylopdf.git
cd pylopdf/examples/cloudflare-worker
uv sync
uv run pywrangler dev
```

在另一个 terminal 中发送 PDF：

```bash
curl --request POST \
  --header "content-type: application/pdf" \
  --data-binary @document.pdf \
  http://localhost:8787
```

确认所选 Cloudflare plan 的上限和 compatibility date 后，再运行
`uv run pywrangler deploy`。CI 会复制这个实际 example，只将公开 pylopdf requirement
替换为刚构建的 wheel，再通过 `workers-py` 解析并运行
`wrangler deploy --dry-run`。

example 将输入限制为 4 MiB，并对结构和解压后数据采用比 `DocumentLimits.web()` 更严格的
budget。由于 pylopdf 当前接受 path 或完整 bytes，request body 必须完整缓冲。Cloudflare
的 128 MiB isolate budget 还包含 Python、JavaScript、WebAssembly linear memory 和
request buffer；周边代码需要更多空间时应进一步降低 file budget。

## 直接使用 Pyodide

在 Pyodide 0.28.3 开发环境中，用 `tools/build_pyodide.sh` 构建，并从 runtime 可访问的
URL 安装 runtime-tagged wheel：

```javascript
const pyodide = await loadPyodide();
await pyodide.loadPackage("micropip");
await pyodide.runPythonAsync(`
import micropip
await micropip.install(
    "https://example.invalid/pylopdf-0.11.0-"
    "cp310-abi3-pyodide_2025_0_wasm32.whl"
)
`);
```

URL 仅为示例。release artifact 为 PyPI 与 Cloudflare 使用 PEP 783
`pyemscripten_2025_0_wasm32` tag；Pyodide 0.28.3 的旧 `micropip` 不接受该公开 tag。
不要只修改 wheel 文件名而保留内部 `WHEEL` metadata。

browser 或 Worker 内没有受支持的 sdist fallback。构建 extension 需要固定的 Rust、
Emscripten、Pyodide cross environment 和 retag verifier。如果未来 ABI 没有匹配 wheel，
应将其视为不支持，而不是要求 runtime package installer 编译 sdist。

应用可用 `sys.platform == "emscripten"` 检测 runtime。path 指向 virtual filesystem，
而不是直接指向 browser 文件：

```python
import sys
import pylopdf

assert sys.platform == "emscripten"
with pylopdf.Document(stream=pdf_bytes, limits=pylopdf.DocumentLimits.web()) as doc:
    text = doc.get_page_text(0) if doc.page_count else ""
    output = doc.tobytes()
```

## 已验证 API

native/Wasm 共享 suite 目前覆盖：

- 不依赖 host filesystem 的 `bytes` 输入、页数、PDF 2.0、AES-256 加密输入；
- plain text、word、dict、搜索、document Markdown、嵌入日文、推断 vertical CJK、
  持续多栏顺序、纯图片页面和旋转页面；
- bordered 与保守 borderless table、Markdown 集成和 vector drawing 抽取；
- 空 document、Standard 14 与 subset-embedded OpenType text、textbox layout、
  rendering、`Pixmap`、serialization、virtual-filesystem save、merge、reorder、
  duplicate 和 select；
- `PdfError`、带 stable resource code 的 `LimitError`、`PasswordError`、
  `EncryptedDocumentError`、`DocumentClosedError`、`StalePageError`，以及 malformed
  input 后的 runtime 复用；
- `render_pages(workers=4)` 的输入顺序和与 `workers=1` 的 byte 一致性。

fixture 包括 PDF 2.0、嵌入 CJK 的日本政府文档、IRS Form 1040、旋转的美国参议院表格、
纯图片日文扫描件和生成的竖排文档。所有提交的 PDF 都小于 1 MiB，并在 corpus README
中记录可再分发 license。

相同 suite 会分别在 native wheel 与 Pyodide 中运行，并要求逻辑结果完全一致。它同时
检查明确结构、预期文本以及完整抽取与 Markdown hash。

## 功能与依赖关系

Wasm wheel 保持为一个 artifact，不拆成多个部分兼容的 variant。

| 功能 | Rust 组件 | Wasm 状态 |
|---|---|---|
| PDF 结构、编辑、加密 | lopdf | 包含 |
| 文本、表格、vector path | hayro syntax/interpreter 与 CMap | 包含 |
| PNG raster rendering | hayro、Vello、PNG encoding | 包含 |
| SVG rendering | hayro SVG backend | 包含 |
| 生成文本与 form appearance | krilla、HarfRust、read-fonts、UAX line breaking | 包含 |
| 图片与 JPEG 压缩 | Flate、zune-jpeg、jpeg-encoder | 包含 |
| 同一 document 并行 rendering | rayon | 仅 native；Wasm 串行执行 |
| PP-OCRv6 inference | RTen 与外部 model wheel | 仅 native；从 Wasm binary 移除 |
| 自动 CJK fallback 搜索 | 外部 CJK font wheel 与 host path | 不属于 Wasm 兼容契约 |

`OcrEngine()` 仍然存在以便确定性 capability 检测，但在 Emscripten 上会抛出
`OcrError`，提示在 Wasm 外完成 OCR 后调用 `Page.insert_ocr_text_layer()`。移除未使用的
RTen inference runtime 不会移除 PDF 抽取、rendering、生成或外部 OCR text 插入。

## 实测 deployment 范围

固定 CI artifact 是 3.834 MiB wheel 和 10.404 MiB 解压后 Wasm extension。测试的
Worker bundle 压缩后为 3.882 MiB，解压后为 10.844 MiB。因此它超过 Cloudflare
Workers Free 的 3 MB 压缩限制，但符合 paid plan 的 10 MB 压缩限制和共享的 64 MB
未压缩限制。pylopdf 支持 paid-plan deployment 路径，不发布功能削减的独立 distribution。

在 Node/Pyodide harness 中，首次打开并抽取 Form 1040 用时 116.267 ms；五次重复运行的
median 为 26.893 ms。Wasm linear memory 在安装后达到 40.375 MiB，在完整兼容与
resource suite 后达到 70.625 MiB。这些是可复现 CI trend，不是 Cloudflare request
latency 或 isolate resident-memory 测量。参见
[完整 size 与 startup report](https://github.com/yhay81/pylopdf/blob/main/bench/results/wasm-latest.md)。

## Runtime 限制

- 本 Emscripten build 没有 rayon worker pool。`render_pages(workers=...)` 接受相同参数，
  但会串行执行。
- `clip=` 会减少返回 pixel，但 hayro 内部仍会 rasterize 整页。
- 当前 renderer 会缓冲完整 raster output；大页面或高 DPI 即使源 PDF 很小也可能主导内存。
- native OCR 和独立 OCR model package 不在 WebAssembly 兼容契约内。
- 不覆盖外部 CJK fallback font 自动发现。已验证 embedded CJK，应用也可显式传入 font bytes。
- 当前 gate 证明 Cloudflare bundle 可构建，不代表已认证 production deploy 或特定 workload
  latency。

同一 matrix 会在 native 与 Wasm 中验证 `DocumentLimits`、`doc.complexity`、Web budget
内的 vector/scan 输入以及 stable file/page/text rejection code。定期 native Atheris
fuzzing 会加入更大的生成 hostile corpus。CPU deadline 由 host 负责。不要因 PDF 在
native Python 通过就假设 Wasm 有更大 memory budget；两端都应使用明确 policy。

## 支持与发布策略

每个发布 Wasm wheel 的 pylopdf release 都必须通过：

1. reproducible build 与 wheel metadata/import verifier；
2. native/Pyodide 共享逻辑兼容 suite；
3. untrusted input 拒绝与 resource trend 检查；
4. wheel、Wasm section、startup/workload 和 linear memory 测量；
5. 从本地 wheel 解析依赖并 dry-run Cloudflare Wrangler；
6. 最终创建 GitHub Release 前，从 PyPI artifact 重复相同解析和 dry-run。

runtime 更新会作为新的验证 matrix，而不会自动假设兼容。固定版本在对应 pylopdf minor
release 中受到支持；新的 Pyodide、PyEmscripten、Emscripten、`workers-py` 或 Wrangler
只有通过完整 gate 后才加入支持声明。测量值和 regression 会一起发布到
[`bench/results/wasm-latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/wasm-latest.md)。
