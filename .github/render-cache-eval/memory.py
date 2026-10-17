"""Report resident memory for one repeated-render scenario in a fresh process.

Usage: python memory.py REPOSITORY_ROOT SCENARIO

Prints JSON with resident set size (MiB) before rendering, the process peak,
and after rendering while the documents are still open.
"""

from __future__ import annotations

import gc
import json
import sys
from pathlib import Path

import psutil

import pylopdf


def _peak_bytes(process: psutil.Process) -> int:
    """Return this process's resident high-water mark.

    Linux keeps ``ru_maxrss`` across ``execve``, so a child spawned by a large
    benchmark parent would report the parent's peak; ``VmHWM`` belongs to the
    new address space instead.
    """
    info = process.memory_info()
    peak = getattr(info, "peak_wset", None)
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
    corpus = Path(sys.argv[1]).resolve() / "tests" / "assets" / "real_world"
    scenario = sys.argv[2]
    process = psutil.Process()
    documents: list[pylopdf.Document] = []
    if scenario == "usrguide_passes":
        documents.append(pylopdf.open(corpus / "usrguide.pdf"))
    elif scenario == "bill_pass":
        documents.append(pylopdf.open(corpus / "bill-hr815.pdf"))
    elif scenario == "open_documents":
        documents.extend(pylopdf.open(path) for path in sorted(corpus.glob("*.pdf")))
    else:
        raise SystemExit(f"unknown scenario: {scenario}")
    gc.collect()
    before = process.memory_info().rss

    if scenario == "usrguide_passes":
        doc = documents[0]
        for _ in range(3):
            for index in range(doc.page_count):
                doc[index].get_pixmap(scale=2)
    elif scenario == "bill_pass":
        doc = documents[0]
        for index in range(doc.page_count):
            doc[index].get_pixmap(scale=1)
    else:
        for doc in documents:
            doc[0].get_pixmap(scale=2)
    gc.collect()
    after = process.memory_info().rss
    mib = 1024 * 1024
    print(
        json.dumps(
            {
                "before_mib": before / mib,
                "peak_mib": _peak_bytes(process) / mib,
                "after_mib": after / mib,
            }
        )
    )


if __name__ == "__main__":
    main()
