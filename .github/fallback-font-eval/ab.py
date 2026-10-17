"""Measure fresh-document workloads for one installed pylopdf build.

Usage: python ab.py CORPUS_DIR FIXTURE_DIR > result.json

Both environments install every locale font extra, as ``uv sync
--all-extras`` does, so each fresh document auto-configures the CJK fallback
fonts before its first render. Extraction alone never configures them and
serves as a control. Every timing is the median of AB_REPEATS runs after one
warmup; each round runs in a new interpreter.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pylopdf

HERE = Path(__file__).resolve().parent
REPEATS = int(os.environ.get("AB_REPEATS", "9"))
BATCH_PAGES = 12


def median_ms(func: Callable[[], object]) -> float:
    func()
    samples = []
    for _ in range(REPEATS):
        start = time.perf_counter()
        func()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples)


def render_fresh(data: bytes) -> None:
    with pylopdf.open(stream=data) as doc:
        doc[0].get_pixmap(scale=2)


def png_fresh(data: bytes) -> None:
    with pylopdf.open(stream=data) as doc:
        doc.render_page(0, 2.0)


def extract_fresh(data: bytes) -> None:
    with pylopdf.open(stream=data) as doc:
        doc.get_text()


def render_then_extract_fresh(data: bytes) -> None:
    with pylopdf.open(stream=data) as doc:
        doc[0].get_pixmap(scale=1)
        doc[0].get_text()


def open_only(data: bytes) -> None:
    pylopdf.open(stream=data).close()


def main() -> None:
    corpus_dir = Path(sys.argv[1]).resolve()
    fixture_dir = Path(sys.argv[2]).resolve()
    corpus = {path.name: path.read_bytes() for path in sorted(corpus_dir.glob("*.pdf"))}
    fixtures = {path.name: path.read_bytes() for path in sorted(fixture_dir.glob("*.pdf"))}
    started = time.perf_counter()
    providers = [provider.language for provider in pylopdf._bundled_font_providers()]  # noqa: SLF001
    result: dict[str, object] = {
        "python": platform.python_version(),
        "pylopdf_module": pylopdf.pylopdf_core.__file__,
        "font_providers": providers,
        "repeats": REPEATS,
    }

    for label, func, inputs in (
        ("render_fresh", render_fresh, corpus),
        ("png_fresh", png_fresh, corpus),
        ("extract_fresh", extract_fresh, corpus),
        ("render_extract_fresh", render_then_extract_fresh, corpus),
        ("open_only", open_only, corpus),
        ("cjk_render_fresh", render_fresh, fixtures),
        ("cjk_extract_fresh", extract_fresh, fixtures),
    ):
        per_file = {name: median_ms(lambda data=data, func=func: func(data)) for name, data in inputs.items()}
        result[label] = per_file
        result[f"{label}_total"] = sum(per_file.values())

    def corpus_loop() -> None:
        for data in corpus.values():
            with pylopdf.open(stream=data) as doc:
                doc[0].get_pixmap(scale=1)

    result["corpus_loop_fresh"] = median_ms(corpus_loop)

    usrguide = corpus["usrguide.pdf"]
    for workers in (1, 4):

        def batch(workers: int = workers) -> None:
            with pylopdf.open(stream=usrguide) as doc:
                doc.render_pages(range(min(BATCH_PAGES, doc.page_count)), scale=1, workers=workers)

        result[f"batch_fresh_w{workers}"] = median_ms(batch)

    # Output identity: rasters and extracted text from fresh documents.
    digests: dict[str, list[str]] = {}
    for name, data in {**corpus, **fixtures}.items():
        with pylopdf.open(stream=data) as doc:
            raster = hashlib.sha256(doc[0].get_pixmap(scale=2).samples).hexdigest()[:16]
        with pylopdf.open(stream=data) as doc:
            text = hashlib.sha256(doc.get_text().encode()).hexdigest()[:16]
        with pylopdf.open(stream=data) as doc:
            doc[0].get_pixmap(scale=1)
            text_after_render = hashlib.sha256(doc.get_text().encode()).hexdigest()[:16]
        digests[name] = [raster, text, text_after_render]
    result["digests"] = digests

    memory = {}
    for scenario in ("single_render", "fresh_renders", "open_documents", "extract_only"):
        output = subprocess.run(
            [sys.executable, "-W", "ignore", str(HERE / "memory.py"), str(corpus_dir), str(fixture_dir), scenario],
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
