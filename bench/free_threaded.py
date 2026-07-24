"""Measure distinct-document extraction on free-threaded CPython."""

from __future__ import annotations

import argparse
import platform
import statistics
import sys
import sysconfig
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pylopdf

DEFAULT_INPUT = Path(__file__).parents[1] / "tests" / "assets" / "real_world" / "bill-hr815.pdf"
MIN_COPIES = 2


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


def main() -> None:
    """Run and print the free-threaded extraction benchmark."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--copies", type=int, default=2)
    parser.add_argument("--repetitions", type=int, default=7)
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
    print("# pylopdf free-threaded benchmark")
    print()
    print(f"- Environment: {platform.platform()} / Python {platform.python_version()} free-threaded")
    print(f"- Input: `{args.input.as_posix()}`")
    print(f"- Workload: {args.copies} independent documents, all-page text extraction")
    print(f"- Repetitions: one warmup + median of {args.repetitions} paired, alternating-order runs")
    print()
    print("| Mode | Workers | Time (ms) | Speedup |")
    print("|---|---:|---:|---:|")
    print(f"| Sequential | 1 | {sequential * 1000:.1f} | 1.00x |")
    print(f"| Parallel | {args.copies} | {parallel * 1000:.1f} | {sequential / parallel:.2f}x |")


if __name__ == "__main__":
    main()
