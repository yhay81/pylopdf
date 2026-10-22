"""Run reproducible pylopdf benchmarks against major PDF libraries.

Usage:

    uv sync --all-extras --group bench
    uv run python bench/run.py

Measure median timings over the same redistributable corpus and tasks, then
write both wins and losses to bench/results/latest.md. The report also records
extracted character counts and similarity to PyMuPDF as a quality proxy.
Rendering is reported end to end and split into rasterization, measured on
fresh (cold) and already open (warm) documents, and PNG encoding with its
output size.

Set BENCH_REPEATS to change the repetition count from its default of five.
"""

from __future__ import annotations

import difflib
import io
import os
import platform
import re
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

import pylopdf

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests" / "assets" / "real_world"
RESULTS = Path(__file__).resolve().parent / "results"
REPEATS = int(os.environ.get("BENCH_REPEATS", "5"))
BATCH_PAGES = 12


class _Renderer(NamedTuple):
    """Open a document, take its first page, rasterize it at 2x, and encode that raster as PNG."""

    open: Callable[[bytes], Any]
    first_page: Callable[[Any], Any]
    pixmap: Callable[[Any], Any]
    png: Callable[[Any], bytes]


class _RenderTimes(NamedTuple):
    """Rendering medians in milliseconds and encoded PNG size in bytes."""

    end_to_end: float | None
    cold: float | None
    warm: float | None
    encode: float | None
    png_bytes: int | None


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

    def _pymupdf_render_batch(data: bytes) -> int:
        """Render up to twelve pages sequentially and return total PNG bytes."""
        with fitz.open(stream=data, filetype="pdf") as doc:
            matrix = fitz.Matrix(2, 2)
            pages = range(min(BATCH_PAGES, doc.page_count))
            return sum(len(doc[pno].get_pixmap(matrix=matrix).tobytes("png")) for pno in pages)

    _PYMUPDF_RENDERER: _Renderer | None = _Renderer(
        open=lambda data: fitz.open(stream=data, filetype="pdf"),
        first_page=lambda doc: doc[0],
        pixmap=lambda page: page.get_pixmap(matrix=fitz.Matrix(2, 2)),
        png=lambda pixmap: pixmap.tobytes("png"),
    )
except Exception:  # pragma: no cover - optional dependency not installed
    _pymupdf_extract = _pymupdf_merge = _pymupdf_render = _pymupdf_render_batch = None  # type: ignore[assignment]
    _PYMUPDF_RENDERER = None

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
    with pylopdf.open(data) as doc:
        return doc.get_text()


def _pylopdf_merge(docs: list[bytes]) -> bytes:
    merged = pylopdf.Document()
    for data in docs:
        with pylopdf.open(data) as src:
            merged.pages.extend(src.pages)
    return merged.to_bytes()


def _pylopdf_render(data: bytes) -> bytes:
    with pylopdf.open(data) as doc:
        return doc.pages[0].get_pixmap(matrix=pylopdf.Matrix(2, 2)).tobytes()


def _pylopdf_render_batch(data: bytes, workers: int) -> int:
    """Render up to twelve pages and return total PNG bytes."""
    with pylopdf.open(data) as doc:
        pages = list(range(min(BATCH_PAGES, len(doc.pages))))
        return sum(len(png) for png in doc.render_pages(pages, matrix=pylopdf.Matrix(2, 2), workers=workers))


_PYLOPDF_RENDERER = _Renderer(
    open=pylopdf.open,
    first_page=lambda doc: doc.pages[0],
    pixmap=lambda page: page.get_pixmap(matrix=pylopdf.Matrix(2, 2)),
    png=lambda pixmap: pixmap.tobytes(),
)


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
    """Format one decimal place, or two below 1 so sub-millisecond runs stay visible."""
    if value is None:
        return "err/n-a"
    return f"{value:.2f}" if value < 1 else f"{value:.1f}"


def _total(values: Sequence[float | None]) -> float | None:
    """Sum per-file medians, or return None when any measurement is missing."""
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def _kib(size: float | None) -> float | None:
    return None if size is None else size / 1024


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _cpu_name() -> str:
    """Return a descriptive CPU model where the platform exposes one."""
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                check=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError):
            pass
        else:
            if name := result.stdout.strip():
                return name
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, value = line.partition(":")
            if key.strip() == "model name" and value.strip():
                return value.strip()
    return platform.processor() or platform.machine()


