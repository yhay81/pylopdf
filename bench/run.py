"""pylopdf と主要 PDF ライブラリの再現可能ベンチマーク。

実行:

    uv sync --all-extras --group bench
    uv run python bench/run.py

同一コーパス（tests/assets/real_world。再配布可能なもののみ）・同一タスクで
各ライブラリの中央値時間を測り、勝ち負けの両方をそのまま
bench/results/latest.md へ書き出す。速度だけでなく抽出文字数と
pymupdf との類似度（正確さの代理指標）も併記する。

環境変数 BENCH_REPEATS（既定 5）で反復回数を変えられる。
"""

from __future__ import annotations

import difflib
import io
import os
import platform
import re
import statistics
import time
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING

import pylopdf

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests" / "assets" / "real_world"
RESULTS = Path(__file__).resolve().parent / "results"
REPEATS = int(os.environ.get("BENCH_REPEATS", "5"))


# --- 各ライブラリのアダプタ（無ければ None のまま = 表で n/a になる） ---

try:
    import fitz  # pymupdf

    def _pymupdf_extract(data: bytes) -> str:
        with fitz.open(stream=data, filetype="pdf") as doc:
            return "".join(page.get_text() for page in doc)

    def _pymupdf_merge(docs: list[bytes]) -> bytes:
        out = fitz.open()
        for data in docs:
            with fitz.open(stream=data, filetype="pdf") as src:
                out.insert_pdf(src)
        return out.tobytes()

    def _pymupdf_render(data: bytes) -> bytes:
        with fitz.open(stream=data, filetype="pdf") as doc:
            return doc[0].get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")
except Exception:  # pragma: no cover - 未インストール環境
    _pymupdf_extract = _pymupdf_merge = _pymupdf_render = None  # type: ignore[assignment]

try:
    from pypdf import PdfReader, PdfWriter

    def _pypdf_extract(data: bytes) -> str:
        reader = PdfReader(io.BytesIO(data))
        return "".join(page.extract_text() or "" for page in reader.pages)

    def _pypdf_merge(docs: list[bytes]) -> bytes:
        writer = PdfWriter()
        for data in docs:
            writer.append(io.BytesIO(data))
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()
except Exception:  # pragma: no cover
    _pypdf_extract = _pypdf_merge = None  # type: ignore[assignment]

try:
    import pdfplumber

    def _pdfplumber_extract(data: bytes) -> str:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "".join(page.extract_text() or "" for page in pdf.pages)
except Exception:  # pragma: no cover
    _pdfplumber_extract = None  # type: ignore[assignment]


def _pylopdf_extract(data: bytes) -> str:
    with pylopdf.open(stream=data) as doc:
        return "".join(doc.get_page_text(i) for i in range(doc.page_count))


def _pylopdf_merge(docs: list[bytes]) -> bytes:
    merged = pylopdf.Document()
    for data in docs:
        with pylopdf.open(stream=data) as src:
            merged.insert_pdf(src)
    return merged.tobytes()


def _pylopdf_render(data: bytes) -> bytes:
    with pylopdf.open(stream=data) as doc:
        return doc.render_page(0, scale=2)


def _median_ms(func: Callable[[], object]) -> float | None:
    """ウォームアップ 1 回 + REPEATS 回実行の中央値（ミリ秒）。失敗は None。"""
    try:
        func()  # ウォームアップ（キャッシュ・遅延 import の影響を除く）
        times = []
        for _ in range(REPEATS):
            start = time.perf_counter()
            func()
            times.append((time.perf_counter() - start) * 1000)
        return statistics.median(times)
    except Exception:
        return None


