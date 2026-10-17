"""Report resident memory for one fresh-document scenario in a new process.

Usage: python memory.py CORPUS_DIR FIXTURE_DIR SCENARIO

Prints JSON with resident set size (MiB) before the scenario, the process
peak, and after it (documents closed, except ``open_documents``, which keeps
every document open).
"""

from __future__ import annotations

import gc
import json
import sys
from pathlib import Path

import psutil

import pylopdf


def peak_bytes(process: psutil.Process) -> int:
    """Return this process's resident high-water mark.

    Linux keeps ``ru_maxrss`` across ``execve``, so a child spawned by a large
    benchmark parent would report the parent's peak; ``VmHWM`` belongs to the
    new address space instead.
    """
    peak = getattr(process.memory_info(), "peak_wset", None)
    if peak is not None:
        return int(peak)
    status = Path("/proc/self/status")
    if status.is_file():
        for line in status.read_text(encoding="ascii").splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
    import resource

    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def main() -> None:
    paths = sorted(Path(sys.argv[1]).glob("*.pdf"))
    corpus = [path.read_bytes() for path in paths]
    smallest = min(paths, key=lambda path: path.stat().st_size).read_bytes()
    fixtures = [path.read_bytes() for path in sorted(Path(sys.argv[2]).glob("*.pdf"))]
    scenario = sys.argv[3]
    process = psutil.Process()
    pylopdf._bundled_font_providers()  # noqa: SLF001 - import the font packages first.
    gc.collect()
    before = process.memory_info().rss
    documents: list[pylopdf.Document] = []
    if scenario == "single_render":
        with pylopdf.open(stream=smallest) as doc:
            doc[0].get_pixmap(scale=2)
    elif scenario == "fresh_renders":
        for _ in range(3):
            for data in corpus + fixtures:
                with pylopdf.open(stream=data) as doc:
                    doc[0].get_pixmap(scale=2)
    elif scenario == "open_documents":
        for data in corpus:
            doc = pylopdf.open(stream=data)
            doc[0].get_pixmap(scale=1)
            documents.append(doc)
    elif scenario == "extract_only":
        for data in corpus + fixtures:
            with pylopdf.open(stream=data) as doc:
                doc.get_text()
    else:
        raise SystemExit(f"unknown scenario: {scenario}")
    gc.collect()
    after = process.memory_info().rss
    mib = 1024 * 1024
    print(json.dumps({"before_mib": before / mib, "peak_mib": peak_bytes(process) / mib, "after_mib": after / mib}))


if __name__ == "__main__":
    main()
