---
title: 安全
description: pylopdf支持版本、私密漏洞报告方式，以及处理不受信任PDF时的安全建议。
---

# 安全

只有PyPI上的最新版本会获得安全修复。

## 报告漏洞 { #report-a-vulnerability }

请通过[GitHub Security Advisories](https://github.com/yhay81/pylopdf/security/advisories/new)
私密报告漏洞，不要创建公开Issue。我们会争取在一周内首次响应。

## 处理不受信任的PDF { #untrusted-pdfs }

pylopdf由Rust编写且没有运行时依赖，但解析恶意PDF输入仍然存在固有风险。

!!! warning "明确设置解压预算"
    请向`pylopdf.open()`传入`max_decompressed_size=`。pylopdf会在返回Document前
    检查每个可读取的流，包括原本会由渲染器延迟解压的页面内容。

```python
import pylopdf

with pylopdf.open("upload.pdf", max_decompressed_size=128 * 1024 * 1024) as doc:
    preview = doc[0].get_pixmap(dpi=144)
```

- 图像流按解码后的RGBA大小限制。
- 启用限制后，无法安全计算输出上限的过滤器链会被拒绝。
- 每页渲染上限为6400万像素。
- 嵌入JavaScript在设计上不受支持，也绝不会执行。
- 批量处理不受信任的文件时，尽量在sandbox或container中运行。

## 依赖审计 { #dependency-auditing }

CI会在每次push时运行`cargo audit`，使用RustSec漏洞数据库审计Rust依赖树。

本政策在仓库中的正本为
[`SECURITY.md`](https://github.com/yhay81/pylopdf/blob/main/SECURITY.md)。
