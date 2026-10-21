---
title: 语言政策
description: pylopdf文档的重要语言、原文与译文的分工以及维护规则。
---

# 语言政策

## 重要语言 { #important-languages }

| 定位 | 语言 | 作用 |
|---|---|---|
| 原文 | English | 内容正本，所有规格变更首先在英文中完成 |
| 译文 | 日本語 | 维护者的主要语言，与英文同步完整更新 |
| 译文 | 简体中文 | 与CJK PDF、字体后备和OCR使用场景直接匹配 |
| 译文 | 한국어 | 对CJK PDF与Python用户同样重要 |

只有完整翻译并通过严格构建的语言才会出现在语言切换器中。

## 维护规则 { #translation-principles }

- API名称、类名、参数名与代码保持英文。
- 技术含义与自然表达优先于逐字翻译。
- 各语言使用相同的标题ID，确保链接稳定。
- 版本、性能数字与安全建议必须在同一变更中同步。
- 新内容先修改英文正本，再更新所有译文。

完整的选择依据、晋级条件与资料来源见仓库中的[`LANGUAGES.md`](https://github.com/yhay81/pylopdf/blob/main/LANGUAGES.md)。
