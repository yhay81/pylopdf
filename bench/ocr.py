"""Measure native OCR accuracy and elapsed time on the licensed MHLW fixture.

Usage:

    uv sync --all-extras
    uv run python bench/ocr.py

The PDF retains its embedded text as ground truth while pylopdf renders the
visible page and recognizes it independently. Results include both
whitespace-stripped strict CER and NFKC-normalized CER so width-form losses
remain visible rather than being reported only in the more practical metric.
"""

from __future__ import annotations

import argparse
import platform
import time
import unicodedata
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import NamedTuple

import pylopdf_ocr_models

import pylopdf

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "assets" / "real_world" / "mhlw-doc.pdf"
RESULT = Path(__file__).resolve().parent / "results" / "ocr-latest.md"


class OcrBenchmarkResult(NamedTuple):
    """One complete accuracy and latency measurement."""

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
    parser.add_argument("--dpi", type=int, nargs="+", default=[150, 300])
    parser.add_argument("--tile-size", type=int, default=1408)
    parser.add_argument("--overlap", type=int, default=192)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--max-concurrent", type=int, default=1)
    parser.add_argument("--min-confidence", type=float, default=0.5)
    parser.add_argument("--no-write", action="store_true", help="print results without updating the Markdown report")
    return parser.parse_args()


def main() -> None:
    """Run each requested resolution and write a reproducible report."""
    args = _parse_args()
    engine = pylopdf.OcrEngine(threads=args.threads, max_concurrent=args.max_concurrent)
    rows: list[OcrBenchmarkResult] = []

    with pylopdf.open(FIXTURE) as document:
        expected = "\n".join(page.get_text() for page in document)
        for dpi in args.dpi:
            started = time.perf_counter()
            page_results = [
                page.get_text_ocr(
                    dpi=dpi,
                    engine=engine,
                    tile_size=args.tile_size,
                    overlap=args.overlap,
                    min_confidence=args.min_confidence,
                )
                for page in document
            ]
            elapsed = time.perf_counter() - started
            words = [word for page_words in page_results for word in page_words]
            actual = "\n".join(word["text"] for word in words)
            strict_expected, strict_actual, strict_edits, strict_cer = _cer(expected, actual, nfkc=False)
            nfkc_expected, nfkc_actual, nfkc_edits, nfkc_cer = _cer(expected, actual, nfkc=True)
            rows.append(
                OcrBenchmarkResult(
                    dpi=dpi,
                    words=len(words),
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
                f"dpi={dpi} words={len(words)} strict_cer={strict_cer:.3%} "
                f"nfkc_cer={nfkc_cer:.3%} elapsed={elapsed:.2f}s"
            )

    model_root = pylopdf_ocr_models.model_paths().detector.parent
    checksums = (model_root / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    lines = [
        "# pylopdf native OCR results",
        "",
        f"- Run at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"- Environment: {platform.platform()} / Python {platform.python_version()} / CPU {platform.processor()}",
        f"- Versions: pylopdf {metadata.version('pylopdf')}, pylopdf-ocr-models {pylopdf_ocr_models.__version__}",
        (
            f"- Fixture: `{FIXTURE.relative_to(ROOT).as_posix()}` "
            "(source and license in `tests/assets/real_world/README.md`)"
        ),
        (
            f"- Controls: tile size {args.tile_size}, overlap {args.overlap}, "
            f"threads {args.threads}, max concurrent {args.max_concurrent}, "
            f"minimum confidence {args.min_confidence}"
        ),
        "- Reproduce: `uv sync --all-extras && uv run python bench/ocr.py`",
        "- Model artifacts: " + "; ".join(entry.replace("  ", " ") for entry in checksums),
        "",
        (
            "| DPI | Words | Strict expected / actual | Strict edits | Strict CER | "
            "NFKC expected / actual | NFKC edits | NFKC CER | Elapsed |"
        ),
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    lines.extend(
        (
            f"| {row.dpi} | {row.words} | {row.strict_expected} / {row.strict_actual} | "
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
    report = "\n".join(lines)
    if not args.no_write:
        RESULT.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
