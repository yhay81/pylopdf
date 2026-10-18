"""Report resident memory for one text-generation scenario in a new process.

Usage: python gen_memory.py SCENARIO

Prints JSON with resident set size (MiB) before the scenario, the process
peak, and after it. Every document is released inside a helper function
before the final measurement.
"""

from __future__ import annotations

import gc
import json
import sys
from pathlib import Path

import psutil

import pylopdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_ab import AR, FORM_4, JA, ZH, blank  # noqa: E402


def peak_bytes(process: psutil.Process) -> int:
    """Return this process's resident high-water mark.

    Linux keeps ``ru_maxrss`` across ``execve``, so read ``VmHWM`` instead.
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


def fresh_texts(count: int, text: str, **options: object) -> None:
    for _ in range(count):
        doc = blank()
        doc[0].insert_text((50, 100), text, fontsize=14, **options)  # type: ignore[arg-type]
        doc.tobytes()
        doc.close()


def mixed_reused() -> None:
    doc = blank()
    page = doc[0]
    for index in range(20):
        y = 40 + 36 * index
        page.insert_text((50, y), JA, fontsize=12)
        page.insert_text((220, y), ZH, fontsize=12, fontname="tiro", font_language="zh-CN")
        page.insert_text((400, y), AR, fontsize=12)
    doc.tobytes()
    doc.close()


def form_fills(count: int) -> None:
    for index in range(count):
        doc = pylopdf.open(stream=FORM_4)
        doc.set_form_field("customer", "山田 太郎" if index % 2 else "佐藤 花子")
        doc.tobytes()
        doc.close()


SCENARIOS = {
    "ja_fresh_texts_x30": lambda: fresh_texts(30, JA),
    "zh_serif_fresh_texts_x30": lambda: fresh_texts(30, ZH, fontname="tiro", font_language="zh-CN"),
    "mixed_scripts_reused_x60": mixed_reused,
    "ja_form_4_widgets_x20": lambda: form_fills(20),
    "helv_fresh_texts_x30": lambda: fresh_texts(30, "Latin text"),
}


def main() -> None:
    scenario = sys.argv[1]
    process = psutil.Process()
    pylopdf._bundled_font_providers()  # noqa: SLF001 - import the font packages first.
    gc.collect()
    before = process.memory_info().rss
    SCENARIOS[scenario]()
    gc.collect()
    after = process.memory_info().rss
    mib = 1024 * 1024
    print(json.dumps({"before_mib": before / mib, "peak_mib": peak_bytes(process) / mib, "after_mib": after / mib}))


if __name__ == "__main__":
    main()
