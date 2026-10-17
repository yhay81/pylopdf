from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from runpy import run_path
from typing import Any, cast

_BENCHMARK = run_path(str(Path(__file__).parents[1] / "bench" / "free_threaded.py"))
BenchmarkReport = cast("type[Any]", _BENCHMARK["BenchmarkReport"])
display_path = cast("Callable[[Path], str]", _BENCHMARK["_display_path"])
format_report = cast("Callable[[Any], str]", _BENCHMARK["_format_report"])

_RUN = run_path(str(Path(__file__).parents[1] / "bench" / "run.py"))
RenderTimes = cast("type[Any]", _RUN["_RenderTimes"])
format_render_results = cast("Callable[[dict[str, tuple[Any, Any]]], list[str]]", _RUN["_format_render_results"])


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


def test_render_report_separates_rasterization_from_png_encoding() -> None:
    report = "\n".join(
        format_render_results(
            {
                "text.pdf": (
                    RenderTimes(end_to_end=12.0, cold=9.5, warm=7.0, encode=2.5, png_bytes=4096),
                    RenderTimes(end_to_end=30.0, cold=6.0, warm=1.5, encode=24.0, png_bytes=2048),
                ),
                "tiny.pdf": (
                    RenderTimes(end_to_end=1.5, cold=0.7, warm=0.3, encode=0.2, png_bytes=512),
                    RenderTimes(end_to_end=None, cold=None, warm=None, encode=None, png_bytes=None),
                ),
            }
        )
    )

    assert "| text.pdf | 12.0 | 30.0 |" in report
    assert "| Total | 13.5 | err/n-a |" in report
    assert "| text.pdf | 9.5 | 6.0 | 7.0 | 1.5 |" in report
    assert "| tiny.pdf | 0.70 | err/n-a | 0.30 | err/n-a |" in report
    assert "| Total | 10.2 | err/n-a | 7.3 | err/n-a |" in report
    assert "| text.pdf | 2.5 | 24.0 | 4.0 | 2.0 |" in report
    assert "| tiny.pdf | 0.20 | err/n-a | 0.50 | err/n-a |" in report
    assert "| Total | 2.7 | err/n-a | 4.5 | err/n-a |" in report
    assert "fresh document from bytes for every repetition" in report
    assert "straight-alpha RGBA8" in report