def _fmt(value: float | None) -> str:
    return "err/n-a" if value is None else f"{value:.1f}"


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def main() -> None:
    """コーパス全体でベンチマークを実行し、Markdown レポートを書き出す。"""
    files = sorted(CORPUS.glob("*.pdf"))
    if not files:
        msg = f"コーパスが見つかりません: {CORPUS}"
        raise SystemExit(msg)
    corpus = {f.name: f.read_bytes() for f in files}

    versions = {}
    for dist in ("pylopdf", "pymupdf", "pypdf", "pdfplumber"):
        try:
            versions[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            versions[dist] = "n/a"

    lines: list[str] = []
    lines.append("# pylopdf ベンチマーク結果")
    lines.append("")
    lines.append(f"- 実行日時: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"- 環境: {platform.platform()} / Python {platform.python_version()} / CPU {platform.processor()}")
    lines.append("- バージョン: " + ", ".join(f"{k} {v}" for k, v in versions.items()))
    lines.append(f"- 反復: 各タスク ウォームアップ 1 回 + {REPEATS} 回の中央値（ミリ秒。小さいほど速い）")
    lines.append("- コーパス: tests/assets/real_world（出典・ライセンスは同ディレクトリの README）")
    lines.append("- 再現方法: `uv sync --all-extras --group bench && uv run python bench/run.py`")
    lines.append("")

    # --- テキスト抽出（全ページ） ---
    lines.append("## テキスト抽出（全ページ、ms）")
    lines.append("")
    lines.append("| ファイル | pylopdf | pymupdf | pypdf | pdfplumber |")
    lines.append("|---|---|---|---|---|")
    for name, data in corpus.items():
        row = [
            _median_ms(lambda d=data: _pylopdf_extract(d)),
            _median_ms(lambda d=data: _pymupdf_extract(d)) if _pymupdf_extract else None,
            _median_ms(lambda d=data: _pypdf_extract(d)) if _pypdf_extract else None,
            _median_ms(lambda d=data: _pdfplumber_extract(d)) if _pdfplumber_extract else None,
        ]
        lines.append(f"| {name} | " + " | ".join(_fmt(v) for v in row) + " |")
        print(f"extract {name}: done")
    lines.append("")

    # --- 抽出の正確さの代理指標（文字数と pymupdf との類似度） ---
    lines.append("## 抽出内容の突き合わせ（正確さの代理指標）")
    lines.append("")
    lines.append("| ファイル | pylopdf 文字数 | pymupdf 文字数 | 類似度（空白正規化後） |")
    lines.append("|---|---|---|---|")
    for name, data in corpus.items():
        ours = _normalize(_pylopdf_extract(data))
        if _pymupdf_extract:
            try:
                theirs = _normalize(_pymupdf_extract(data))
                ratio = difflib.SequenceMatcher(None, ours, theirs).ratio()
                lines.append(f"| {name} | {len(ours)} | {len(theirs)} | {ratio:.3f} |")
            except Exception:
                lines.append(f"| {name} | {len(ours)} | err | - |")
        else:
            lines.append(f"| {name} | {len(ours)} | n/a | - |")
    lines.append("")
    lines.append("類似度の読み方: 1.0 に近いほど pymupdf と同じテキストが取れている。")
    lines.append("低い行はフォーム（f1040）やスキャン + OCR 層（patent）で、文字数がほぼ同数の")
    lines.append("まま読み順・空白の流儀が違うことを示す（どちらが正とは言えない）。")
    lines.append("0 文字の行は画像のみでテキスト層が無い PDF（0 はどちらも正しい）。")
    lines.append("")

    # --- 結合（コーパス全部を 1 文書へ） ---
    lines.append("## 結合（コーパス全ファイルを 1 文書へ、ms）")
    lines.append("")
    docs = list(corpus.values())
    lines.append("| タスク | pylopdf | pymupdf | pypdf |")
    lines.append("|---|---|---|---|")
    merge_row = [
        _median_ms(lambda: _pylopdf_merge(docs)),
        _median_ms(lambda: _pymupdf_merge(docs)) if _pymupdf_merge else None,
        _median_ms(lambda: _pypdf_merge(docs)) if _pypdf_merge else None,
    ]
    lines.append(f"| merge x{len(docs)} | " + " | ".join(_fmt(v) for v in merge_row) + " |")
    print("merge: done")
    lines.append("")

    # --- レンダリング（1 ページ目を 2 倍スケール PNG） ---
    lines.append("## レンダリング（1 ページ目 → 2x PNG、ms）")
    lines.append("")
    lines.append("| ファイル | pylopdf | pymupdf |")
    lines.append("|---|---|---|")
    for name, data in corpus.items():
        row = [
            _median_ms(lambda d=data: _pylopdf_render(d)),
            _median_ms(lambda d=data: _pymupdf_render(d)) if _pymupdf_render else None,
        ]
        lines.append(f"| {name} | " + " | ".join(_fmt(v) for v in row) + " |")
        print(f"render {name}: done")
    lines.append("")
    lines.append("速い・遅いの両方をそのまま掲載する方針です。数値は環境依存のため、")
    lines.append("引用時は必ず上記の環境情報とセットで扱ってください。")
    lines.append("")

    RESULTS.mkdir(exist_ok=True)
    out_path = RESULTS / "latest.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
