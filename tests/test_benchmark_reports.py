from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from runpy import run_path
from typing import Any, cast

_BENCHMARK = run_path(str(Path(__file__).parents[1] / "bench" / "free_threaded.py"))
BenchmarkReport = cast("type[Any]", _BENCHMARK["BenchmarkReport"])
display_path = cast("Callable[[Path], str]", _BENCHMARK["_display_path"])
format_report = cast("Callable[[Any], str]", _BENCHMARK["_format_report"])


def test_free_threaded_report_is_complete_and_standalone() -> None:
    report = format_report(
        BenchmarkReport(
            environment="Test OS / Python 3.14.6 free-threaded",
            input_path="tests/assets/real_world/input.pdf",
            copies=2,
            repetitions=7,
            sequential_seconds=0.28,
            parallel_seconds=0.16,
            python_command="py -3.14t",
            run_at="2026-07-26 10:00 UTC",
            version="0.11.1",
        )
    )

    assert report.endswith("\n")
    assert "- pylopdf: 0.11.1" in report
    assert "| Sequential | 1 | 280.0 | 1.00x |" in report
    assert "| Parallel | 2 | 160.0 | 1.75x |" in report
    assert "- Reproduce: `py -3.14t bench/free_threaded.py" in report
    assert "every parallel result exactly matched" in report
    assert "bench/results/latest.md" in report


def test_free_threaded_default_input_is_repository_relative() -> None:
    input_path = Path(__file__).parents[1] / "tests" / "assets" / "real_world" / "bill-hr815.pdf"

    assert display_path(input_path) == "tests/assets/real_world/bill-hr815.pdf"
