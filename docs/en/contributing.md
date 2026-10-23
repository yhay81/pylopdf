---
title: Contributing and support
description: Where to ask questions and report bugs, how to contribute to pylopdf, and its license and acknowledgements.
---

# Contributing and support

## Get help and report bugs { #support }

- **Questions, bugs, and feature requests:** open an issue on
  [GitHub](https://github.com/yhay81/pylopdf/issues/new/choose). Search the
  [existing issues](https://github.com/yhay81/pylopdf/issues) first.
- **Security vulnerabilities:** report them privately; see
  [Security](security.md#report-a-vulnerability).

A useful bug report includes the pylopdf, Python, and operating system
versions, a short script that reproduces the problem, and what you expected
to happen. If the problem depends on a particular PDF, attach a small one or a
script that creates it. PDFs often contain personal data, signatures, and
hidden metadata: share only files you are allowed to redistribute, with
confidential content removed.

## Contribute { #contribute }

Bug fixes, real-world PDFs that pylopdf handles badly, documentation
improvements, and translations are all welcome. For a large feature, open an
issue first so the design can be agreed before you write the code.

Building from source needs a Rust toolchain and
[uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/yhay81/pylopdf.git
cd pylopdf
uv sync
uv run pytest
```

[CONTRIBUTING.md](https://github.com/yhay81/pylopdf/blob/main/CONTRIBUTING.md)
has the complete development guide: checks to run, how to add test PDFs, and
how pull requests are reviewed. Everyone taking part is expected to follow the
[Code of Conduct](https://github.com/yhay81/pylopdf/blob/main/CODE_OF_CONDUCT.md).

### Documentation and translations { #documentation }

This site is built with [Zensical](https://zensical.org/) from the Markdown
files in [`docs/`](https://github.com/yhay81/pylopdf/tree/main/docs). Every
page has an edit button that opens its source on GitHub. To preview your
changes locally:

```bash
uv sync --group docs
uv run zensical serve -f mkdocs.yml
```

English is the reference version. The Japanese, Simplified Chinese, and Korean
translations are maintained alongside it and use the same page names and
section anchors, so links work in every language.

## Plans and releases { #plans }

- [Roadmap](https://github.com/yhay81/pylopdf/blob/main/ROADMAP.md): what is
  planned and in which order
- [Changelog](https://github.com/yhay81/pylopdf/blob/main/CHANGELOG.md): what
  changed in each release
- [Releases](https://github.com/yhay81/pylopdf/releases) and
  [PyPI](https://pypi.org/project/pylopdf/)
- [API stability](stability.md): what may change before and after v1.0

## License and acknowledgements { #license }

pylopdf is released under the
[MIT License](https://github.com/yhay81/pylopdf/blob/main/LICENSE).

It is built on [lopdf](https://github.com/J-F-Liu/lopdf) for reading and
editing PDFs, [hayro](https://github.com/LaurenzV/hayro) for rendering and
text extraction, [krilla](https://github.com/LaurenzV/krilla) and
[HarfRust](https://github.com/harfbuzz/harfrust) for writing text, and
[RTen](https://github.com/robertknight/rten) for OCR. It aims to give
programs complete control of every feature of PDF that ISO 32000-2 defines,
covering at least what [pymupdf](https://pymupdf.readthedocs.io/) can do with
PDF files, through an API of its own.
[NOTICE.md](https://github.com/yhay81/pylopdf/blob/main/NOTICE.md) lists the
licenses of all bundled third-party code. The optional font packages contain
Noto fonts under the SIL Open Font License, and the OCR model package contains
PP-OCRv6 models under the Apache License 2.0.