def _cold_pixmap(renderer: _Renderer, data: bytes) -> object:
    """Open a fresh document and rasterize its first page."""
    with renderer.open(data) as doc:
        return renderer.pixmap(renderer.first_page(doc))


def _warm_render(renderer: _Renderer, data: bytes) -> tuple[float | None, float | None, int | None]:
    """Return warm raster and PNG encode medians plus PNG size for one open document."""
    try:
        doc = renderer.open(data)
    except Exception:
        return None, None, None
    with doc:
        page = renderer.first_page(doc)
        raster = _median_ms(lambda: renderer.pixmap(page))
        try:
            pixmap = renderer.pixmap(page)
            png_bytes = len(renderer.png(pixmap))
        except Exception:
            return raster, None, None
        return raster, _median_ms(lambda: renderer.png(pixmap)), png_bytes


def _measure_render(
    renderer: _Renderer | None,
    end_to_end: Callable[[bytes], bytes] | None,
    data: bytes,
) -> _RenderTimes:
    if renderer is None or end_to_end is None:
        return _RenderTimes(None, None, None, None, None)
    return _RenderTimes(
        _median_ms(lambda: end_to_end(data)),
        _median_ms(lambda: _cold_pixmap(renderer, data)),
        *_warm_render(renderer, data),
    )


def _measure_renders(corpus: dict[str, bytes]) -> dict[str, tuple[_RenderTimes, _RenderTimes]]:
    """Measure first-page rendering for pylopdf and pymupdf on every corpus file."""
    results: dict[str, tuple[_RenderTimes, _RenderTimes]] = {}
    for name, data in corpus.items():
        results[name] = (
            _measure_render(_PYLOPDF_RENDERER, _pylopdf_render, data),
            _measure_render(_PYMUPDF_RENDERER, _pymupdf_render, data),
        )
        print(f"render {name}: done")
    return results


def _format_render_results(results: dict[str, tuple[_RenderTimes, _RenderTimes]]) -> list[str]:
    """Format rendering end to end, then split into raster and PNG work."""
    lines: list[str] = []
    rows = list(results.values())

    lines.append("## Rendering end to end (first page to 2x PNG, ms)")
    lines.append("")
    lines.append("| File | pylopdf | pymupdf |")
    lines.append("|---|---|---|")
    for name, (ours, theirs) in results.items():
        lines.append(f"| {name} | {_fmt(ours.end_to_end)} | {_fmt(theirs.end_to_end)} |")
    totals = [_total([row[i].end_to_end for row in rows]) for i in range(2)]
    lines.append("| Total | " + " | ".join(_fmt(v) for v in totals) + " |")
    lines.append("")
    lines.append("Each run opens a fresh document, rasterizes the first page at 2x, and encodes")
    lines.append("PNG with library defaults. The next two tables split that work.")
    lines.append("")

    lines.append("## Rasterization only (first page to a 2x Pixmap, ms)")
    lines.append("")
    lines.append("| File | pylopdf cold | pymupdf cold | pylopdf warm | pymupdf warm |")
    lines.append("|---|---|---|---|---|")
    for name, (ours, theirs) in results.items():
        values = (ours.cold, theirs.cold, ours.warm, theirs.warm)
        lines.append(f"| {name} | " + " | ".join(_fmt(v) for v in values) + " |")
    totals = [
        _total([row[0].cold for row in rows]),
        _total([row[1].cold for row in rows]),
        _total([row[0].warm for row in rows]),
        _total([row[1].warm for row in rows]),
    ]
    lines.append("| Total | " + " | ".join(_fmt(v) for v in totals) + " |")
    lines.append("")
    lines.append("Cold runs open a fresh document from bytes for every repetition; warm runs")
    lines.append("rasterize the same page of one open document again. Caches that outlive a")
    lines.append("document, such as process-wide glyph caches, still apply to cold runs.")
    lines.append("Both libraries return opaque RGB on white by default.")
    lines.append("")

    lines.append("## PNG encoding only (2x first-page Pixmap)")
    lines.append("")
    lines.append("| File | pylopdf ms | pymupdf ms | pylopdf KiB | pymupdf KiB |")
    lines.append("|---|---|---|---|---|")
    for name, (ours, theirs) in results.items():
        values = (ours.encode, theirs.encode, _kib(ours.png_bytes), _kib(theirs.png_bytes))
        lines.append(f"| {name} | " + " | ".join(_fmt(v) for v in values) + " |")
    totals = [
        _total([row[0].encode for row in rows]),
        _total([row[1].encode for row in rows]),
        _kib(_total([row[0].png_bytes for row in rows])),
        _kib(_total([row[1].png_bytes for row in rows])),
    ]
    lines.append("| Total | " + " | ".join(_fmt(v) for v in totals) + " |")
    lines.append("")
    lines.append("Both encoders receive an already rendered RGB Pixmap. pylopdf writes it with")
    lines.append("fast fdeflate compression; pymupdf with zlib's default level.")
    lines.append("KiB is 1,024 bytes of encoded PNG.")
    lines.append("")
    return lines


