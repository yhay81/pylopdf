"""Measure native OCR accuracy and bounded concurrency on licensed fixtures.

Usage:

    uv sync --all-extras
    uv run python bench/ocr.py

One digital PDF retains embedded text as independent ground truth; one archival
scan uses a manually verified transcript. Results include whitespace-stripped
strict CER and NFKC-normalized CER so width-form losses remain visible rather
than being reported only in the more practical metric.
"""

from __future__ import annotations

import argparse
import platform
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import NamedTuple

import pylopdf_ocr_models

import pylopdf

ROOT = Path(__file__).resolve().parent.parent
RESULT = Path(__file__).resolve().parent / "results" / "ocr-latest.md"


class OcrCase(NamedTuple):
    """One licensed document and its independent text ground truth."""

    name: str
    fixture: Path
    ground_truth: Path | None
    description: str


CASES = (
    OcrCase(
        name="mhlw-digital",
        fixture=ROOT / "tests" / "assets" / "real_world" / "mhlw-doc.pdf",
        ground_truth=None,
        description="embedded text retained as independent ground truth",
    ),
    OcrCase(
        name="bunka-scan",
        fixture=ROOT / "tests" / "assets" / "real_world" / "bunka-kokugo-series-019-p4.pdf",
        ground_truth=ROOT / "bench" / "ground_truth" / "bunka-kokugo-series-019-p4.txt",
        description="image-only archival scan with manually verified ground truth",
    ),
)


class OcrBenchmarkResult(NamedTuple):
    """One complete accuracy and latency measurement."""

    case: str
    dpi: int
    words: int
    strict_expected: int
    strict_actual: int
    strict_edits: int
    strict_cer: float
    nfkc_expected: int
    nfkc_actual: int
    nfkc_edits: int
    nfkc_cer: float
    elapsed: float


class OcrConcurrencyResult(NamedTuple):
    """One bounded concurrent workload measurement."""

    max_concurrent: int
    calls: int
    words: int
    matches_reference: bool
    elapsed: float


class OcrControls(NamedTuple):
    """Recognition controls shared by sequential and concurrent passes."""

    dpi: int
    tile_size: int
    overlap: int
    min_confidence: float


def _compact(text: str, *, nfkc: bool) -> str:
    """Remove whitespace and optionally normalize compatibility forms."""
    if nfkc:
        text = unicodedata.normalize("NFKC", text)
    return "".join(char for char in text if not char.isspace())


