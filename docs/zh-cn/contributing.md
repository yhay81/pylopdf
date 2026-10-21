---
title: 贡献与支持
description: 在哪里提问和报告bug、如何为pylopdf做贡献，以及项目的许可证与致谢。
---

# 贡献与支持

## 获取帮助与报告bug { #support }

- **问题、bug和功能需求**：请在[GitHub](https://github.com/yhay81/pylopdf/issues/new/choose)上创建issue。请先搜索[已有的issue](https://github.com/yhay81/pylopdf/issues)。
- **安全漏洞**：请私密报告；参见[安全](security.md#report-a-vulnerability)。

一份有用的bug报告应包括pylopdf、Python和操作系统的版本，一段能复现问题的简短脚本，以及你预期的结果。如果问题取决于某个特定的PDF，请附上一个小文件或能生成它的脚本。PDF中经常包含个人数据、签名和隐藏的元数据：只分享你有权再分发的文件，并删除其中的机密内容。

## 参与贡献 { #contribute }

欢迎各种贡献：bug修复、pylopdf处理得不好的真实PDF、文档改进以及翻译。如果是较大的功能，请先创建issue，以便在编写代码之前就设计达成一致。

从源码构建需要Rust工具链和[uv](https://docs.astral.sh/uv/)：

```bash
git clone https://github.com/yhay81/pylopdf.git
cd pylopdf
uv sync
uv run pytest
```

[CONTRIBUTING.md](https://github.com/yhay81/pylopdf/blob/main/CONTRIBUTING.md)是完整的开发指南：需要运行哪些检查、如何添加测试用PDF，以及pull request如何审查。所有参与者都应遵守[行为准则](https://github.com/yhay81/pylopdf/blob/main/CODE_OF_CONDUCT.md)。

### 文档与翻译 { #documentation }

本站使用[Zensical](https://zensical.org/)，由[`docs/`](https://github.com/yhay81/pylopdf/tree/main/docs)中的Markdown文件构建。每个页面都有一个编辑按钮，可在GitHub上打开其源文件。在本地预览修改：

```bash
uv sync --group docs
uv run zensical serve -f mkdocs.yml
```

英文版是基准版本。日文、简体中文和韩文译本与英文版同步维护，使用相同的页面名称和章节锚点，因此链接在每种语言中都有效。

## 计划与发布 { #plans }

- [路线图](https://github.com/yhay81/pylopdf/blob/main/ROADMAP.md)：计划做什么，以及按什么顺序进行
- [更新日志](https://github.com/yhay81/pylopdf/blob/main/CHANGELOG.md)：每个版本有哪些变化
- [Releases](https://github.com/yhay81/pylopdf/releases)和[PyPI](https://pypi.org/project/pylopdf/)
- [API稳定性](stability.md)：v1.0之前和之后可能发生哪些变化

## 许可证与致谢 { #license }

pylopdf以[MIT许可证](https://github.com/yhay81/pylopdf/blob/main/LICENSE)发布。

它基于以下项目构建：用于读取和编辑PDF的[lopdf](https://github.com/J-F-Liu/lopdf)，用于渲染和文本提取的[hayro](https://github.com/LaurenzV/hayro)，用于写入文本的[krilla](https://github.com/LaurenzV/krilla)和[HarfRust](https://github.com/harfbuzz/harfrust)，以及用于OCR的[RTen](https://github.com/robertknight/rten)。其API沿用[pymupdf](https://pymupdf.readthedocs.io/)。[NOTICE.md](https://github.com/yhay81/pylopdf/blob/main/NOTICE.md)列出了所有内置第三方代码的许可证。可选的字体包包含采用SIL Open Font License的Noto字体，OCR模型包包含采用Apache License 2.0的PP-OCRv6模型。
