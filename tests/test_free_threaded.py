"""Free-threaded CPython support and distinct-document concurrency checks."""

from __future__ import annotations

import os
import statistics
import sys
import sysconfig
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import pylopdf

IS_FREE_THREADED = sysconfig.get_config_var("Py_GIL_DISABLED") == 1
pytestmark = pytest.mark.skipif(not IS_FREE_THREADED, reason="requires a free-threaded CPython build")
ASSETS = Path(__file__).parent / "assets" / "real_world"


def _extract_document(data: bytes) -> str:
    with pylopdf.open(stream=data) as doc:
        return "".join(doc.get_page_text(index) for index in range(doc.page_count))


def _timed_extract_pair(data: bytes, *, parallel: bool) -> tuple[float, tuple[str, str]]:
    start = time.perf_counter()
    if parallel:
        with ThreadPoolExecutor(max_workers=2) as executor:
            extracted = list(executor.map(_extract_document, (data, data)))
            outputs = (extracted[0], extracted[1])
    else:
        outputs = (_extract_document(data), _extract_document(data))
    return time.perf_counter() - start, outputs


def test_import_keeps_gil_disabled() -> None:
    is_gil_enabled = getattr(sys, "_is_gil_enabled", None)
    assert is_gil_enabled is not None
    assert not is_gil_enabled()


def test_distinct_documents_are_correct_and_scale_across_threads() -> None:
    data = (ASSETS / "bill-hr815.pdf").read_bytes()
    _timed_extract_pair(data, parallel=False)
    _timed_extract_pair(data, parallel=True)
    sequential_runs: list[tuple[float, tuple[str, str]]] = []
    parallel_runs: list[tuple[float, tuple[str, str]]] = []
    parallel_first = False
    for _ in range(3):
        first = _timed_extract_pair(data, parallel=parallel_first)
        second = _timed_extract_pair(data, parallel=not parallel_first)
        (parallel_runs if parallel_first else sequential_runs).append(first)
        (sequential_runs if parallel_first else parallel_runs).append(second)
        parallel_first = not parallel_first

    expected = sequential_runs[0][1]
    assert all(outputs == expected for _, outputs in sequential_runs + parallel_runs)
    if (os.cpu_count() or 1) >= 2:
        sequential = statistics.median(elapsed for elapsed, _ in sequential_runs)
        parallel = statistics.median(elapsed for elapsed, _ in parallel_runs)
        assert sequential / parallel >= 1.05, (
            f"distinct-document extraction did not scale: sequential={sequential:.3f}s, parallel={parallel:.3f}s"
        )
