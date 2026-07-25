# WebAssembly 兼容性

pylopdf 为 Pyodide 0.28.3 和 Cloudflare Python Workers 使用的 Python 3.13 ABI
构建静态 PyEmscripten wheel。它包含与原生 wheel 相同的 Rust PDF 引擎，不依赖
JavaScript PDF 实现或 wasm-bindgen shim。

!!! note "发布状态"

    WebAssembly 构建已进入 `main`，将从 v0.10.0 之后的包版本开始发布。
    v0.10.0 本身仅包含原生 wheel。

## 分发状态

| 环境 | 状态 | 说明 |
|---|---|---|
| Cloudflare Python Workers | 有发布门禁 | CI 使用 `workers-py` 1.15.0 解析 PEP 783 wheel，并用 Wrangler 4.114.0 dry-run bundle。标签发布还会从 PyPI 重新解析，成功后才创建 GitHub Release。 |
| Node.js 中的 Pyodide 0.28.3 | 有兼容性门禁 | CI 将本地构建的旧标签 wheel 安装到精确固定的 runtime，并运行完整共享兼容性套件。 |
| 从 PyPI 直接安装到浏览器 | 暂不支持 | Pyodide 0.28.3 的 `micropip` 早于 PyPI 要求的 PEP 783 `pyemscripten_*` 标签。二进制兼容，但稳定的公开安装流程仍需更新的前端工具。 |
| 其他 Pyodide / Python-Wasm 版本 | 未测试 | 扩大 wheel 标签或支持声明前，必须先验证平台与 ABI。 |

发布产物使用 `cp310-abi3-pyemscripten_2025_0_wasm32`。builder 先以 runtime 原生的
`pyodide_2025_0_wasm32` 标签执行同一二进制，再确定性地改为 PEP 783 标签。
只有 PyEmscripten 标签产物会进入 PyPI、provenance attestation 和 release SBOM。

## 已测试的 API

原生/Wasm 共享套件目前覆盖：

- 不依赖 host filesystem 的 `bytes` 输入、页数、PDF 2.0 与 AES-256 加密输入；
- plain text、words、dict、搜索、文档 Markdown、嵌入日文、竖排 CJK 推断、
  多栏顺序、纯图像页面与旋转页面；
- 有边框表格、保守的无边框表格、Markdown 集成与 vector drawing 提取；
- 空文档、Standard 14 与子集嵌入 OpenType 文本、textbox、渲染、`Pixmap`、
  serialization、虚拟文件系统保存、merge、重排、复制与 select；
- `PdfError`、带稳定资源code的`LimitError`、`PasswordError`、`EncryptedDocumentError`、
  `DocumentClosedError`、`StalePageError`，以及错误输入后的 runtime 复用；
- `render_pages(workers=4)` 的输入顺序，以及与 `workers=1` 的逐字节一致性。

fixture 包含 PDF 2.0、嵌入 CJK 的日本政府文档、IRS Form 1040、旋转 90 度的
美国参议院表格、纯图像日文扫描页和合成竖排文档。提交的 PDF 均小于 1 MB，
其可再分发许可记录在 corpus README 中。

套件会分别在原生 wheel 与 Pyodide 中运行，并要求逻辑结果完全一致。除明确检查
关键文字与结构外，还比较完整提取文本和 Markdown 的 hash。

## runtime 差异与限制

- 此 Emscripten build 没有 rayon worker pool。`render_pages(workers=...)`
  接受普通参数，但串行执行；原生 build 保留有界 rayon 并行。
- path 指向 runtime 虚拟文件系统，而不是浏览器或 Worker host。应用边界应优先使用
  `Document(stream=data)` 和 `tobytes()`。
- 渲染上限不变。`clip=` 会减少返回像素，但 hayro 仍在内部光栅化完整页面。
- native OCR 与独立分发的 OCR model package 不在当前 WebAssembly 兼容契约内。
- 尚未保证自动发现外部 CJK fallback font。已测试嵌入 CJK，也可显式传入字体 bytes。
- 当前门禁验证 Cloudflare bundle 构建，不代表已在认证的生产环境 live deploy。

同一matrix现会在native与Wasm中验证`DocumentLimits`、`doc.complexity`、Web
预算内的代表性vector/scan，以及file/page/text拒绝code。定期native Atheris
fuzzing还会加入更大的生成hostile corpus。不能因为文档在native Python中通过，
就推断Wasm拥有更大的memory budget；两个runtime都应使用明确policy。
