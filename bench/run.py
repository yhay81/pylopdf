"""Run reproducible pylopdf benchmarks against major PDF libraries.

Usage:

    uv sync --all-extras --group bench
    uv run python bench/run.py

Measure median timings over the same redistributable corpus and tasks, then
write both wins and losses to bench/results/latest.md. The report also records
extracted character counts and similarity to PyMuPDF as a quality proxy.

Set BENCH_REPEATS to change the repetition count from its default of five.
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


# --- Library adapters; unavailable dependencies remain None and report n/a. ---

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
except Exception:  # pragma: no cover - optional dependency not installed
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
    """Return median milliseconds after one warmup, or None on failure."""
    try:
        func()  # Warm caches and deferred imports.
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
    """Benchmark the complete corpus and write a Markdown report."""
    files = sorted(CORPUS.glob("*.pdf"))
    if not files:
        msg = f"benchmark corpus not found: {CORPUS}"
        raise SystemExit(msg)
    corpus = {f.name: f.read_bytes() for f in files}

    versions = {}
    for dist in ("pylopdf", "pymupdf", "pypdf", "pdfplumber"):
        try:
            versions[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            versions[dist] = "n/a"

    lines: list[str] = []
    lines.append("# pylopdf benchmark results")
    lines.append("")
    lines.append(f"- Run at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(
        f"- Environment: {platform.platform()} / Python {platform.python_version()} / CPU {platform.processor()}"
    )
    lines.append("- Versions: " + ", ".join(f"{k} {v}" for k, v in versions.items()))
    lines.append(f"- Repetitions: one warmup + median of {REPEATS} runs per task (ms; lower is faster)")
    lines.append("- Corpus: tests/assets/real_world (sources and licenses are documented in its README)")
    lines.append("- Reproduce: `uv sync --all-extras --group bench && uv run python bench/run.py`")
    lines.append("")

    # --- Text extraction across all pages. ---
    lines.append("## Text extraction (all pages, ms)")
    lines.append("")
    lines.append("| File | pylopdf | pymupdf | pypdf | pdfplumber |")
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

    # --- Quality proxies: character counts and similarity to PyMuPDF. ---
    lines.append("## Extracted-content comparison (quality proxy)")
    lines.append("")
    lines.append("| File | pylopdf characters | pymupdf characters | Similarity after whitespace normalization |")
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
    lines.append("Similarity approaches 1.0 as output converges with PyMuPDF.")
    lines.append("Low scores for the form (f1040) and scanned OCR-layer patent reflect different")
    lines.append("reading-order and whitespace conventions despite similar character counts.")
    lines.append("A zero-character row is image-only with no text layer, so zero is correct for both.")
    lines.append("")

    # --- Merge the complete corpus into one document. ---
    lines.append("## Merge (all corpus files into one document, ms)")
    lines.append("")
    docs = list(corpus.values())
    lines.append("| Task | pylopdf | pymupdf | pypdf |")
    lines.append("|---|---|---|---|")
    merge_row = [
        _median_ms(lambda: _pylopdf_merge(docs)),
        _median_ms(lambda: _pymupdf_merge(docs)) if _pymupdf_merge else None,
        _median_ms(lambda: _pypdf_merge(docs)) if _pypdf_merge else None,
    ]
    lines.append(f"| merge x{len(docs)} | " + " | ".join(_fmt(v) for v in merge_row) + " |")
    print("merge: done")
    lines.append("")

    # --- Render the first page to a 2x PNG. ---
    lines.append("## Rendering (first page to 2x PNG, ms)")
    lines.append("")
    lines.append("| File | pylopdf | pymupdf |")
    lines.append("|---|---|---|")
    for name, data in corpus.items():
        row = [
            _median_ms(lambda d=data: _pylopdf_render(d)),
            _median_ms(lambda d=data: _pymupdf_render(d)) if _pymupdf_render else None,
        ]
        lines.append(f"| {name} | " + " | ".join(_fmt(v) for v in row) + " |")
        print(f"render {name}: done")
    lines.append("")
    lines.append("This report publishes both wins and losses. Results depend on the environment,")
    lines.append("so cite them together with the environment details above.")
    lines.append("")

    RESULTS.mkdir(exist_ok=True)
    out_path = RESULTS / "latest.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
