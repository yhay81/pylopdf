"""Measure pylopdf rendering workloads for one installed build.

Usage: python ab.py REPOSITORY_ROOT > result.json

The rendering split (end to end, cold and warm rasterization, PNG encoding)
comes from the bench/run.py proposed in yhay81/pylopdf#202, vendored next to
this file so baseline and candidate builds run identical workloads. Additional
workloads cover sequential page loops and repeated batches on one open
document, the cases a cross-call render cache targets.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import pylopdf

HERE = Path(__file__).resolve().parent


def _load_bench(repo: Path):
    spec = importlib.util.spec_from_file_location("bench_run", HERE / "bench_run.py")
    assert spec is not None and spec.loader is not None
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    bench.CORPUS = repo / "tests" / "assets" / "real_world"
    return bench


def _pixmap_digest(pixmap: pylopdf.Pixmap) -> str:
    return hashlib.sha256(pixmap.samples).hexdigest()[:16]


def main() -> None:
    repo = Path(sys.argv[1]).resolve()
    bench = _load_bench(repo)
    median = bench._median_ms
    corpus = {path.name: path.read_bytes() for path in sorted(bench.CORPUS.glob("*.pdf"))}
    started = time.perf_counter()
    result: dict[str, object] = {
        "python": platform.python_version(),
        "pylopdf_module": pylopdf.pylopdf_core.__file__,
        "repeats": bench.REPEATS,
    }

    renders = {
        name: bench._measure_render(bench._PYLOPDF_RENDERER, bench._pylopdf_render, data)
        for name, data in corpus.items()
    }
    for field in ("end_to_end", "cold", "warm", "encode"):
        per_file = {name: getattr(times, field) for name, times in renders.items()}
        result[f"render_{field}"] = per_file
        result[f"render_{field}_total"] = sum(per_file.values())

    # Pixel identity: the first (cold) and a repeated (warm) raster must match
    # each other and the other build on the same platform.
    digests = {}
    for name, data in corpus.items():
        with pylopdf.open(stream=data) as doc:
            first = _pixmap_digest(doc[0].get_pixmap(scale=2))
            again = _pixmap_digest(doc[0].get_pixmap(scale=2))
        digests[name] = [first, again]
    result["pixmap_digests"] = digests

    usrguide = corpus["usrguide.pdf"]
    bill = corpus["bill-hr815.pdf"]
    for workers in (1, 4):
        result[f"batch_w{workers}"] = median(lambda w=workers: bench._pylopdf_render_batch(usrguide, w))
    with pylopdf.open(stream=usrguide) as doc:
        pages = list(range(min(bench.BATCH_PAGES, doc.page_count)))
        batch = doc.render_pages(pages, scale=2, workers=1)
        result["batch_digest"] = hashlib.sha256(b"".join(batch)).hexdigest()[:16]
        for workers in (1, 4):
            result[f"batch_open_w{workers}"] = median(
                lambda w=workers: doc.render_pages(pages, scale=2, workers=w)
            )

    # Sequential Page.get_pixmap loops: one pass over a freshly opened document,
    # and a repeated pass over the same open document.
    for label, data, count, scale in (
        ("usrguide_1x", usrguide, 27, 1.0),
        ("usrguide_2x", usrguide, 27, 2.0),
        ("bill_half", bill, 110, 0.5),
    ):

        def fresh(data: bytes = data, count: int = count, scale: float = scale) -> None:
            with pylopdf.open(stream=data) as doc:
                for index in range(min(count, doc.page_count)):
                    doc[index].get_pixmap(scale=scale)

        result[f"seq_fresh_{label}"] = median(fresh)
        with pylopdf.open(stream=data) as doc:
            pages = list(range(min(count, doc.page_count)))

            def again(doc: pylopdf.Document = doc, pages: list[int] = pages, scale: float = scale) -> None:
                for index in pages:
                    doc[index].get_pixmap(scale=scale)

            result[f"seq_open_{label}"] = median(again)

    memory = {}
    for scenario in ("usrguide_passes", "bill_pass", "open_documents"):
        output = subprocess.run(
            [sys.executable, "-W", "ignore", str(HERE / "memory.py"), str(repo), scenario],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        memory[scenario] = json.loads(output)
    result["memory"] = memory
    result["elapsed_s"] = time.perf_counter() - started
    print(json.dumps(result))


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    main()
