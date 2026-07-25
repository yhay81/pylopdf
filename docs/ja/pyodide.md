---
title: PyodideとCloudflare Workers
description: Cloudflare Python Workersを含むPyodide環境でpylopdfを導入・利用する方法。
---

# PyodideとCloudflare Workers

この対応を含む最初のreleaseから、pylopdfはPyodide 0.28.3で使用される
Python 3.13 ABI向けのPyEmscripten wheelを公開します。これは現在の
Cloudflare Python Workersが使用するruntime系列です。release wheelは、
公開前にPyodide上でbuildとsmoke testを行います。

## インストール

通常のPython依存関係と同じように宣言します。

```toml
[project]
dependencies = [
  "pylopdf>=0.11",
]
```

Cloudflareのbuildは、互換性のあるPyEmscripten wheelをPyPIから解決します。
Python Workerではsdistやnative Linux wheelを使用できません。

## bytesを直接使う

R2、request、Worker bindingなどから取得したbytesを直接渡します。永続的な
filesystemに依存しません。

```python
import pylopdf


def extract_pdf(pdf_bytes: bytes) -> str:
    with pylopdf.open(stream=pdf_bytes, max_decompressed_size=10 * 1024 * 1024) as document:
        if document.page_count > 200:
            raise ValueError("PDF exceeds the 200-page limit")
        return "\n".join(page.get_text() for page in document)
```

信頼できないPDFには処理前に上限を設定します。`max_decompressed_size`は
open時に各展開streamを制限します。application側でも入力bytes、ページ数、
抽出text量、Queue retry回数を制限してください。

## 対応runtime

| 構成要素 | 対応release target |
|---|---|
| Python | CPython 3.13 |
| Pyodide | 0.28.3 |
| Emscripten | 4.0.9 |
| Rust | nightly-2025-02-01と対応するWasm EH sysroot |
| 入力 | bytes/streamを推奨 |

wheelは編集、抽出、renderingについてnative buildと同じPython APIを維持します。
Cloudflare runtimeはpthreadを提供しないため、PDFのparseとrenderingは直列処理です。
`render_pages()`は自動的にworker数を1にし、`workers>1`を明示するとerrorにします。

軽量なWorker wheelでは、OpenType fontをsubset embeddingするPDF生成とoffline OCRを
対象外にします。textやformの生成時に`fontfile`または`fontbuffer`を渡すと`PdfError`、
`OcrEngine()`は`OcrError`を返し、どちらもruntime制約を説明します。Standard 14 font
による編集、既存PDFのopen、text/image抽出、custom embedded fontを使わないform、
save、renderingは利用できます。custom fontが必要なPDFの生成とOCRはupload前に
行ってください。native wheelは両機能を維持します。

Emscriptenのvirtual filesystemは一時的です。元PDFや派生fileはR2などのapplication
storageへ保存してください。

release CIで検証した版だけをsupport対象とします。新しいPyodide ABIは、
新しいwheelと互換性testが通った後にこの表へ追加します。

## wheelを再現する

RustとPythonを導入済みのLinuxまたはmacOSで実行します。

```bash
tools/install_pyodide_rust.sh
RUSTUP_TOOLCHAIN=nightly-2025-02-01 \
  RUSTC_WRAPPER=tools/pyodide_rustc_wrapper.sh \
  MATURIN_PEP517_ARGS="--no-default-features --ignore-rust-version -Zbuild-std=std,panic_unwind" \
  RUSTFLAGS="-C symbol-mangling-version=v0 -Zemscripten-wasm-eh" \
  CIBW_BUILD=cp313-pyodide_wasm32 \
  python -m cibuildwheel --platform pyodide --output-dir wheelhouse
```

buildは`pyproject.toml`に固定したPyodide版を使用します。install scriptは
downloadしたRust sysrootをSHA-256で検証します。CargoはWebAssemblyで安全な
symbol manglingによりstandard libraryをrebuildし、wrapperは互換性のための
feature gateをsysroot以外のcrateだけに適用します。

runtimeの制約はCloudflareの
[Python package support](https://developers.cloudflare.com/workers/languages/python/packages/)
とPyodideの
[package build guide](https://pyodide.org/en/0.28.0/development/building-packages.html)
も参照してください。
