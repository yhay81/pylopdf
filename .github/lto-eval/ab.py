"""Measure pylopdf-only bench/run.py workloads for one installed build.

Usage: python ab.py REPOSITORY_ROOT > result.json

The script loads the repository's bench/run.py as a module so the measured
workloads, repetition policy, and corpus stay identical to the published report.
"""

from __future__ import annotations

import importlib.util
import json
import platform
import sys
import time
from pathlib import Path

import pylopdf


def main() -> None:
    repo = Path(sys.argv[1]).resolve()
    spec = importlib.util.spec_from_file_location("bench_run", repo / "bench" / "run.py")
    assert spec is not None and spec.loader is not None
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)

    corpus = {path.name: path.read_bytes() for path in sorted(bench.CORPUS.glob("*.pdf"))}
    median = bench._median_ms
    started = time.perf_counter()
    result: dict[str, object] = {
        "python": platform.python_version(),
        "pylopdf_module": pylopdf.pylopdf_core.__file__,
        "repeats": bench.REPEATS,
    }

    extract = {name: median(lambda d=data: bench._pylopdf_extract(d)) for name, data in corpus.items()}
    result["extract"] = extract
    result["extract_total"] = sum(extract.values())

    def markdown(data: bytes) -> str:
        with pylopdf.open(stream=data) as doc:
            return doc.to_markdown()

    md = {name: median(lambda d=data: markdown(d)) for name, data in corpus.items()}
    result["markdown_total"] = sum(v for v in md.values() if v is not None)

    docs = list(corpus.values())
    result["merge"] = median(lambda: bench._pylopdf_merge(docs))

    # Merge phases: parse every input, import all pages, and serialize the result.
    def open_all() -> None:
        for data in docs:
            pylopdf.open(stream=data).close()

    def import_all() -> pylopdf.Document:
        merged = pylopdf.Document()
        for data in docs:
            with pylopdf.open(stream=data) as src:
                merged.insert_pdf(src)
        return merged

    result["merge_open_all"] = median(open_all)
    result["merge_import_all"] = median(lambda: import_all().close())
    merged_doc = import_all()
    result["merge_tobytes"] = median(merged_doc.tobytes)
    merged_doc.close()

    renders = {name: bench._measure_render(bench._PYLOPDF_RENDERER, bench._pylopdf_render, data) for name, data in corpus.items()}
    for field in ("end_to_end", "cold", "warm", "encode"):
        per_file = {name: getattr(times, field) for name, times in renders.items()}
        result[f"render_{field}"] = per_file
        result[f"render_{field}_total"] = sum(per_file.values())
    result["png_bytes_total"] = sum(times.png_bytes for times in renders.values())

    batch = corpus["usrguide.pdf"]
    for workers in (1, 4):
        result[f"batch_w{workers}"] = median(lambda w=workers: bench._pylopdf_render_batch(batch, w))

    result["elapsed_s"] = time.perf_counter() - started
    print(json.dumps(result))


if __name__ == "__main__":
    main()