def _append_parallel_render_results(lines: list[str], corpus: dict[str, bytes]) -> None:
    """Append same-document parallel rendering scaling when the fixture exists."""
    batch_data = corpus.get("usrguide.pdf")
    if batch_data is None:
        return

    lines.append(f"## Parallel rendering (first {BATCH_PAGES} usrguide pages to 2x PNG, ms)")
    lines.append("")
    lines.append("| Workers | Time | Speedup vs 1 worker |")
    lines.append("|---:|---:|---:|")
    batch_times = {
        workers: _median_ms(lambda w=workers: _pylopdf_render_batch(batch_data, w)) for workers in (1, 2, 4, 8)
    }
    baseline = batch_times[1]
    for workers, elapsed in batch_times.items():
        speedup = "err/n-a" if baseline is None or elapsed is None or elapsed <= 0 else f"{baseline / elapsed:.2f}x"
        lines.append(f"| {workers} | {_fmt(elapsed)} | {speedup} |")
    lines.append("")
    lines.append(
        "`render_pages()` preserves input order, releases the GIL, and uses "
        "a dedicated worker pool bounded by both worker count and estimated "
        "live rendering memory."
    )
    if _pymupdf_render_batch is not None:
        sequential = _median_ms(lambda: _pymupdf_render_batch(batch_data))
        lines.append(
            "For reference, pymupdf renders and encodes the same pages sequentially "
            f"on one fresh document in {_fmt(sequential)} ms."
        )
    lines.append("")
    print("parallel render: done")


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
    lines.append(f"- Run at: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(
        f"- Environment: {platform.platform()} / Python {platform.python_version()} / "
        f"CPU {_cpu_name()} ({os.cpu_count()} logical CPUs)"
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
    extract_rows = []
    for name, data in corpus.items():
        row = [
            _median_ms(lambda d=data: _pylopdf_extract(d)),
            _median_ms(lambda d=data: _pymupdf_extract(d)) if _pymupdf_extract else None,
            _median_ms(lambda d=data: _pypdf_extract(d)) if _pypdf_extract else None,
            _median_ms(lambda d=data: _pdfplumber_extract(d)) if _pdfplumber_extract else None,
        ]
        extract_rows.append(row)
        lines.append(f"| {name} | " + " | ".join(_fmt(v) for v in row) + " |")
        print(f"extract {name}: done")
    totals = [_total(column) for column in zip(*extract_rows, strict=True)]
    lines.append("| Total | " + " | ".join(_fmt(v) for v in totals) + " |")
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
    lines.append("Low scores can reflect the PyMuPDF reference convention rather than pylopdf quality:")
    lines.append("on rotated table reports PyMuPDF emits unsorted content order (its own sort=True")
    lines.append("scores lower still there), and on dense forms pylopdf matches PyMuPDF's sorted")
    lines.append("mode while the default reference stays in content order.")
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

    # --- Render the first page at 2x, then split raster and PNG work. ---
    lines.extend(_format_render_results(_measure_renders(corpus)))

    # --- Same-document parallel rendering scaling. ---
    _append_parallel_render_results(lines, corpus)

    lines.append("This report publishes both wins and losses. Results depend on the environment,")
    lines.append("so cite them together with the environment details above.")
    lines.append("")

    RESULTS.mkdir(exist_ok=True)
    out_path = RESULTS / "latest.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