def _edit_distance(left: str, right: str) -> int:
    """Return Levenshtein distance with memory linear in the shorter input."""
    if len(left) > len(right):
        left, right = right, left
    previous = list(range(len(left) + 1))
    for row, right_char in enumerate(right, start=1):
        current = [row]
        for column, left_char in enumerate(left, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def _cer(expected: str, actual: str, *, nfkc: bool) -> tuple[int, int, int, float]:
    expected_compact = _compact(expected, nfkc=nfkc)
    actual_compact = _compact(actual, nfkc=nfkc)
    edits = _edit_distance(expected_compact, actual_compact)
    return len(expected_compact), len(actual_compact), edits, edits / len(expected_compact)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", choices=[case.name for case in CASES])
    parser.add_argument("--dpi", type=int, nargs="+", default=[150, 300])
    parser.add_argument("--tile-size", type=int, default=1408)
    parser.add_argument("--overlap", type=int, default=192)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--max-concurrent", type=int, default=1)
    parser.add_argument(
        "--concurrency",
        type=int,
        nargs="*",
        choices=range(1, 17),
        default=[1, 2],
        help="admission limits to field-test concurrently at the first requested DPI; pass no values to skip",
    )
    parser.add_argument("--min-confidence", type=float, default=0.5)
    parser.add_argument("--no-write", action="store_true", help="print results without updating the Markdown report")
    return parser.parse_args()


def _expected_text(case: OcrCase, document: pylopdf.Document) -> str:
    """Read manual ground truth or retain embedded text independently."""
    if case.ground_truth is not None:
        return case.ground_truth.read_text(encoding="utf-8")
    return "\n".join(page.get_text() for page in document)


def _recognize_case(
    case: OcrCase,
    engine: pylopdf.OcrEngine,
    controls: OcrControls,
) -> tuple[int, str]:
    """Recognize one distinct document and return its word count and text."""
    with pylopdf.open(case.fixture) as document:
        page_results = [
            page.get_text_ocr(
                dpi=controls.dpi,
                engine=engine,
                tile_size=controls.tile_size,
                overlap=controls.overlap,
                min_confidence=controls.min_confidence,
            )
            for page in document
        ]
    words = [word for page_words in page_results for word in page_words]
    return len(words), "\n".join(word["text"] for word in words)


def main() -> None:
    """Run each requested resolution and write a reproducible report."""
    args = _parse_args()
    selected_names = set(args.case or ())
    cases = tuple(case for case in CASES if not selected_names or case.name in selected_names)
    engine = pylopdf.OcrEngine(threads=args.threads, max_concurrent=args.max_concurrent)
    rows: list[OcrBenchmarkResult] = []
    recognized_text: dict[tuple[str, int], str] = {}

    for case in cases:
        with pylopdf.open(case.fixture) as document:
            expected = _expected_text(case, document)
        for dpi in args.dpi:
            controls = OcrControls(
                dpi=dpi,
                tile_size=args.tile_size,
                overlap=args.overlap,
                min_confidence=args.min_confidence,
            )
            started = time.perf_counter()
            word_count, actual = _recognize_case(case, engine, controls)
            elapsed = time.perf_counter() - started
            recognized_text[(case.name, dpi)] = actual
            strict_expected, strict_actual, strict_edits, strict_cer = _cer(expected, actual, nfkc=False)
            nfkc_expected, nfkc_actual, nfkc_edits, nfkc_cer = _cer(expected, actual, nfkc=True)
            rows.append(
                OcrBenchmarkResult(
                    case=case.name,
                    dpi=dpi,
                    words=word_count,
                    strict_expected=strict_expected,
                    strict_actual=strict_actual,
                    strict_edits=strict_edits,
                    strict_cer=strict_cer,
                    nfkc_expected=nfkc_expected,
                    nfkc_actual=nfkc_actual,
                    nfkc_edits=nfkc_edits,
                    nfkc_cer=nfkc_cer,
                    elapsed=elapsed,
                )
            )
            print(
                f"case={case.name} dpi={dpi} words={word_count} strict_cer={strict_cer:.3%} "
                f"nfkc_cer={nfkc_cer:.3%} elapsed={elapsed:.2f}s"
            )

    concurrency_rows: list[OcrConcurrencyResult] = []
    concurrency_dpi = args.dpi[0]
    concurrency_controls = OcrControls(
        dpi=concurrency_dpi,
        tile_size=args.tile_size,
        overlap=args.overlap,
        min_confidence=args.min_confidence,
    )
    concurrency_limits = args.concurrency if len(cases) > 1 else []
    if args.concurrency and not concurrency_limits:
        print("skipping concurrency field check: select at least two cases")
    for max_concurrent in concurrency_limits:
        concurrent_engine = pylopdf.OcrEngine(
            threads=args.threads,
            max_concurrent=max_concurrent,
        )
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=len(cases)) as executor:
            futures = [
                executor.submit(
                    _recognize_case,
                    case,
                    concurrent_engine,
                    concurrency_controls,
                )
                for case in cases
            ]
            results = [future.result() for future in futures]
        elapsed = time.perf_counter() - started
        matches_reference = all(
            actual == recognized_text[(case.name, concurrency_dpi)]
            for case, (_, actual) in zip(cases, results, strict=True)
        )
        concurrency_rows.append(
            OcrConcurrencyResult(
                max_concurrent=max_concurrent,
                calls=len(cases),
                words=sum(words for words, _ in results),
                matches_reference=matches_reference,
                elapsed=elapsed,
            )
        )
        print(
            f"max_concurrent={max_concurrent} calls={len(cases)} "
            f"matches_reference={matches_reference} elapsed={elapsed:.2f}s"
        )

    model_root = pylopdf_ocr_models.model_paths().detector.parent
    checksums = (model_root / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    lines = [
        "# pylopdf native OCR results",
        "",
        f"- Run at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"- Environment: {platform.platform()} / Python {platform.python_version()} / CPU {platform.processor()}",
        f"- Versions: pylopdf {metadata.version('pylopdf')}, pylopdf-ocr-models {pylopdf_ocr_models.__version__}",
        "- Fixtures (sources and licenses in `tests/assets/real_world/README.md`):",
    ]
    lines.extend(
        f"  - `{case.name}`: `{case.fixture.relative_to(ROOT).as_posix()}` - {case.description}" for case in cases
    )
    lines.extend(
        [
            (
                f"- Controls: tile size {args.tile_size}, overlap {args.overlap}, "
                f"threads {args.threads}, max concurrent {args.max_concurrent}, "
                f"minimum confidence {args.min_confidence}"
            ),
            "- Reproduce: `uv sync --all-extras && uv run python bench/ocr.py`",
            "- Model artifacts: " + "; ".join(entry.replace("  ", " ") for entry in checksums),
            "",
            (
                "| Case | DPI | Words | Strict expected / actual | Strict edits | Strict CER | "
                "NFKC expected / actual | NFKC edits | NFKC CER | Elapsed |"
            ),
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    lines.extend(
        (
            f"| {row.case} | {row.dpi} | {row.words} | "
            f"{row.strict_expected} / {row.strict_actual} | "
            f"{row.strict_edits} | {row.strict_cer:.3%} | {row.nfkc_expected} / "
            f"{row.nfkc_actual} | {row.nfkc_edits} | {row.nfkc_cer:.3%} | {row.elapsed:.2f}s |"
        )
        for row in rows
    )
    lines.extend(
        [
            "",
            "Strict CER removes whitespace only. NFKC CER additionally folds compatibility",
            "forms such as full-width Latin characters. Elapsed time includes page rendering,",
            "tiled detection, recognition, ordering, and Python result materialization after",
            "the engine has been loaded. It is one run, not a throughput benchmark.",
            "",
        ]
    )
    if concurrency_rows:
        lines.extend(
            [
                "## Bounded concurrent workload",
                "",
                (
                    f"The {len(cases)} selected documents run through one shared engine at "
                    f"{concurrency_dpi} dpi. The timer starts after model loading. Equality "
                    "compares exact ordered OCR text with the sequential accuracy pass."
                ),
                "",
                "| `max_concurrent` | Calls | Words | Exact reference match | Elapsed |",
                "|---:|---:|---:|:---:|---:|",
            ]
        )
        lines.extend(
            (
                f"| {row.max_concurrent} | {row.calls} | {row.words} | "
                f"{'yes' if row.matches_reference else 'no'} | {row.elapsed:.2f}s |"
            )
            for row in concurrency_rows
        )
        lines.extend(
            [
                "",
                "This is a bounded field check, not a throughput claim: simultaneous calls own",
                "separate raster and inference buffers, so peak memory grows with the admission",
                "limit even when elapsed time does not improve.",
                "",
            ]
        )
    report = "\n".join(lines)
    if not args.no_write:
        RESULT.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
