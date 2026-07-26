"""Measure distinct-document extraction on free-threaded CPython."""

from __future__ import annotations

import argparse
import os
import platform
import statistics
import sys
import sysconfig
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pylopdf

ROOT = Path(__file__).parents[1]
DEFAULT_INPUT = ROOT / "tests" / "assets" / "real_world" / "bill-hr815.pdf"
DEFAULT_OUTPUT = Path(__file__).parent / "results" / "free-threaded-latest.md"
MIN_COPIES = 2


@dataclass(frozen=True)
class BenchmarkReport:
    """Complete metadata and timings for one generated report."""

    environment: str
    input_path: str
    copies: int
    repetitions: int
    sequential_seconds: float
    parallel_seconds: float
    python_command: str
    run_at: str
    version: str


def _extract(data: bytes) -> str:
    with pylopdf.open(stream=data) as document:
        return "".join(document.get_page_text(index) for index in range(document.page_count))


def _run_once(data: bytes, *, copies: int, workers: int) -> tuple[float, list[str]]:
    inputs = [data] * copies
    start = time.perf_counter()
    if workers == 1:
        outputs = [_extract(item) for item in inputs]
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            outputs = list(executor.map(_extract, inputs))
    return time.perf_counter() - start, outputs


def _display_path(path: Path) -> str:
    """Return a repository-relative input path when possible."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _format_report(result: BenchmarkReport) -> str:
    """Format one complete, independently reproducible benchmark report."""
    speedup = result.sequential_seconds / result.parallel_seconds
    lines = [
        "# pylopdf free-threaded benchmark",
        "",
        f"- Run at: {result.run_at}",
        f"- Environment: {result.environment}",
        f"- pylopdf: {result.version}",
        f"- Input: `{result.input_path}`",
        f"- Workload: {result.copies} independent documents, all-page text extraction",
        f"- Repetitions: one warmup + median of {result.repetitions} paired, alternating-order runs",
        "- Output validation: every parallel result exactly matched the sequential result",
        (
            f"- Reproduce: `{result.python_command} bench/free_threaded.py "
            f"--copies {result.copies} --repetitions {result.repetitions}`"
        ),
        "",
        "| Mode | Workers | Time (ms) | Speedup |",
        "|---|---:|---:|---:|",
        f"| Sequential | 1 | {result.sequential_seconds * 1000:.1f} | 1.00x |",
        f"| Parallel | {result.copies} | {result.parallel_seconds * 1000:.1f} | {speedup:.2f}x |",
        "",
        "This report is generated separately from `bench/results/latest.md` so rerunning",
        "the regular GIL-enabled benchmark cannot discard the free-threaded evidence.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    """Run, print, and write the free-threaded extraction benchmark."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--copies", type=int, default=2)
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Markdown report path (default: {DEFAULT_OUTPUT.relative_to(ROOT).as_posix()})",
    )
    args = parser.parse_args()

    if sysconfig.get_config_var("Py_GIL_DISABLED") != 1:
        parser.error("run this benchmark with a free-threaded CPython build")
    is_gil_enabled = getattr(sys, "_is_gil_enabled", None)
    if is_gil_enabled is None or is_gil_enabled():
        parser.error("the GIL must remain disabled after importing pylopdf")
    if args.copies < MIN_COPIES:
        parser.error("--copies must be at least 2")
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")

    data = args.input.read_bytes()
    _run_once(data, copies=args.copies, workers=1)
    _run_once(data, copies=args.copies, workers=args.copies)

    sequential_times: list[float] = []
    parallel_times: list[float] = []
    expected: list[str] | None = None
    parallel_first = False
    for _ in range(args.repetitions):
        first_workers = args.copies if parallel_first else 1
        second_workers = 1 if parallel_first else args.copies
        first_time, first_output = _run_once(data, copies=args.copies, workers=first_workers)
        second_time, second_output = _run_once(data, copies=args.copies, workers=second_workers)
        if parallel_first:
            parallel, parallel_output = first_time, first_output
            sequential, sequential_output = second_time, second_output
        else:
            sequential, sequential_output = first_time, first_output
            parallel, parallel_output = second_time, second_output
        expected = expected or sequential_output
        if sequential_output != expected or parallel_output != expected:
            message = "parallel extraction output differs from sequential extraction"
            raise RuntimeError(message)
        sequential_times.append(sequential)
        parallel_times.append(parallel)
        parallel_first = not parallel_first

    sequential = statistics.median(sequential_times)
    parallel = statistics.median(parallel_times)
    report = _format_report(
        BenchmarkReport(
            environment=f"{platform.platform()} / Python {platform.python_version()} free-threaded",
            input_path=_display_path(args.input),
            copies=args.copies,
            repetitions=args.repetitions,
            sequential_seconds=sequential,
            parallel_seconds=parallel,
            python_command="py -3.14t" if os.name == "nt" else "python3.14t",
            run_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            version=pylopdf.__version__,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(report, end="")
    print(f"\nwrote {args.output}")


if __name__ == "__main__":
    main()
