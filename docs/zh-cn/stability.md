---
title: API稳定性
description: pylopdf的公共API边界、语义化版本保证、弃用生命周期与兼容性审查流程。
---

# API稳定性

pylopdf遵循[Semantic Versioning 2.0.0](https://semver.org/)。本页说明这对一个结果还
依赖PDF生成器、字体、renderer和受支持runtime的Python library意味着什么。

## 当前状态 { #current-status }

0.12 API是**候选baseline**，并不是v1.0兼容性承诺。项目会先通过实际使用验证它，同时
继续加法式改进和经过审查的修正。不过，从现在起每个公共surface变化都会被检测和审查，
使最终v1.0边界来自明确决策，而不是偶然形成。

[v1.0之后](#after-v1)的保证从v1.0开始。在此之前，release note仍会标明不兼容变化和
迁移路径；项目不会以版本低于1.0为理由进行无声破坏。

## 公共API边界 { #public-api-boundary }

受支持的公共API包括：

- `pylopdf.__all__`导出的名称，以及`pylopdf.__version__`；
- 这些class中有文档的public member；
- callable parameter的名称、类型、default和有文档的return contract；
- 有文档的常量和enum值；
- TypedDict的必需/可选key、NamedTuple field和public type alias；
- 有文档的异常层次，以及`LimitError.code`等机器可读属性。

以下内容属于private：以`_`开头的名称、`pylopdf.pylopdf_core`、Rust实现细节、
object的`repr`、异常message的精确文本、warning顺序和未记录属性。从实现module
import一个public object并不会使该module path成为公共API；请从`pylopdf` import。

`save()`生成的PDF byte以及PNG/SVG的精确serialization不是逐byte稳定格式。contract是
有文档的视觉、结构和提取语义。

## v1.0之后 { #after-v1 }

对于stable release：

- **major** release可以删除或不兼容地修改public API；
- **minor** release增加向后兼容的API和行为；
- **patch** release修复缺陷，不会故意修改public API。

删除或重命名public symbol/member、把parameter改为必需、不兼容地修改
positional/keyword接受方式、删除mapping key、缩小可接受输入、修改有文档的常量，或
破坏public异常继承，都需要major release。

增加可选keyword parameter、新symbol、新method、可接受输入的新enum或Literal选项，
以及可选结果key通常属于加法式变化。类型contract与runtime object接受相同的兼容性
审查：改变TypedDict key的必需/可选状态，或不兼容地修改value type，并不只是“typing
变化”。

## 弃用生命周期 { #deprecation-lifecycle }

v1.0之后，计划删除的public API通常会：

1. 在文档中标为弃用，同时给出替代方案和最早删除release；
2. 在可行时发出`DeprecationWarning`；
3. 保留至少两个minor release并且至少六个月；
4. 只在major release中删除。

`DeprecationWarning`用于开发者迁移。`PylopdfWarning`继续用于PDF解释过程中的运行
warning，不作为弃用channel。

security、legal或upstream runtime紧急问题可能需要缩短流程。此类例外会在changelog
和release note中醒目标明，并提供影响最小的迁移方案。

## 行为与data兼容性 { #behavior-and-data }

当旧行为不正确时，bug fix可能改变输出。例如损坏PDF的恢复、阅读顺序、glyph geometry、
table解释、颜色转换和renderer差异。如果变化使行为更接近已有文档contract，就不要求
major release，但会报告重大影响。

resource limit可能拒绝早期版本曾尝试处理的攻击性或异常昂贵输入。需要机器决策时使用
稳定异常类型和error code；面向人的message文本不是API。

支持范围由每个release记录的Python version、platform、ABI和WebAssembly runtime
matrix定义。runtime在upstream EOL后，或继续支持会阻碍security/correctness修复时，
可以在minor release中移除；项目会公布原因和迁移窗口。Pyodide兼容性按pylopdf minor
line固定并测试，不会跨runtime升级作推测。

## 兼容性审查 { #compatibility-review }

[`api/public-api.json`](https://github.com/yhay81/pylopdf/blob/main/api/public-api.json)
记录经过审查的0.11候选surface。所有native Python lane都会比较该文件，检测export、
signature、mapping key、type alias、enum/常量值、public member和异常继承的变化。

```console
uv run python tools/check_api_surface.py
```

有意的变化需要先审查runtime、typing、documentation和SemVer影响，再刷新snapshot：

```console
uv run python tools/check_api_surface.py --update
```

snapshot是review gate，而不是自动兼容性判决。每个有意变化仍需test、四种受支持语言的
documentation和changelog entry。
