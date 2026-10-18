"""Summarize ab.py output as Markdown.

For every (mode, file) the value of a variant is the median over rounds of its
per-round medians; the delta is relative to BASELINE.

Usage: python summary.py OUT BASELINE VARIANT... [--modes warm,cold,...]
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def main() -> None:
    args = sys.argv[1:]
    modes = None
    if "--modes" in args:
        index = args.index("--modes")
        modes = args[index + 1].split(",")
        del args[index : index + 2]
    out_dir = Path(args[0])
    baseline = args[1]
    variants = args[2:]
    data: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    order: list[tuple[str, str]] = []
    for variant in [baseline, *variants]:
        for line in (out_dir / f"{variant}.jsonl").read_text().splitlines():
            record = json.loads(line)
            key = (record["mode"], record["file"])
            if modes is not None and key[0] not in modes:
                continue
            if key not in data:
                order.append(key)
            data[key][variant].append(record["median_ns"] / 1000)
    print(f"| mode | file | {baseline} µs | " + " | ".join(variants) + " |")
    print("|" + "---|" * (3 + len(variants)))
    for key in order:
        base = statistics.median(data[key][baseline])
        cells = []
        for variant in variants:
            values = data[key].get(variant)
            if not values:
                cells.append("")
                continue
            value = statistics.median(values)
            cells.append(f"{value:.1f} ({100 * (value / base - 1):+.0f}%)")
        print(f"| {key[0]} | {key[1]} | {base:.1f} | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
