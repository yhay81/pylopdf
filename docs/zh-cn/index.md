---
title: 迁移至 pyropdf
description: pylopdf 已归档；在 Python 中读取、渲染、编辑和签署 PDF 请迁移至 pyropdf。
hide:
  - navigation
---

<p class="living-eyebrow">Archived project · 最终版本 0.16.1</p>

# 从 pylopdf 迁移至 pyropdf { #migration-notice }

`pylopdf` 已归档，不再提供新功能、缺陷修复或安全更新。后续开发已全面转向在全新 Rust 基础之上重构的继任项目 **[pyropdf](https://github.com/yhay81/pyropdf)**。

<div class="living-actions">
  <a class="living-button living-button--primary" href="https://pyropdf.haya.works/">pyropdf 文档&nbsp; →</a>
  <a class="living-button" href="https://github.com/yhay81/pyropdf">GitHub (yhay81/pyropdf)</a>
  <a class="living-button" href="https://pypi.org/project/pyropdf/">PyPI (pyropdf)</a>
</div>

<div class="living-install" aria-label="安装命令">
  <span class="living-install__prompt" aria-hidden="true">$</span>
  <code tabindex="0">pip install pyropdf</code>
  <button
    class="living-install__copy"
    type="button"
    data-copy-command="pip install pyropdf"
    data-copied-label="已复制"
    aria-label="复制安装命令"
  ><span data-copy-label>复制</span></button>
</div>

## 为什么由 pyropdf 取代 pylopdf { #why }

`pylopdf` 同时维护了三套对象模型——`lopdf` 的可编辑对象图、`hayro` 对序列化字节的解析结果以及 `krilla` 生成的 PDF——并在事后进行协调。每次编辑都会丢弃 `hayro` 的快照并重新序列化整份文档，事务需要克隆整个对象图，且 `lopdf` 使用 32 位浮点数存储实数。

`pyropdf` 延续了 `pylopdf` 的理念与 Python API 设计，并在 Rust 中基于**单一版本化对象存储**重新构建（详见[基础设计与实测数据](https://github.com/yhay81/pyropdf/blob/main/DESIGN.md)）：

- **编辑开销仅与修改内容成正比。** 所有引擎直接从同一存储读取当前版本，无需在编辑后重新序列化整份文档。
- **原生版本化与撤销重做。** 每次编辑都会生成不可变的新版本；失败的事务无需复制文档即可回滚，并可通过 `doc.history` 撤销与重做。
- **无损保存与变更报告。** 未修改的对象保留原始字节；`incremental=True` 增量保存仅追加变更对象以保持已有数字签名有效；`doc.changes` 可在保存前报告将被破坏的签名或 PDF/A、PDF/UA 合规声明。
- **更完整的 PDF 能力。** 支持数字签名与验签、内容清理、色彩转换、画布上的可变字体与彩色字体，以及由 Arlington PDF Model 生成的类型化对象。

## 如何迁移 { #migrate }

替换安装包并更新 `import`：

```bash
pip uninstall pylopdf
pip install pyropdf
```

```python
import pyropdf

with pyropdf.open("input.pdf") as doc:
    page = doc.pages[0]
    text = page.text()
    hits = page.search("合计")
    page.pixmap(dpi=150).save("page.png")

    with doc.transaction("annotate"):
        page.annotations.add(pyropdf.Highlight(hits))
    doc.save("output.pdf", incremental=True)
```

- **从 `pylopdf` 0.16 迁移：** 将 `import pylopdf` 改为 `import pyropdf` 后，绝大多数文档、页面、提取、渲染、画布、批注、表单、大纲、附件与图层接口均可直接使用。请核对下方差异表。
- **从 `pylopdf` 0.15 或 `pymupdf` 迁移：** 如需先自动转换旧接口，可使用 `pylopdf[migrate]==0.16.1` 运行 `python -m pylopdf.migrate src/` 升级到 0.16 风格，再将 `import pylopdf` 替换为 `import pyropdf`。

## 与 pylopdf 0.16 的主要差异 { #differences }

| 方面 | `pylopdf` 0.16 | `pyropdf` |
|---|---|---|
| 导入与警告类 | `import pylopdf`、`PylopdfWarning` | `import pyropdf`、`PyropdfWarning` |
| 可选依赖（extras） | `ocr`, `jp`, `ko`, `zh-cn`, `zh-tw`, `ar`, `he`, `hi`, `th`, `migrate` | `ocr`, `jp`, `ko`, `zh-cn`, `zh-tw`（直接复用相同的数据包）；其他文字字体请使用 `pyropdf.Font.from_file(...)` 加载 |
| 页面框 | `page.boxes.media`, `.crop`, `.bleed`, `.trim`, `.art`（PDF 用户空间） | `page.media_box`, `page.crop_box`, `page.bleed_box`, `page.trim_box`, `page.art_box`（显示坐标系） |
| 创建新文档 | `pylopdf.Document()` 或 `pylopdf.create()` | 统一使用 `doc = pyropdf.Document()`、`doc.pages.new(...)` 或 `doc.pages.new_from_image(...)` 与 `page.draw()` |
| `doc.save()` 默认行为 | 默认压缩流并清理未使用对象（`compress=True, prune=True`） | 默认保留未修改对象（`compress=False, prune=False`）；需要压缩体积时传入 `compress=True, prune=True`，增量保存时传入 `incremental=True` |
| 不受信任的输入 | 嵌套扫描 + `DocumentLimits.web()` | 在上游有界构造发布前，请参阅[状态与安全说明](https://pyropdf.haya.works/status/)并在受限工作进程中处理不受信任的 PDF（[限制与错误](https://pyropdf.haya.works/limits/#reading-a-pdf-in-a-worker)） |
| WebAssembly | 发布包提供 Pyodide 0.28.3 wheel | 在 [pyropdf 路线图](https://github.com/yhay81/pyropdf/issues/1)中跟进；已发布的 `pylopdf` 0.16 wheel 仍可从 PyPI 与 GitHub Releases 安装 |

## 相关链接 { #links }

- **pyropdf 文档：** <https://pyropdf.haya.works/>（[`llms.txt`](https://pyropdf.haya.works/llms.txt)）
- **pyropdf 仓库与 Issue：** <https://github.com/yhay81/pyropdf>
- **pyropdf (PyPI)：** <https://pypi.org/project/pyropdf/>
- **pyropdf 基础设计文档：** <https://github.com/yhay81/pyropdf/blob/main/DESIGN.md>
- **已归档的 pylopdf 仓库与发布记录：** <https://github.com/yhay81/pylopdf> · [PyPI (`pylopdf`)](https://pypi.org/project/pylopdf/) · [更新日志](https://github.com/yhay81/pylopdf/blob/main/CHANGELOG.md)
